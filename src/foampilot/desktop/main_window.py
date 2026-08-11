"""Interactive PySide6 workbench for canonical FoamPilot jobs and runs."""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path

import yaml

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSettings,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from foampilot.taskbuilder import TaskDraft, validate_task_draft
from foampilot.jobs import JobStatus, RecoveryAction

from .job_controller import DesktopJobController, DesktopJobError
from .repository import (
    RunCollectionError,
    RunOpenError,
    RunRepository,
)
from .residual_plot import ResidualPlot
from .viewmodels import RunSnapshot
from .workspace import (
    DesktopWorkspace,
    DesktopWorkspaceError,
    confirm_task_draft,
)


_CATEGORY_LABELS = {
    "case": "Case",
    "log": "Logs",
    "report": "Reports",
    "workflow": "Workflow",
    "other": "Other",
}


def _text(value: object | None) -> str:
    if value is None:
        return "not available"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _summary_json(snapshot: RunSnapshot) -> str:
    if snapshot.summary is None:
        return "Run 尚未生成 summary.json；当前状态来自 workflow 公开事件。"
    return json.dumps(
        snapshot.summary.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _yaml_value(value: object) -> str:
    rendered = yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=True,
        sort_keys=False,
    ).strip()
    return rendered.removesuffix("\n...")


class _RunLoadSignals(QObject):
    succeeded = Signal(int, object, object)
    failed = Signal(int, object, object)


class _RunLoadTask(QRunnable):
    def __init__(
        self,
        generation: int,
        repository: RunRepository,
        run_dir: Path,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.repository = repository
        self.run_dir = run_dir
        self.signals = _RunLoadSignals()

    @Slot()
    def run(self) -> None:
        try:
            snapshot = self.repository.open(self.run_dir)
        except Exception as error:
            self.signals.failed.emit(self.generation, self.run_dir, error)
            return
        self.signals.succeeded.emit(
            self.generation,
            self.run_dir,
            snapshot,
        )


class FoamPilotMainWindow(QMainWindow):
    """Create canonical jobs and display immutable public run projections."""

    def __init__(
        self,
        *,
        repository: RunRepository | None = None,
        settings: QSettings | None = None,
        job_controller: DesktopJobController | None = None,
        runtime_cli_args: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.repository = repository or RunRepository()
        self.settings = settings
        self.job_controller = job_controller or DesktopJobController(self)
        self.runtime_cli_args = tuple(str(item) for item in runtime_cli_args)
        self.workspace: DesktopWorkspace | None = None
        self.current_snapshot: RunSnapshot | None = None
        self.recovery_warning = ""
        self._job_purpose: str | None = None
        self._expected_draft_path: Path | None = None
        self._compile_source_path: Path | None = None
        self._expected_task_path: Path | None = None
        self._solve_task_path: Path | None = None
        self._solve_run_root: Path | None = None
        self._draft_can_compile = False
        self._draft_needs_confirmation = False
        self._recovery_decision = None
        self._question_editors: dict[str, QLineEdit] = {}
        self._refresh_pool = QThreadPool.globalInstance()
        self._refresh_generation = 0
        self._refresh_inflight = False
        self._refresh_pending = False
        self._refresh_target: Path | None = None
        self._refresh_interactive = False
        self._closing = False
        self.live_refresh_timer = QTimer(self)
        self.live_refresh_timer.setInterval(1000)
        self.live_refresh_timer.timeout.connect(self.refresh_run)
        self.setWindowTitle("FoamPilot Interactive IDE")
        self.resize(1440, 900)
        self._build_interface()
        self._connect_job_controller()
        self._render_empty_state()
        self._restore_window_layout()

    def _connect_job_controller(self) -> None:
        self.job_controller.job_started.connect(self._on_job_started)
        self.job_controller.output_received.connect(self._on_job_output)
        self.job_controller.run_discovered.connect(self._on_run_discovered)
        self.job_controller.job_finished.connect(self._on_job_finished)
        self.job_controller.job_error.connect(self._on_job_error)
        self.job_controller.activity_received.connect(self._on_activity)
        self.job_controller.job_status_changed.connect(self._on_job_status)
        self.job_controller.job_health_changed.connect(self._on_job_health)
        if hasattr(self.job_controller, "recovery_decision_changed"):
            self.job_controller.recovery_decision_changed.connect(
                self._on_recovery_decision
            )

    def _build_interface(self) -> None:
        toolbar = QToolBar("FoamPilot")
        toolbar.setObjectName("run_toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.workspace_action = QAction("工程目录", self)
        self.workspace_action.triggered.connect(self.choose_workspace)
        toolbar.addAction(self.workspace_action)
        self.environment_action = QAction("环境检查", self)
        self.environment_action.triggered.connect(self.check_environment)
        toolbar.addAction(self.environment_action)
        toolbar.addSeparator()
        self.generate_action = QAction("生成草稿", self)
        self.generate_action.triggered.connect(self.generate_draft)
        toolbar.addAction(self.generate_action)
        self.compile_action = QAction("确认并编译", self)
        self.compile_action.triggered.connect(self.compile_draft)
        toolbar.addAction(self.compile_action)
        self.solve_action = QAction("开始求解", self)
        self.solve_action.triggered.connect(self.start_solve)
        toolbar.addAction(self.solve_action)
        self.cancel_action = QAction("取消任务", self)
        self.cancel_action.setToolTip(
            "OpenFOAM/Codex CLI 会终止受控进程组；非流式 HTTP 模型请求需等待响应或超时"
        )
        self.cancel_action.triggered.connect(self.cancel_job)
        toolbar.addAction(self.cancel_action)
        self.terminate_orphan_action = QAction("终止孤儿进程", self)
        self.terminate_orphan_action.triggered.connect(
            self.terminate_orphan_job
        )
        toolbar.addAction(self.terminate_orphan_action)
        self.recover_finalize_action = QAction("固化中断", self)
        self.recover_finalize_action.triggered.connect(
            self.recover_finalize_job
        )
        toolbar.addAction(self.recover_finalize_action)
        self.resume_action = QAction("恢复模型阶段", self)
        self.resume_action.triggered.connect(self.start_strict_resume)
        toolbar.addAction(self.resume_action)
        self.rerun_action = QAction("完整重跑", self)
        self.rerun_action.triggered.connect(self.start_rerun)
        toolbar.addAction(self.rerun_action)
        toolbar.addSeparator()
        self.open_action = QAction("打开 Run", self)
        self.open_action.triggered.connect(self.choose_run)
        toolbar.addAction(self.open_action)
        self.refresh_action = QAction("刷新", self)
        self.refresh_action.triggered.connect(self.refresh_run)
        toolbar.addAction(self.refresh_action)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("workspace_tabs")
        self.task_page = self._build_task_page()
        self.context_page = self._build_context_page()
        self.residual_page = self._build_residual_page()
        self.artifact_page = self._build_artifact_page()
        self.workspace_tabs.addTab(self.task_page, "任务")
        self.workspace_tabs.addTab(self.context_page, "知识上下文")
        self.workspace_tabs.addTab(self.residual_page, "残差监控")
        self.workspace_tabs.addTab(self.artifact_page, "产物")
        self.setCentralWidget(self.workspace_tabs)

        self._build_file_dock()
        self._build_details_dock()
        self._build_bottom_dock()

        self.status_label = QLabel("not available")
        self.status_label.setObjectName("status_label")
        self.statusBar().addPermanentWidget(self.status_label)

    def _build_task_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(QLabel("工程目录"))
        self.workspace_line = QLineEdit()
        self.workspace_line.setObjectName("workspace_line")
        self.workspace_line.setReadOnly(True)
        self.workspace_line.setPlaceholderText("请选择一个 FoamPilot 工程目录")
        workspace_row.addWidget(self.workspace_line, 1)
        self.workspace_button = QPushButton("选择…")
        self.workspace_button.clicked.connect(self.choose_workspace)
        workspace_row.addWidget(self.workspace_button)
        layout.addLayout(workspace_row)

        self.recovery_hint_label = QLabel(
            "OpenFOAM continuation：当前不支持从任意时间目录断点续算。"
        )
        self.recovery_hint_label.setWordWrap(True)
        layout.addWidget(self.recovery_hint_label)

        layout.addWidget(QLabel("自然语言任务描述"))
        self.request_editor = QPlainTextEdit()
        self.request_editor.setObjectName("request_editor")
        self.request_editor.setPlaceholderText(
            "描述几何、单位、流体/材料、边界条件、稳态/瞬态、结束条件和期望输出。"
        )
        self.request_editor.setMaximumHeight(150)
        self.request_editor.textChanged.connect(self._update_action_states)
        layout.addWidget(self.request_editor)

        buttons = QHBoxLayout()
        self.generate_draft_button = QPushButton("1. 生成 TaskDraft")
        self.generate_draft_button.clicked.connect(self.generate_draft)
        buttons.addWidget(self.generate_draft_button)
        self.confirm_draft_button = QPushButton("2. 应用回答并确认")
        self.confirm_draft_button.clicked.connect(self.confirm_draft)
        buttons.addWidget(self.confirm_draft_button)
        self.compile_draft_button = QPushButton("3. 验证并编译 TaskSpec")
        self.compile_draft_button.clicked.connect(self.compile_draft)
        buttons.addWidget(self.compile_draft_button)
        self.solve_button = QPushButton("4. 开始规范求解")
        self.solve_button.clicked.connect(self.start_solve)
        buttons.addWidget(self.solve_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.draft_tree = QTreeWidget()
        self.draft_tree.setObjectName("draft_tree")
        self.draft_tree.setHeaderLabels(
            ["类型", "字段/问题", "来源", "影响", "值/回答", "状态"]
        )
        self.draft_tree.setAlternatingRowColors(True)
        splitter.addWidget(self.draft_tree)

        editors = QTabWidget()
        self.draft_editor = QPlainTextEdit()
        self.draft_editor.setObjectName("draft_editor")
        self.draft_editor.setPlaceholderText("生成的 TaskDraft YAML 将显示在这里。")
        self.task_editor = QPlainTextEdit()
        self.task_editor.setObjectName("task_editor")
        self.task_editor.setPlaceholderText(
            "编译后的 TaskSpec YAML；高级用户也可以直接在这里粘贴 TaskSpec。"
        )
        self.task_editor.textChanged.connect(self._update_action_states)
        self.diagnostics_viewer = self._read_only_editor("diagnostics_viewer")
        editors.addTab(self.draft_editor, "TaskDraft YAML（高级）")
        editors.addTab(self.task_editor, "TaskSpec YAML（高级）")
        editors.addTab(self.diagnostics_viewer, "诊断")
        splitter.addWidget(editors)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        return page

    def _build_context_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        boundary = QLabel(
            "仅显示模型实际收到的公开 Knowledge/Skill 引用；不展示隐藏思维过程。"
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        self.capability_viewer = self._read_only_editor("capability_viewer")
        self.capability_viewer.setMaximumHeight(150)
        self.capability_viewer.setPlaceholderText("等待 capability-profile.json")
        layout.addWidget(self.capability_viewer)
        self.knowledge_tree = QTreeWidget()
        self.knowledge_tree.setObjectName("knowledge_tree")
        self.knowledge_tree.setHeaderLabels(
            ["阶段", "Attempt", "Slot", "Knowledge ID", "标题", "类型", "来源", "SHA256"]
        )
        self.knowledge_tree.setAlternatingRowColors(True)
        layout.addWidget(self.knowledge_tree, 2)
        self.skill_tree = QTreeWidget()
        self.skill_tree.setObjectName("skill_tree")
        self.skill_tree.setHeaderLabels(["阶段", "Attempt", "Skill"])
        self.skill_tree.setAlternatingRowColors(True)
        layout.addWidget(self.skill_tree, 1)
        return page

    def _build_residual_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(
            "曲线显示 solver log 中 initial residual 的 log10 趋势；正常 End 不自动等于收敛。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.residual_plot = ResidualPlot()
        layout.addWidget(self.residual_plot, 2)
        self.residual_table = QTreeWidget()
        self.residual_table.setObjectName("residual_table")
        self.residual_table.setHeaderLabels(
            ["Field", "Initial", "Final", "Linear iters", "Time/Iteration", "Attempt", "Log"]
        )
        self.residual_table.setAlternatingRowColors(True)
        layout.addWidget(self.residual_table, 1)
        return page

    def _build_artifact_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.central_tabs = QTabWidget()
        self.central_tabs.setObjectName("central_tabs")
        self.overview_viewer = self._read_only_editor("overview_viewer")
        self.file_viewer = self._read_only_editor("file_viewer")
        self.report_viewer = self._read_only_editor("report_viewer")
        self.central_tabs.addTab(self.overview_viewer, "概览")
        self.central_tabs.addTab(self.file_viewer, "文件")
        self.central_tabs.addTab(self.report_viewer, "验证/报告")
        layout.addWidget(self.central_tabs)
        return page

    def _build_file_dock(self) -> None:
        self.file_tree = QTreeWidget()
        self.file_tree.setObjectName("file_tree")
        self.file_tree.setHeaderLabels(["Run 文件", "大小"])
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.currentItemChanged.connect(self._show_selected_file)
        files_dock = QDockWidget("工程 / Run 文件", self)
        files_dock.setObjectName("files_dock")
        files_dock.setWidget(self.file_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, files_dock)

    def _build_details_dock(self) -> None:
        details = QWidget()
        details_layout = QVBoxLayout(details)
        self.job_status_label = QLabel()
        self.current_stage_label = QLabel()
        self.task_id_label = QLabel()
        self.workflow_label = QLabel()
        self.native_label = QLabel()
        self.qualification_label = QLabel()
        self.attempts_label = QLabel()
        self.primary_failure_label = QLabel()
        self.terminal_blocker_label = QLabel()
        self.manifest_label = QLabel()
        self.config_source_label = QLabel()
        self.openfoam_runtime_label = QLabel()
        self.isolation_label = QLabel()
        self.actual_backend_label = QLabel()
        self.risk_label = QLabel()
        self.sandbox_probe_label = QLabel()
        self.fallback_warning_label = QLabel()
        for label in (
            self.job_status_label,
            self.current_stage_label,
            self.task_id_label,
            self.workflow_label,
            self.native_label,
            self.qualification_label,
            self.attempts_label,
            self.primary_failure_label,
            self.terminal_blocker_label,
            self.manifest_label,
            self.config_source_label,
            self.openfoam_runtime_label,
            self.isolation_label,
            self.actual_backend_label,
            self.risk_label,
            self.sandbox_probe_label,
            self.fallback_warning_label,
        ):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            label.setWordWrap(True)
            details_layout.addWidget(label)
        details_layout.addStretch(1)
        details_dock = QDockWidget("任务与 Run 状态", self)
        details_dock.setObjectName("details_dock")
        details_dock.setWidget(details)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, details_dock)

    def _build_bottom_dock(self) -> None:
        self.bottom_tabs = QTabWidget()
        workflow_panel = QWidget()
        workflow_layout = QVBoxLayout(workflow_panel)
        self.timeline_tree = QTreeWidget()
        self.timeline_tree.setObjectName("timeline_tree")
        self.timeline_tree.setHeaderLabels(
            ["#", "阶段", "状态", "Attempt", "Step", "说明"]
        )
        self.timeline_tree.setAlternatingRowColors(True)
        self.log_viewer = self._read_only_editor("log_viewer")
        self.log_viewer.setPlaceholderText("选择日志文件后在此显示内容。")
        workflow_layout.addWidget(self.timeline_tree, 2)
        workflow_layout.addWidget(self.log_viewer, 1)
        self.process_log_viewer = self._read_only_editor("process_log_viewer")
        self.process_log_viewer.setPlaceholderText(
            "TaskBuilder 与 foampilot solve 的 stdout/stderr 将显示在这里。"
        )
        self.bottom_tabs.addTab(workflow_panel, "Workflow / OpenFOAM 日志")
        self.bottom_tabs.addTab(self.process_log_viewer, "任务 / 进程日志")
        timeline_dock = QDockWidget("实时运行", self)
        timeline_dock.setObjectName("timeline_dock")
        timeline_dock.setWidget(self.bottom_tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, timeline_dock)

    @staticmethod
    def _read_only_editor(object_name: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setObjectName(object_name)
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return editor

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 FoamPilot 工程目录",
            str(Path.cwd()),
        )
        if selected:
            self.set_workspace(Path(selected))

    def set_workspace(self, path: str | Path) -> None:
        try:
            workspace = DesktopWorkspace.open(path)
        except (OSError, DesktopWorkspaceError) as error:
            QMessageBox.critical(
                self,
                "无法打开工程目录",
                f"DESKTOP_WORKSPACE_INVALID\n{error}",
            )
            return
        self.workspace = workspace
        self.workspace_line.setText(str(workspace.root))
        self.statusBar().showMessage(f"工程目录：{workspace.root}", 5000)
        try:
            self.job_controller.attach_latest(workspace.runs_dir)
        except DesktopJobError as error:
            self._desktop_error("DESKTOP_ATTACH_FAILED", str(error))
        self._update_action_states()

    def choose_run(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "打开 FoamPilot Run",
            str(self.workspace.root if self.workspace is not None else Path.cwd()),
        )
        if selected:
            self.open_run(Path(selected))

    def open_run(self, run_dir: Path) -> None:
        """Schedule one run projection without blocking the Qt GUI thread."""

        self._schedule_run_load(Path(run_dir), interactive=True)

    def _schedule_run_load(
        self,
        run_dir: Path,
        *,
        interactive: bool,
    ) -> None:
        self._refresh_generation += 1
        self._refresh_target = Path(run_dir)
        self._refresh_interactive = interactive
        if self._refresh_inflight:
            self._refresh_pending = True
            return
        self._start_scheduled_run_load()

    def _start_scheduled_run_load(self) -> None:
        if self._refresh_target is None or self._closing:
            return
        generation = self._refresh_generation
        task = _RunLoadTask(
            generation,
            self.repository,
            self._refresh_target,
        )
        task.signals.succeeded.connect(self._on_run_load_succeeded)
        task.signals.failed.connect(self._on_run_load_failed)
        self._refresh_inflight = True
        self._refresh_pool.start(task)

    def _finish_run_load(self) -> None:
        self._refresh_inflight = False
        if self._refresh_pending and not self._closing:
            self._refresh_pending = False
            self._start_scheduled_run_load()

    @Slot(int, object, object)
    def _on_run_load_failed(
        self,
        generation: int,
        run_dir: Path,
        error: Exception,
    ) -> None:
        current = generation == self._refresh_generation
        interactive = self._refresh_interactive
        if current and not self._closing and isinstance(error, RunCollectionError):
            names = [child.name for child in error.children]
            selected, accepted = QInputDialog.getItem(
                self,
                "请选择具体 Run",
                "该目录包含多个 run，请选择一个具体子目录：",
                names,
                max(0, len(names) - 1),
                False,
            )
            if accepted and selected:
                child = next(
                    item for item in error.children if item.name == selected
                )
                self.open_run(child)
            else:
                self.statusBar().showMessage(
                    "RUN_COLLECTION_SELECTED：请选择具体的 run-* 子目录。",
                    8000,
                )
        elif current and not self._closing and interactive:
            QMessageBox.critical(
                self,
                "无法打开 Run",
                "RUN_OPEN_FAILED\n"
                "无法打开 FoamPilot Run。\n"
                f"路径：{run_dir}\n"
                f"原因：{error}",
            )
        elif current and not self._closing:
            self.recovery_warning = f"DESKTOP_REFRESH_DEGRADED: {error}"
            self.statusBar().showMessage(self.recovery_warning, 10000)
        self._finish_run_load()

    @Slot(int, object, object)
    def _on_run_load_succeeded(
        self,
        generation: int,
        run_dir: Path,
        snapshot: RunSnapshot,
    ) -> None:
        del run_dir
        if generation != self._refresh_generation or self._closing:
            self._finish_run_load()
            return
        self.current_snapshot = snapshot
        self.recovery_warning = ""
        if self.settings is not None:
            self.settings.setValue("desktop/last_run", str(snapshot.run_dir))
            self.settings.sync()
        self._render_snapshot(snapshot)
        self._finish_run_load()

    def refresh_run(self) -> None:
        if self.current_snapshot is not None:
            self._schedule_run_load(
                self.current_snapshot.run_dir,
                interactive=False,
            )

    def restore_last_run(self) -> None:
        if self.settings is None:
            return
        stored = self.settings.value("desktop/last_run")
        if not stored:
            return
        run_dir = Path(str(stored))
        if not run_dir.is_dir():
            self.recovery_warning = f"最近 Run 不存在：{run_dir}"
            self.statusBar().showMessage(self.recovery_warning)
            return
        self.open_run(run_dir)

    def _restore_window_layout(self) -> None:
        if self.settings is None:
            return
        geometry = self.settings.value("desktop/window_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = self.settings.value("desktop/window_state")
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.live_refresh_timer.stop()
        if self.settings is not None:
            self.settings.setValue("desktop/window_geometry", self.saveGeometry())
            self.settings.setValue("desktop/window_state", self.saveState())
            self.settings.sync()
        super().closeEvent(event)

    def cancel_job(self) -> None:
        try:
            self.job_controller.request_cancel()
        except DesktopJobError as error:
            self._desktop_error("DESKTOP_CANCEL_FAILED", str(error))
            return
        self.job_status_label.setText("IDE Job: CANCEL_REQUESTED")
        self.statusBar().showMessage(
            "已请求取消；进程型任务正在退出，HTTP 模型请求可能需等待响应或超时。",
            8000,
        )

    def terminate_orphan_job(self) -> None:
        try:
            decision = self.job_controller.request_terminate_orphan()
        except DesktopJobError as error:
            self._desktop_error("DESKTOP_ORPHAN_TERMINATION_FAILED", str(error))
            return
        self._on_recovery_decision(decision)

    def recover_finalize_job(self) -> None:
        try:
            decision = self.job_controller.request_recover_finalize()
        except DesktopJobError as error:
            self._desktop_error("DESKTOP_RECOVER_FINALIZE_FAILED", str(error))
            return
        self._on_recovery_decision(decision)
        if decision.run_dir is not None:
            self.open_run(decision.run_dir)

    def _new_recovery_job_root(self) -> Path | None:
        if self.workspace is None:
            self._desktop_error(
                "DESKTOP_WORKSPACE_INVALID",
                "恢复或重跑前必须先打开包含 parent run 的工程目录。",
            )
            return None
        try:
            return self.workspace.create_job_root()
        except DesktopWorkspaceError as error:
            self._desktop_error("DESKTOP_WORKSPACE_INVALID", str(error))
            return None

    def start_strict_resume(self) -> None:
        snapshot = self.current_snapshot
        if snapshot is None or snapshot.summary is None:
            self._desktop_error(
                "STRICT_RESUME_INELIGIBLE",
                "当前没有可验证的 parent run summary。",
            )
            return
        stage = snapshot.summary.resume.from_stage
        if not snapshot.summary.resume.allowed or stage is None:
            self._desktop_error(
                "STRICT_RESUME_INELIGIBLE",
                snapshot.summary.resume.reason,
            )
            return
        job_root = self._new_recovery_job_root()
        if job_root is None:
            return
        self._solve_run_root = job_root
        self._start_cli(
            "resume",
            [
                "resume",
                str(snapshot.run_dir),
                "--run-root",
                str(job_root),
                "--backend",
                "auto",
                "--progress",
                "jsonl",
                "--json",
            ],
            run_root=job_root,
        )

    def start_rerun(self) -> None:
        snapshot = self.current_snapshot
        if snapshot is None or snapshot.manifest_state != "verified":
            self._desktop_error(
                "RERUN_PARENT_INVALID",
                "完整重跑需要 manifest 有效的 parent run。",
            )
            return
        job_root = self._new_recovery_job_root()
        if job_root is None:
            return
        self._solve_run_root = job_root
        self._start_cli(
            "rerun",
            [
                "rerun",
                str(snapshot.run_dir),
                "--run-root",
                str(job_root),
                "--backend",
                "auto",
                "--progress",
                "jsonl",
                "--json",
            ],
            run_root=job_root,
        )

    def check_environment(self) -> None:
        self._start_cli("environment_preflight", ["preflight", "--json"])

    def generate_draft(self) -> None:
        if self.workspace is None:
            self._desktop_error(
                "DESKTOP_WORKSPACE_INVALID", "请先选择工程目录。"
            )
            return
        try:
            request_path = self.workspace.save_request(
                self.request_editor.toPlainText()
            )
            output_path = self.workspace.reserve_draft_path()
            job_root = self.workspace.create_job_root()
        except DesktopWorkspaceError as error:
            self._desktop_error("DESKTOP_WORKSPACE_INVALID", str(error))
            return
        self._expected_draft_path = output_path
        self._start_cli(
            "draft",
            [
                "task",
                "draft",
                "--request-file",
                str(request_path),
                "--output",
                str(output_path),
                "--backend",
                "auto",
                "--progress",
                "jsonl",
                "--json",
            ],
            run_root=job_root,
        )

    def load_draft_text(self, text: str) -> None:
        self.draft_editor.setPlainText(text)
        self.draft_tree.clear()
        self._question_editors.clear()
        self._draft_can_compile = False
        self._draft_needs_confirmation = False
        try:
            payload = yaml.safe_load(text)
            if not isinstance(payload, dict):
                raise ValueError("TaskDraft root must be a mapping")
            draft = TaskDraft.model_validate(payload)
            review = validate_task_draft(draft)
        except (ValueError, yaml.YAMLError) as error:
            self.diagnostics_viewer.setPlainText(
                f"TASK_DRAFT_INVALID: {error}"
            )
            self._update_action_states()
            return

        for fact in draft.facts:
            item = QTreeWidgetItem(
                [
                    "事实",
                    fact.path,
                    fact.source.value,
                    fact.impact,
                    _yaml_value(fact.value),
                    "已确认" if fact.confirmed else "待确认",
                ]
            )
            self.draft_tree.addTopLevelItem(item)
        for assumption in draft.assumptions:
            self.draft_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        "假设",
                        assumption.path,
                        assumption.source,
                        assumption.impact,
                        _yaml_value(assumption.value),
                        "可见默认" if assumption.impact == "low" else "待确认",
                    ]
                )
            )
        for question in draft.unresolved_questions:
            item = QTreeWidgetItem(
                [
                    "问题",
                    question.path,
                    question.question_id,
                    question.kind,
                    "",
                    "待回答",
                ]
            )
            item.setToolTip(1, f"{question.prompt_zh}\n{question.reason_zh}")
            self.draft_tree.addTopLevelItem(item)
            editor = QLineEdit()
            editor.setPlaceholderText(question.prompt_zh)
            if question.candidate is not None:
                editor.setText(_yaml_value(question.candidate))
            self.draft_tree.setItemWidget(item, 4, editor)
            self._question_editors[question.question_id] = editor
        for column in range(self.draft_tree.columnCount()):
            self.draft_tree.resizeColumnToContents(column)
        self._draft_can_compile = review.can_compile
        self._draft_needs_confirmation = bool(
            draft.unresolved_questions
            or any(
                not fact.confirmed and fact.impact in {"medium", "high"}
                for fact in draft.facts
            )
            or any(
                assumption.source == "model_inference"
                and assumption.impact in {"medium", "high"}
                for assumption in draft.assumptions
            )
        )
        self.diagnostics_viewer.setPlainText(
            json.dumps(
                {
                    "status": draft.status.value,
                    "can_compile": review.can_compile,
                    "issues": [
                        issue.model_dump(mode="json") for issue in review.issues
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        self._update_action_states()

    def confirm_draft(self) -> None:
        answers = {
            question_id: editor.text()
            for question_id, editor in self._question_editors.items()
        }
        try:
            confirmed = confirm_task_draft(
                self.draft_editor.toPlainText(), answers
            )
        except DesktopWorkspaceError as error:
            self._desktop_error("TASK_CONFIRMATION_FAILED", str(error))
            return
        self.load_draft_text(confirmed)

    def compile_draft(self) -> None:
        if self.workspace is None:
            self._desktop_error(
                "DESKTOP_WORKSPACE_INVALID", "请先选择工程目录。"
            )
            return
        if not self._draft_can_compile:
            self._desktop_error(
                "TASK_CONFIRMATION_REQUIRED",
                "请先回答问题、确认 TaskDraft，并处理阻断诊断。",
            )
            return
        try:
            self._compile_source_path = self.workspace.save_draft(
                self.draft_editor.toPlainText()
            )
            self._expected_task_path = self.workspace.reserve_task_path()
        except DesktopWorkspaceError as error:
            self._desktop_error("TASK_DRAFT_INVALID", str(error))
            return
        self._start_cli(
            "validate_draft",
            [
                "task",
                "validate-draft",
                str(self._compile_source_path),
                "--json",
            ],
        )

    def start_solve(self) -> None:
        if self.workspace is None:
            self._desktop_error(
                "DESKTOP_WORKSPACE_INVALID", "请先选择工程目录。"
            )
            return
        try:
            self._solve_task_path = self.workspace.save_task(
                self.task_editor.toPlainText()
            )
            self._solve_run_root = self.workspace.create_job_root()
        except DesktopWorkspaceError as error:
            self._desktop_error("TASKSPEC_INVALID", str(error))
            return
        self._start_cli(
            "validate_task",
            ["validate", str(self._solve_task_path), "--json"],
        )

    def _start_cli(
        self,
        purpose: str,
        arguments: list[str],
        *,
        run_root: Path | None = None,
    ) -> None:
        effective_arguments = list(arguments)
        if (
            effective_arguments
            and effective_arguments[0]
            in {"preflight", "solve", "resume", "rerun"}
            and self.runtime_cli_args
        ):
            insertion = (
                effective_arguments.index("--json")
                if "--json" in effective_arguments
                else len(effective_arguments)
            )
            effective_arguments[insertion:insertion] = self.runtime_cli_args
        self._job_purpose = purpose
        self.process_log_viewer.appendPlainText(
            f"\n[{purpose}] foampilot " + " ".join(effective_arguments)
        )
        self.bottom_tabs.setCurrentWidget(self.process_log_viewer)
        try:
            self.job_controller.start_cli(
                effective_arguments,
                run_root=run_root,
                project_root=(
                    self.workspace.root
                    if run_root is not None and self.workspace is not None
                    else None
                ),
            )
        except DesktopJobError as error:
            self._job_purpose = None
            self._desktop_error("DESKTOP_PROCESS_FAILED", str(error))
        self._update_action_states()

    def _on_job_started(self, command: str) -> None:
        # A newly submitted/bound job must not inherit actions from the
        # previously selected terminal job. Startup reconciliation will emit
        # a fresh decision after attachment when one exists.
        self._recovery_decision = None
        if self._job_purpose is None and command in {
            "draft",
            "plan",
            "solve",
            "resume",
            "rerun",
        }:
            self._job_purpose = command
            arguments = self.job_controller.current_arguments
            if command == "draft" and "--output" in arguments:
                self._expected_draft_path = Path(
                    arguments[arguments.index("--output") + 1]
                )
            elif command == "solve" and len(arguments) > 1:
                self._solve_task_path = Path(arguments[1])
                if "--run-root" in arguments:
                    self._solve_run_root = Path(
                        arguments[arguments.index("--run-root") + 1]
                    )
        self.job_status_label.setText(f"IDE Job: running ({command})")
        self._update_action_states()

    def _on_job_output(self, channel: str, text: str) -> None:
        if text:
            self.process_log_viewer.appendPlainText(
                f"[{channel}] {text.rstrip()}"
            )

    def _on_activity(self, event) -> None:
        stage = event.stage or event.source.value
        self.current_stage_label.setText(
            f"Current activity: {stage} / {event.state.value}"
        )

    def _on_job_status(self, status: JobStatus) -> None:
        suffix = (
            f" · {status.current_stage}"
            if status.current_stage is not None
            else ""
        )
        self.job_status_label.setText(
            f"IDE Job: {status.state.value}{suffix}"
        )
        if status.run_dir is not None and self.job_controller.is_running:
            self.live_refresh_timer.start()
        self._update_action_states()

    def _on_job_health(self, health: str) -> None:
        if health == "UNRESPONSIVE":
            self.job_status_label.setText("IDE Job: UNRESPONSIVE")
            self.statusBar().showMessage(
                "后台 worker 心跳已过期；可检查日志或请求取消。",
                8000,
            )

    def _on_recovery_decision(self, decision) -> None:
        self._recovery_decision = decision
        self.recovery_hint_label.setText(
            f"{decision.code}: {decision.reason_zh} {decision.recovery_zh}\n"
            "OpenFOAM continuation：当前不支持从任意时间目录断点续算。"
        )
        self.job_status_label.setText(
            f"IDE Recovery: {decision.state.value}"
        )
        self._update_action_states()

    def _on_run_discovered(self, run_dir: Path) -> None:
        self.open_run(Path(run_dir))
        if self.job_controller.is_running:
            self.live_refresh_timer.start()
        self.workspace_tabs.setCurrentWidget(self.residual_page)

    def _on_job_finished(self, exit_code: int, exit_status: str) -> None:
        purpose = self._job_purpose
        self._job_purpose = None
        self.job_status_label.setText(
            f"IDE Job: exited {exit_code} ({exit_status})"
        )
        self.process_log_viewer.appendPlainText(
            f"[{purpose or 'job'}] exit={exit_code} status={exit_status}"
        )
        if exit_status == "cancelled":
            self.live_refresh_timer.stop()
            self.job_status_label.setText("IDE Job: CANCELLED")
            self.refresh_run()
            self._update_action_states()
            return
        if purpose == "environment_preflight":
            if exit_code == 0:
                self._start_cli(
                    "environment_model", ["model", "doctor", "--json"]
                )
                return
        elif purpose == "draft":
            if self._expected_draft_path is not None and self._expected_draft_path.is_file():
                self.load_draft_text(
                    self._expected_draft_path.read_text(encoding="utf-8")
                )
                self.workspace_tabs.setCurrentWidget(self.task_page)
            else:
                self._desktop_error(
                    "DESKTOP_PROCESS_FAILED",
                    "TaskBuilder 未生成预期的 TaskDraft 文件。",
                )
        elif purpose == "validate_draft" and exit_code == 0:
            if self._compile_source_path is None or self._expected_task_path is None:
                self._desktop_error(
                    "DESKTOP_PROCESS_FAILED", "编译状态缺少输入或输出路径。"
                )
            else:
                self._start_cli(
                    "compile",
                    [
                        "task",
                        "compile",
                        str(self._compile_source_path),
                        "--output",
                        str(self._expected_task_path),
                        "--json",
                    ],
                )
                return
        elif purpose == "compile":
            if (
                exit_code == 0
                and self._expected_task_path is not None
                and self._expected_task_path.is_file()
            ):
                self.task_editor.setPlainText(
                    self._expected_task_path.read_text(encoding="utf-8")
                )
            elif exit_code == 0:
                self._desktop_error(
                    "DESKTOP_PROCESS_FAILED",
                    "TaskCompiler 未生成预期的 TaskSpec 文件。",
                )
        elif purpose == "validate_task" and exit_code == 0:
            if self._solve_task_path is None or self._solve_run_root is None:
                self._desktop_error(
                    "DESKTOP_PROCESS_FAILED", "求解状态缺少 TaskSpec 或 run root。"
                )
            else:
                self._start_cli(
                    "solve",
                    [
                        "solve",
                        str(self._solve_task_path),
                        "--run-root",
                        str(self._solve_run_root),
                        "--public-asset-root",
                        str(self.workspace.root),
                        "--backend",
                        "auto",
                        "--progress",
                        "jsonl",
                        "--json",
                    ],
                    run_root=self._solve_run_root,
                )
                return
        elif purpose in {"solve", "resume", "rerun"}:
            self.live_refresh_timer.stop()
            self.refresh_run()
        if exit_code != 0 and purpose not in {
            "draft",
            "solve",
            "resume",
            "rerun",
        }:
            self.diagnostics_viewer.setPlainText(
                f"DESKTOP_PROCESS_FAILED: {purpose} exited with {exit_code}.\n"
                "请查看任务/进程日志中的 core 错误 code、message 和 recovery。"
            )
        self._update_action_states()

    def _on_job_error(self, code: str, message: str) -> None:
        self.job_status_label.setText(f"IDE Job: {code}")
        self.process_log_viewer.appendPlainText(f"[{code}] {message}")
        self._update_action_states()

    def _desktop_error(self, code: str, message: str) -> None:
        self.diagnostics_viewer.setPlainText(f"{code}: {message}")
        self.statusBar().showMessage(f"{code}: {message}", 10000)
        self.workspace_tabs.setCurrentWidget(self.task_page)

    def _update_action_states(self) -> None:
        busy = bool(self.job_controller.is_running)
        has_workspace = self.workspace is not None
        can_generate = (
            has_workspace
            and bool(self.request_editor.toPlainText().strip())
            and not busy
        )
        can_confirm = self._draft_needs_confirmation and not busy
        can_compile = has_workspace and self._draft_can_compile and not busy
        can_solve = (
            has_workspace
            and bool(self.task_editor.toPlainText().strip())
            and not busy
        )
        self.generate_draft_button.setEnabled(can_generate)
        self.confirm_draft_button.setEnabled(can_confirm)
        self.compile_draft_button.setEnabled(can_compile)
        self.solve_button.setEnabled(can_solve)
        self.generate_action.setEnabled(can_generate)
        self.compile_action.setEnabled(can_compile)
        self.solve_action.setEnabled(can_solve)
        self.cancel_action.setEnabled(busy)
        allowed = set(
            self._recovery_decision.allowed_actions
            if self._recovery_decision is not None
            else ()
        )
        if self._recovery_decision is not None:
            self.cancel_action.setEnabled(RecoveryAction.CANCEL in allowed)
        self.terminate_orphan_action.setEnabled(
            RecoveryAction.TERMINATE_ORPHAN in allowed
        )
        self.recover_finalize_action.setEnabled(
            RecoveryAction.RECOVER_FINALIZE in allowed and not busy
        )
        self.resume_action.setEnabled(
            RecoveryAction.STRICT_RESUME in allowed
            and self.current_snapshot is not None
            and not busy
        )
        self.rerun_action.setEnabled(
            RecoveryAction.RERUN in allowed
            and self.current_snapshot is not None
            and not busy
        )
        self.resume_action.setText("恢复模型阶段")
        if (
            self.current_snapshot is not None
            and self.current_snapshot.summary is not None
        ):
            stage = self.current_snapshot.summary.resume.from_stage
            if stage is not None:
                label = (
                    "恢复模型生成"
                    if stage.value == "MODEL_GENERATION_STARTED"
                    else "恢复模型修复"
                )
                self.resume_action.setText(label)
        if self._recovery_decision is not None:
            tooltip = (
                f"{self._recovery_decision.code}: "
                f"{self._recovery_decision.recovery_zh}"
            )
            for action in (
                self.terminate_orphan_action,
                self.recover_finalize_action,
                self.resume_action,
                self.rerun_action,
            ):
                action.setToolTip(tooltip)
        self.environment_action.setEnabled(not busy)
        self.workspace_action.setEnabled(not busy)
        self.workspace_button.setEnabled(not busy)
        self.refresh_action.setEnabled(self.current_snapshot is not None)

    def _render_empty_state(self) -> None:
        self.job_status_label.setText("IDE Job: idle")
        self.current_stage_label.setText("Current stage: not available")
        self.task_id_label.setText("Task: not available")
        self.workflow_label.setText("Workflow: not available")
        self.native_label.setText("Native: not available")
        self.qualification_label.setText("Qualification: not available")
        self.attempts_label.setText("Attempts: 0")
        self.primary_failure_label.setText("Primary failure: not available")
        self.terminal_blocker_label.setText("Terminal blocker: not available")
        self.manifest_label.setText("Manifest: pending")
        self.config_source_label.setText("Config source: not available")
        self.openfoam_runtime_label.setText(
            "OpenFOAM root/version: not available"
        )
        self.isolation_label.setText("Requested isolation: not available")
        self.actual_backend_label.setText("Actual backend: not available")
        self.risk_label.setText("Risk: not available")
        self.sandbox_probe_label.setText("Sandbox probe: not available")
        self.fallback_warning_label.setText(
            "Fallback warning: not available"
        )
        self.status_label.setText("not available")
        self.knowledge_tree.clear()
        self.skill_tree.clear()
        self.residual_plot.set_samples(())
        self.residual_table.clear()
        self._update_action_states()

    def _render_snapshot(self, snapshot: RunSnapshot) -> None:
        summary = snapshot.summary
        task_id = _text(summary.task_id if summary is not None else None)
        workflow = _text(summary.workflow_state if summary is not None else None)
        native = _text(summary.native_status if summary is not None else None)
        attempts = len(summary.attempts) if summary is not None else 0
        primary_failure = _text(
            summary.primary_failure if summary is not None else None
        )
        terminal_blocker = _text(
            summary.terminal_blocker if summary is not None else None
        )
        latest = snapshot.timeline[-1] if snapshot.timeline else None
        current_stage = (
            f"{latest.stage} / {latest.state}" if latest is not None else "not available"
        )

        self.setWindowTitle(f"FoamPilot — {snapshot.run_dir.name}")
        self.current_stage_label.setText(f"Current stage: {current_stage}")
        self.task_id_label.setText(f"Task: {task_id}")
        self.workflow_label.setText(f"Workflow: {workflow}")
        self.native_label.setText(f"Native: {native}")
        self.qualification_label.setText("Qualification: not available")
        self.attempts_label.setText(f"Attempts: {attempts}")
        self.primary_failure_label.setText(f"Primary failure: {primary_failure}")
        self.terminal_blocker_label.setText(
            f"Terminal blocker: {terminal_blocker}"
        )
        self.manifest_label.setText(f"Manifest: {snapshot.manifest_state}")
        config = snapshot.runtime_config or {}
        provenance = snapshot.runtime_provenance or {}
        policy = snapshot.execution_policy or {}
        risk = snapshot.execution_risk or {}
        probe = snapshot.sandbox_probe or {}
        provenance_fields = provenance.get("fields")
        isolation_source: object | None = None
        if isinstance(provenance_fields, dict):
            source_record = provenance_fields.get("execution.isolation")
            if isinstance(source_record, dict):
                isolation_source = source_record.get("source")
                locator = source_record.get("locator")
                if locator:
                    isolation_source = f"{isolation_source} ({locator})"
        self.config_source_label.setText(
            f"Config source: {_text(isolation_source)}"
        )
        self.openfoam_runtime_label.setText(
            "OpenFOAM root/version: "
            f"{_text(config.get('openfoam_root'))} / "
            f"{_text(config.get('version'))}"
        )
        requested_isolation = policy.get(
            "requested_isolation",
            config.get("isolation"),
        )
        self.isolation_label.setText(
            f"Requested isolation: {_text(requested_isolation)}"
        )
        actual_backend = policy.get("actual_backend")
        self.actual_backend_label.setText(
            f"Actual backend: {_text(actual_backend)}"
        )
        self.risk_label.setText(
            f"Risk: {_text(risk.get('risk_level'))}"
        )
        probe_detail = probe.get("status")
        if probe.get("failure_code"):
            probe_detail = (
                f"{probe_detail} ({probe.get('failure_code')})"
            )
        self.sandbox_probe_label.setText(
            f"Sandbox probe: {_text(probe_detail)}"
        )
        if actual_backend == "host":
            warning = (
                policy.get("unisolated_warning")
                or policy.get("fallback_reason")
                or "typed argv 不提供文件系统或网络隔离"
            )
            fallback = f"未隔离宿主机执行：{warning}"
        elif actual_backend == "bubblewrap":
            fallback = "无；当前 attempt 使用 bubblewrap 隔离"
        else:
            fallback = _text(policy.get("fallback_reason"))
        self.fallback_warning_label.setText(
            f"Fallback warning: {fallback}"
        )
        self.status_label.setText(native if summary is not None else current_stage)

        overview_parts = [_summary_json(snapshot)]
        if snapshot.manifest_issues:
            overview_parts.append(
                "Manifest issues:\n" + "\n".join(snapshot.manifest_issues)
            )
        if snapshot.warnings:
            overview_parts.append("Warnings:\n" + "\n".join(snapshot.warnings))
        self.overview_viewer.setPlainText("\n\n".join(overview_parts))
        self.file_viewer.clear()
        self.log_viewer.clear()
        self._render_files(snapshot)
        self._render_timeline(snapshot)
        self._render_context(snapshot)
        self._render_residuals(snapshot)
        self._render_initial_report(snapshot)
        self._update_action_states()

    def _render_files(self, snapshot: RunSnapshot) -> None:
        self.file_tree.clear()
        roots: dict[str, QTreeWidgetItem] = {}
        for category, label in _CATEGORY_LABELS.items():
            root = QTreeWidgetItem([label, ""])
            root.setData(0, Qt.ItemDataRole.UserRole, None)
            roots[category] = root
            self.file_tree.addTopLevelItem(root)
        for file_view in snapshot.files:
            item = QTreeWidgetItem([file_view.path, str(file_view.bytes)])
            item.setData(0, Qt.ItemDataRole.UserRole, file_view.path)
            item.setData(
                0, Qt.ItemDataRole.UserRole + 1, file_view.category
            )
            roots[file_view.category].addChild(item)
        for root in roots.values():
            root.setExpanded(root.childCount() > 0)
            if root.childCount() == 0:
                root.setHidden(True)
        self.file_tree.resizeColumnToContents(0)

    def _render_timeline(self, snapshot: RunSnapshot) -> None:
        self.timeline_tree.clear()
        for event in snapshot.timeline:
            self.timeline_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        str(event.sequence),
                        event.stage,
                        event.state,
                        _text(event.attempt),
                        _text(event.step_id),
                        event.detail,
                    ]
                )
            )
        for column in range(self.timeline_tree.columnCount()):
            self.timeline_tree.resizeColumnToContents(column)

    def _render_context(self, snapshot: RunSnapshot) -> None:
        self.knowledge_tree.clear()
        for reference in snapshot.context_references:
            self.knowledge_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        reference.stage,
                        _text(reference.attempt),
                        reference.slot,
                        reference.entry_id,
                        _text(reference.title),
                        _text(reference.knowledge_type),
                        _text(reference.source_locator),
                        _text(reference.source_sha256),
                    ]
                )
            )
        self.skill_tree.clear()
        for reference in snapshot.skill_references:
            self.skill_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [reference.stage, _text(reference.attempt), reference.name]
                )
            )
        for tree in (self.knowledge_tree, self.skill_tree):
            for column in range(tree.columnCount()):
                tree.resizeColumnToContents(column)
        available = {item.path for item in snapshot.files}
        if "capability-profile.json" in available:
            try:
                text = self.repository.read_text(
                    snapshot, "capability-profile.json"
                )
            except RunOpenError as error:
                text = f"RUN_OPEN_FAILED: {error}"
            self.capability_viewer.setPlainText(text)
        else:
            self.capability_viewer.clear()
            self.capability_viewer.setPlaceholderText(
                "等待 capability routing 公开产物"
            )

    def _render_residuals(self, snapshot: RunSnapshot) -> None:
        self.residual_plot.set_samples(snapshot.residual_samples)
        self.residual_table.clear()
        latest: dict[str, object] = {}
        for sample in snapshot.residual_samples:
            latest[sample.field] = sample
        for field, raw_sample in latest.items():
            sample = raw_sample
            step = (
                f"Time {sample.simulation_time:g}"
                if sample.simulation_time is not None
                else (
                    f"Iteration {sample.iteration}"
                    if sample.iteration is not None
                    else "not available"
                )
            )
            self.residual_table.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        field,
                        f"{sample.initial_residual:.6g}",
                        f"{sample.final_residual:.6g}",
                        str(sample.solver_iterations),
                        step,
                        _text(sample.attempt),
                        sample.source_log,
                    ]
                )
            )
        for column in range(self.residual_table.columnCount()):
            self.residual_table.resizeColumnToContents(column)

    def _render_initial_report(self, snapshot: RunSnapshot) -> None:
        self.report_viewer.clear()
        priorities = (
            "public-validation.json",
            "qualification-report.json",
            "summary.json",
        )
        available = {item.path for item in snapshot.files}
        for relative_path in priorities:
            if relative_path not in available:
                continue
            try:
                text = self.repository.read_text(snapshot, relative_path)
            except RunOpenError as error:
                text = f"RUN_OPEN_FAILED: {error}"
            self.report_viewer.setPlainText(text)
            return

    def _show_selected_file(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        del previous
        if current is None or self.current_snapshot is None:
            return
        relative_path = current.data(0, Qt.ItemDataRole.UserRole)
        if not relative_path:
            return
        try:
            text = self.repository.read_text(
                self.current_snapshot, str(relative_path)
            )
        except RunOpenError as error:
            text = f"RUN_OPEN_FAILED: {error}"
        self.file_viewer.setPlainText(text)
        self.workspace_tabs.setCurrentWidget(self.artifact_page)
        self.central_tabs.setCurrentWidget(self.file_viewer)
        category = current.data(0, Qt.ItemDataRole.UserRole + 1)
        if category == "log":
            self.log_viewer.setPlainText(text)
        elif category == "report":
            self.report_viewer.setPlainText(text)


__all__ = ["FoamPilotMainWindow"]
