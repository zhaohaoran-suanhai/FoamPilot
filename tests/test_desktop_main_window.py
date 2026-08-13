from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMessageBox,
    QPlainTextEdit,
    QTreeWidgetItem,
)
import pytest
import yaml

from foampilot.artifacts import ArtifactStore
from foampilot.desktop import application as desktop_application
from foampilot.desktop.job_controller import DesktopJobController, DesktopJobError
from foampilot.desktop.main_window import FoamPilotMainWindow
from foampilot.desktop.repository import RunRepository
from foampilot.evidence import MetricPoint
from foampilot.jobs import (
    JobState,
    LocalJobStore,
    RecoveryAction,
    RecoveryDecision,
    RecoveryState,
    build_job_spec,
    current_process_identity,
    process_identity,
)
from foampilot.workflow import WorkflowEvent, WorkflowStage
from tests.test_cli_results import _write_results


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
REAL_RUN = os.environ.get("FOAMPILOT_DESKTOP_REAL_RUN")


class RecordingJobController(QObject):
    job_started = Signal(str)
    output_received = Signal(str, str)
    run_discovered = Signal(object)
    job_finished = Signal(int, str)
    job_error = Signal(str, str)
    activity_received = Signal(object)
    job_status_changed = Signal(object)
    job_health_changed = Signal(str)
    recovery_decision_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.is_running = False
        self.calls: list[tuple[list[str], Path | None]] = []
        self.recovery_decision = None

    @property
    def current_arguments(self) -> tuple[str, ...]:
        return tuple(self.calls[-1][0]) if self.calls else ()

    def start_cli(
        self,
        arguments: list[str] | tuple[str, ...],
        *,
        run_root: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        del project_root
        self.calls.append((list(arguments), run_root))
        self.is_running = True
        self.job_started.emit(str(arguments[0]))

    def attach_latest(self, runs_root: Path) -> None:
        del runs_root

    def request_cancel(self) -> None:
        if not self.is_running:
            raise DesktopJobError("DESKTOP_JOB_NOT_RUNNING")
        self.is_running = True

    def request_terminate_orphan(self):
        return self.recovery_decision

    def request_recover_finalize(self):
        return self.recovery_decision

    def emit_recovery(self, decision: RecoveryDecision) -> None:
        self.recovery_decision = decision
        self.recovery_decision_changed.emit(decision)

    def finish(self, exit_code: int = 0) -> None:
        self.is_running = False
        self.job_finished.emit(exit_code, "normal")


def _summary() -> dict[str, object]:
    return {
        "schema_version": 2,
        "task_id": "desktop-fixture",
        "workflow_state": "COMPLETED",
        "native_status": "PUBLIC_VALIDATION_PASS",
        "last_completed_stage": "RUN_FINALIZED",
        "attempts": [
            {
                "attempt": 1,
                "status": "PUBLIC_VALIDATION_PASS",
                "failed_step_id": None,
                "failure_fingerprint": None,
                "changed_files": [],
            }
        ],
        "primary_failure": None,
        "terminal_blocker": None,
        "resume": {
            "allowed": False,
            "from_stage": None,
            "reason": "completed run",
        },
        "parent_run": None,
        "message": "All public checks pass.",
    }


def _event(
    sequence: int,
    stage: WorkflowStage,
    *,
    step_id: str | None = None,
) -> WorkflowEvent:
    return WorkflowEvent.completed(
        stage=stage,
        sequence=sequence,
        occurred_at=NOW,
        attempt=1,
        step_id=step_id,
    )


def _draft_yaml() -> str:
    def fact(
        path: str,
        value: object,
        *,
        source: str = "user_text",
        confirmed: bool = True,
        impact: str = "high",
    ) -> dict[str, object]:
        return {
            "path": path,
            "value": value,
            "source": source,
            "evidence": f"evidence for {path}",
            "impact": impact,
            "confirmed": confirmed,
        }

    return yaml.safe_dump(
        {
            "schema_version": 1,
            "draft_id": "desktop-draft",
            "request_text": "求解不可压缩层流方腔。",
            "facts": [
                fact(
                    "geometry",
                    {
                    "mode": "parametric",
                    "dimensionality": "two_d",
                    "description": "1 m square cavity",
                    "length_unit": "m",
                    "parameters": {
                        "width": {"value": 1.0, "unit": "m"},
                        "height": {"value": 1.0, "unit": "m"},
                    },
                },
                ),
                fact("physics.family", "fluid"),
                fact(
                    "physics.compressibility",
                    "incompressible",
                    source="model_inference",
                    confirmed=False,
                    impact="medium",
                ),
                fact("physics.phase_family", "single_phase"),
                fact("materials.fluid", {"nu": 1e-5}),
                fact("boundaries", [{"role": "wall"}]),
            ],
            "assumptions": [],
            "unresolved_questions": [
                {
                    "question_id": "regime",
                    "path": "physics.regime",
                    "kind": "confirmable",
                    "prompt_zh": "确认采用稳态还是瞬态？",
                    "reason_zh": "影响求解流程。",
                    "candidate": "steady",
                    "evidence": "用户描述未明确。",
                }
            ],
            "assets": [],
            "protected_paths": [],
            "status": "ready_for_confirmation",
        },
        sort_keys=False,
        allow_unicode=True,
    )


def _run(tmp_path: Path, *, finalized: bool = True) -> Path:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    (run_dir / "summary.json").write_text(
        json.dumps(_summary()),
        encoding="utf-8",
    )
    (run_dir / "workflow-events.jsonl").write_text(
        "\n".join(
            (
                _event(1, WorkflowStage.TASK_VALIDATED).model_dump_json(),
                _event(
                    2,
                    WorkflowStage.OPENFOAM_STEP_COMPLETE,
                    step_id="solve-pimplefoam",
                ).model_dump_json(),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    log = run_dir / "attempt-01/case/.foampilot/logs/solve.stdout.log"
    log.parent.mkdir(parents=True)
    log.write_text("Time = 0.03\nEnd\n", encoding="utf-8")
    validation = run_dir / "public-validation.json"
    validation.write_text(
        json.dumps({"status": "PUBLIC_VALIDATION_PASS"}),
        encoding="utf-8",
    )
    if finalized:
        store.finalize(run_dir)
    return run_dir


def _find_file_item(
    root: QTreeWidgetItem,
    relative_path: str,
) -> QTreeWidgetItem | None:
    for index in range(root.childCount()):
        child = root.child(index)
        if child.data(0, Qt.ItemDataRole.UserRole) == relative_path:
            return child
        found = _find_file_item(child, relative_path)
        if found is not None:
            return found
    return None


def _open_run(qtbot, window: FoamPilotMainWindow, run_dir: Path) -> None:
    window.open_run(run_dir)
    expected = run_dir.resolve()
    qtbot.waitUntil(
        lambda: window.current_snapshot is not None
        and window.current_snapshot.run_dir == expected,
        timeout=5000,
    )


def test_open_verified_run_renders_read_only_snapshot(
    qtbot,
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, run_dir)

    assert window.current_snapshot is not None
    assert window.windowTitle().startswith("FoamPilot")
    assert window.status_label.text() == "PUBLIC_VALIDATION_PASS"
    assert window.workflow_label.text() == "Workflow: COMPLETED"
    assert window.native_label.text() == "Native: PUBLIC_VALIDATION_PASS"
    assert window.qualification_label.text() == "Qualification: not available"
    assert window.manifest_label.text() == "Manifest: verified"
    assert window.timeline_tree.topLevelItemCount() == 2
    assert all(
        editor.isReadOnly()
        for editor in (
            window.overview_viewer,
            window.file_viewer,
            window.report_viewer,
            window.log_viewer,
        )
    )
    assert window.request_editor.isReadOnly() is False
    assert window.draft_editor.isReadOnly() is False
    assert window.task_editor.isReadOnly() is False

    relative_path = "attempt-01/case/.foampilot/logs/solve.stdout.log"
    item = _find_file_item(window.file_tree.invisibleRootItem(), relative_path)
    assert item is not None
    window.file_tree.setCurrentItem(item)
    qtbot.waitUntil(lambda: "Time = 0.03" in window.file_viewer.toPlainText())
    assert "Time = 0.03" in window.log_viewer.toPlainText()


def test_results_page_renders_shared_verdict_observation_and_condition(
    qtbot,
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path, finalized=False)
    _write_results(run_dir)
    ArtifactStore(run_dir.parent).finalize(run_dir)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, run_dir)

    assert window.workspace_tabs.indexOf(window.results_page) >= 0
    assert window.result_verdict_label.text() == "验收结论：PASS"
    assert window.observation_tree.topLevelItemCount() == 1
    observation = window.observation_tree.topLevelItem(0)
    assert observation.text(0) == "continuity"
    assert observation.text(3) == "AVAILABLE"
    assert window.condition_tree.topLevelItemCount() == 1
    condition = window.condition_tree.topLevelItem(0)
    assert condition.text(0) == "continuity-limit"
    assert condition.text(2) == "PASS"


def test_pending_design_renders_fields_reasons_and_candidates_without_override(
    qtbot,
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                **_summary(),
                "workflow_state": "DEFERRED",
                "native_status": None,
                "last_completed_stage": "DESIGNING_CASE",
                "attempts": [],
                "primary_failure": {
                    "domain": "design",
                    "code": "INFORMATION_REQUIRED",
                    "retryable": False,
                    "detail": "design facts are unresolved",
                    "message": "算例设计缺少必要信息。",
                    "recovery": "逐项补充或确认后创建子运行。",
                    "evidence_paths": ["questions.json"],
                },
                "message": "Simulation design needs concrete information.",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "questions.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": "INFORMATION_REQUIRED",
                "reason_codes": [
                    "REQUIRED_INFORMATION_MISSING_OR_CONFLICTING"
                ],
                "questions": [
                    {
                        "question_id": "confirm-nu",
                        "field_path": "materials.fluid.nu",
                        "impact": "high",
                        "kind": "confirmable",
                        "prompt_zh": "是否确认采用该运动黏度？",
                        "reason_zh": "该值只来自模型推断。",
                        "candidates": [
                            {
                                "candidate_id": "water-like-nu",
                                "value": {
                                    "value": 1.0e-6,
                                    "unit": "m2/s",
                                },
                                "rationale": "水样流体候选。",
                                "evidence": [],
                            }
                        ],
                        "conflicting_evidence": [],
                    },
                    {
                        "question_id": "provide-outlet-role",
                        "field_path": "boundaries.outlet.role",
                        "impact": "high",
                        "kind": "information_required",
                        "prompt_zh": "请补充 outlet 的物理角色。",
                        "reason_zh": "网格只能确定 patch 名称，不能确定用户语义。",
                        "candidates": [],
                        "conflicting_evidence": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store.finalize(run_dir)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, run_dir)

    rendered = window.report_viewer.toPlainText()
    assert "设计门禁：INFORMATION_REQUIRED" in rendered
    assert "字段：materials.fluid.nu" in rendered
    assert "原因：该值只来自模型推断。" in rendered
    assert "候选 water-like-nu" in rendered
    assert "1.0e-06" in rendered
    assert "m2/s" in rendered
    assert "字段：boundaries.outlet.role" in rendered
    assert "网格只能确定 patch 名称，不能确定用户语义。" in rendered
    action_text = "\n".join(
        action.text() for action in window.findChildren(QAction)
    ).casefold()
    assert "全部接受" not in action_text
    assert "accept all" not in action_text
    assert "continue anyway" not in action_text


def test_run_projection_is_loaded_outside_qt_main_thread(
    qtbot,
    tmp_path: Path,
) -> None:
    class RecordingRepository(RunRepository):
        def __init__(self) -> None:
            super().__init__()
            self.thread_ids: list[int] = []

        def open(self, run_dir: str | Path):
            self.thread_ids.append(threading.get_ident())
            return super().open(run_dir)

    repository = RecordingRepository()
    window = FoamPilotMainWindow(repository=repository)
    qtbot.addWidget(window)

    _open_run(qtbot, window, _run(tmp_path))

    assert repository.thread_ids
    assert repository.thread_ids[-1] != threading.get_ident()


def test_refresh_failure_preserves_last_snapshot_and_reports_degraded(
    qtbot,
    tmp_path: Path,
) -> None:
    class FailingRefreshRepository(RunRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def open(self, run_dir: str | Path):
            self.calls += 1
            if self.calls > 1:
                raise OSError("synthetic read failure")
            return super().open(run_dir)

    repository = FailingRefreshRepository()
    window = FoamPilotMainWindow(repository=repository)
    qtbot.addWidget(window)
    _open_run(qtbot, window, _run(tmp_path))
    snapshot = window.current_snapshot

    window.refresh_run()

    qtbot.waitUntil(
        lambda: "DESKTOP_REFRESH_DEGRADED"
        in window.statusBar().currentMessage(),
        timeout=5000,
    )
    assert window.current_snapshot is snapshot


def test_manifest_invalid_and_active_run_are_distinct(
    qtbot,
    tmp_path: Path,
) -> None:
    finalized = _run(tmp_path / "finalized")
    (finalized / "public-validation.json").write_text(
        '{"status":"changed"}\n',
        encoding="utf-8",
    )
    active = _run(tmp_path / "active", finalized=False)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, finalized)
    assert window.manifest_label.text() == "Manifest: invalid"
    assert "hash mismatch" in window.overview_viewer.toPlainText()

    _open_run(qtbot, window, active)
    assert window.current_snapshot is not None
    assert window.manifest_label.text() == "Manifest: pending"


def test_refresh_reloads_the_opened_run(qtbot, tmp_path: Path) -> None:
    run_dir = _run(tmp_path, finalized=False)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)
    _open_run(qtbot, window, run_dir)
    (run_dir / "summary.json").write_text(
        json.dumps({**_summary(), "message": "refreshed"}),
        encoding="utf-8",
    )

    window.refresh_run()

    qtbot.waitUntil(
        lambda: "refreshed" in window.overview_viewer.toPlainText(),
        timeout=5000,
    )
    assert "refreshed" in window.overview_viewer.toPlainText()
    assert window.manifest_label.text() == "Manifest: pending"


def test_open_error_has_stable_code_and_rejected_path(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: shown.append((title, message)),
    )
    missing = tmp_path / "missing-run"
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    window.open_run(missing)

    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert window.current_snapshot is None
    assert shown
    assert "RUN_OPEN_FAILED" in shown[0][1]
    assert "无法打开 FoamPilot Run" in shown[0][1]
    assert str(missing) in shown[0][1]


def test_settings_restore_last_successful_run_outside_artifacts(
    qtbot,
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path / "run-root")
    settings_path = tmp_path / "settings" / "desktop.ini"
    settings_path.parent.mkdir()
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    window = FoamPilotMainWindow(settings=settings)
    qtbot.addWidget(window)

    _open_run(qtbot, window, run_dir)
    window.close()
    settings.sync()

    assert Path(str(settings.value("desktop/last_run"))) == run_dir.resolve()
    assert set(settings.allKeys()) == {
        "desktop/last_run",
        "desktop/window_geometry",
        "desktop/window_state",
    }
    assert not settings_path.is_relative_to(run_dir)
    manifest = json.loads(
        (run_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert settings_path.name not in manifest["files"]

    restored_settings = QSettings(
        str(settings_path),
        QSettings.Format.IniFormat,
    )
    restored = FoamPilotMainWindow(settings=restored_settings)
    qtbot.addWidget(restored)

    restored.restore_last_run()

    qtbot.waitUntil(lambda: restored.current_snapshot is not None, timeout=5000)
    assert restored.current_snapshot is not None
    assert restored.current_snapshot.run_dir == run_dir.resolve()


def test_missing_last_run_is_ignored_with_nonfatal_warning(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = QSettings(
        str(tmp_path / "desktop.ini"),
        QSettings.Format.IniFormat,
    )
    missing = tmp_path / "deleted-run"
    settings.setValue("desktop/last_run", str(missing))
    settings.sync()
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: pytest.fail("missing recovery path must not be modal"),
    )
    window = FoamPilotMainWindow(settings=settings)
    qtbot.addWidget(window)

    window.restore_last_run()

    assert window.current_snapshot is None
    assert "最近 Run 不存在" in window.recovery_warning
    assert str(missing) in window.statusBar().currentMessage()


@pytest.mark.skipif(
    not REAL_RUN or not Path(REAL_RUN).is_dir(),
    reason="real Desktop A run gate is opt-in",
)
def test_real_run_inspector_gate(qtbot) -> None:
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, Path(str(REAL_RUN)))

    assert window.current_snapshot is not None
    snapshot = window.current_snapshot
    assert snapshot.summary is not None
    assert snapshot.summary.task_id == "pimple-blocked-channel"
    assert snapshot.summary.workflow_state == "COMPLETED"
    assert snapshot.summary.native_status == "PUBLIC_VALIDATION_PASS"
    assert snapshot.manifest_state == "verified"
    assert any(
        event.step_id == "solve-pimplefoam" for event in snapshot.timeline
    )
    assert window.manifest_label.text() == "Manifest: verified"


def test_launch_keeps_window_alive_with_existing_application(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qtbot
    run_dir = _run(tmp_path / "run-root")
    settings = QSettings(
        str(tmp_path / "desktop.ini"),
        QSettings.Format.IniFormat,
    )
    monkeypatch.setattr(desktop_application, "QSettings", lambda: settings)
    application = QApplication.instance()
    assert application is not None
    existing = set(application.topLevelWidgets())

    assert desktop_application.launch(run_dir) == 0
    application.processEvents()

    created = set(application.topLevelWidgets()) - existing
    assert len(created) == 1
    window = created.pop()
    assert isinstance(window, FoamPilotMainWindow)
    assert window.isVisible()
    window.close()
    application.processEvents()


def test_context_and_residual_tabs_render_public_run_evidence(
    qtbot,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-active"
    run_dir.mkdir()
    source_hash = "96538945ea4170a24ef356863904a745fa68b586e489b33ac0b6cbd8f8954641"
    (run_dir / "agent-context.json").write_text(
        json.dumps(
            {
                "knowledge_slots": {
                    "solver_family_contract": "of10.solver.icofoam-contract"
                },
                "missing_slots": [],
                "selected_knowledge_ids": [
                    "of10.solver.icofoam-contract"
                ],
                "selected_source_hashes": {
                    "of10.solver.icofoam-contract": source_hash
                },
                "skill_names": ["openfoam-author-native-case"],
            }
        ),
        encoding="utf-8",
    )
    metrics = run_dir / "metrics.jsonl"
    metrics.write_text(
        "".join(
            MetricPoint(
                sequence=sequence,
                occurred_at=datetime.now(timezone.utc),
                attempt=1,
                step_id="solve",
                simulation_time=0.1,
                series=f"residual:{field}",
                value=value,
            ).model_dump_json()
            + "\n"
            for sequence, (field, value) in enumerate(
                (("Ux", 0.2), ("p", 0.1)),
                start=1,
            )
        ),
        encoding="utf-8",
    )
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, run_dir)

    assert window.knowledge_tree.topLevelItemCount() == 1
    knowledge = window.knowledge_tree.topLevelItem(0)
    assert knowledge.text(2) == "solver_family_contract"
    assert knowledge.text(3) == "of10.solver.icofoam-contract"
    assert "icoFoam" in knowledge.text(4)
    assert window.skill_tree.topLevelItemCount() == 1
    assert window.skill_tree.topLevelItem(0).text(2) == (
        "openfoam-author-native-case"
    )
    assert window.residual_plot.sample_count == 2
    assert window.residual_plot.fields == ("Ux", "p")
    assert window.residual_table.topLevelItemCount() == 2


def test_runtime_security_artifacts_render_unisolated_warning(
    qtbot,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-runtime"
    attempt = run_dir / "attempt-01"
    attempt.mkdir(parents=True)
    (run_dir / "task.yaml").write_text("task_id: runtime\n", encoding="utf-8")
    (run_dir / "runtime-config.json").write_text(
        json.dumps(
            {
                "openfoam_root": "/opt/OpenFOAM/OpenFOAM-10",
                "version": "10",
                "isolation": "sandbox_preferred",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "runtime-config-provenance.json").write_text(
        json.dumps(
            {
                "fields": {
                    "execution.isolation": {
                        "source": "environment",
                        "locator": "FOAMPILOT_EXECUTION_ISOLATION",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (attempt / "execution-risk-report.json").write_text(
        json.dumps({"risk_level": "low"}),
        encoding="utf-8",
    )
    (attempt / "execution-policy.json").write_text(
        json.dumps(
            {
                "requested_isolation": "sandbox_preferred",
                "actual_backend": "host",
                "fallback_reason": "Operation not permitted",
                "unisolated_warning": "host execution is not isolated",
            }
        ),
        encoding="utf-8",
    )
    (attempt / "sandbox-probe.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failure_code": "NAMESPACE_UNAVAILABLE",
            }
        ),
        encoding="utf-8",
    )
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    _open_run(qtbot, window, run_dir)

    assert "environment" in window.config_source_label.text()
    assert "OpenFOAM-10" in window.openfoam_runtime_label.text()
    assert "sandbox_preferred" in window.isolation_label.text()
    assert "host" in window.actual_backend_label.text()
    assert "low" in window.risk_label.text()
    assert "NAMESPACE_UNAVAILABLE" in window.sandbox_probe_label.text()
    assert "未隔离" in window.fallback_warning_label.text()


def test_batch_root_offers_concrete_child_selection(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = tmp_path / "batch"
    run_a = batch / "run-20260811-a"
    run_b = batch / "run-20260811-b"
    run_a.mkdir(parents=True)
    run_b.mkdir()
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: (run_b.name, True),
    )
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    window.open_run(batch)
    qtbot.waitUntil(lambda: window.current_snapshot is not None, timeout=5000)

    assert window.current_snapshot is not None
    assert window.current_snapshot.run_dir == run_b.resolve()
    assert window.current_snapshot.run_dir != run_a.resolve()


def test_task_actions_build_fixed_cli_arguments(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(
        job_controller=controller,
        repository=RunRepository(active_rescan_seconds=0.0),
    )
    qtbot.addWidget(window)
    assert window.generate_draft_button.isEnabled() is False
    assert window.solve_button.isEnabled() is False

    window.set_workspace(tmp_path / "project")
    window.request_editor.setPlainText("求解一个二维不可压缩层流方腔。")
    assert window.generate_draft_button.isEnabled() is True
    window.generate_draft()

    arguments, run_root = controller.calls[-1]
    assert arguments[:3] == ["task", "draft", "--request-file"]
    assert arguments[-5:] == [
        "--backend",
        "auto",
        "--progress",
        "jsonl",
        "--json",
    ]
    assert run_root is not None
    assert run_root.parent.name == "runs"


def test_runtime_options_reach_preflight_and_solve_but_not_task_draft(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    runtime_args = (
        "--runtime-config",
        "/tmp/runtime.toml",
        "--execution-isolation",
        "sandbox_required",
    )
    window = FoamPilotMainWindow(
        job_controller=controller,
        runtime_cli_args=runtime_args,
    )
    qtbot.addWidget(window)

    window.check_environment()
    preflight, _ = controller.calls[-1]
    assert preflight == ["preflight", *runtime_args, "--json"]
    controller.finish(0)
    controller.finish(0)

    window.set_workspace(tmp_path / "project")
    window.request_editor.setPlainText("求解二维方腔。")
    window.generate_draft()
    draft, _ = controller.calls[-1]
    assert draft[0:2] == ["task", "draft"]
    assert "--runtime-config" not in draft
    controller.finish(4)

    window.task_editor.setPlainText("schema_version: 2\ntask_id: direct\n")
    window.start_solve()
    controller.finish(0)
    solve, _ = controller.calls[-1]
    json_index = solve.index("--json")
    assert solve[json_index - len(runtime_args) : json_index] == list(
        runtime_args
    )


def test_direct_taskspec_validates_then_starts_unique_solve(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    window.set_workspace(tmp_path / "project")
    window.task_editor.setPlainText("schema_version: 2\ntask_id: direct\n")

    window.start_solve()

    validate_arguments, validate_root = controller.calls[-1]
    assert validate_arguments[0] == "validate"
    assert validate_arguments[-1] == "--json"
    assert validate_root is None
    controller.finish(0)

    solve_arguments, solve_root = controller.calls[-1]
    assert solve_arguments[0] == "solve"
    assert solve_arguments[2:4] == ["--run-root", str(solve_root)]
    assert solve_arguments[4:6] == [
        "--public-asset-root",
        str(window.workspace.root),
    ]
    assert solve_arguments[-5:] == [
        "--backend",
        "auto",
        "--progress",
        "jsonl",
        "--json",
    ]
    assert solve_root is not None
    assert solve_root.parent.name == "runs"
    assert solve_root.is_dir()
    assert window.live_refresh_timer.isActive() is False


def test_incomplete_draft_can_be_confirmed_and_compiled_without_yaml_editing(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    window.set_workspace(tmp_path / "project")
    window.request_editor.setPlainText("求解不可压缩层流方腔。")
    window.generate_draft()
    draft_arguments, _ = controller.calls[-1]
    output_index = draft_arguments.index("--output") + 1
    draft_path = Path(draft_arguments[output_index])
    draft_path.write_text(_draft_yaml(), encoding="utf-8")

    controller.finish(4)

    assert window.confirm_draft_button.isEnabled() is True
    assert "regime" in window._question_editors
    window.confirm_draft()
    assert window.compile_draft_button.isEnabled() is True
    assert "status: confirmed" in window.draft_editor.toPlainText()
    window.compile_draft()
    validate_arguments, _ = controller.calls[-1]
    assert validate_arguments[:2] == ["task", "validate-draft"]
    controller.finish(0)
    compile_arguments, _ = controller.calls[-1]
    assert compile_arguments[:2] == ["task", "compile"]
    output_index = compile_arguments.index("--output") + 1
    task_path = Path(compile_arguments[output_index])
    task_path.write_text("schema_version: 2\ntask_id: compiled\n", encoding="utf-8")

    controller.finish(0)

    assert "task_id: compiled" in window.task_editor.toPlainText()
    assert window.solve_button.isEnabled() is True


def test_audit_only_model_inference_does_not_enable_confirmation(
    qtbot,
) -> None:
    window = FoamPilotMainWindow(job_controller=RecordingJobController())
    qtbot.addWidget(window)
    payload = yaml.safe_load(_draft_yaml())
    payload["unresolved_questions"] = []
    payload["status"] = "confirmed"

    window.load_draft_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    )

    assert window.confirm_draft_button.isEnabled() is False
    assert window._draft_needs_confirmation is False


def test_discovered_run_starts_live_refresh_and_updates_residuals(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    run_dir = tmp_path / "run-active"
    run_dir.mkdir()

    controller.is_running = True
    controller.run_discovered.emit(run_dir)

    qtbot.waitUntil(lambda: window.current_snapshot is not None, timeout=5000)
    assert window.current_snapshot is not None
    assert window.live_refresh_timer.isActive() is True
    (run_dir / "metrics.jsonl").write_text(
        MetricPoint(
            sequence=1,
            occurred_at=datetime.now(timezone.utc),
            attempt=1,
            step_id="solve",
            simulation_time=1.0,
            series="residual:p",
            value=0.1,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    window.live_refresh_timer.timeout.emit()

    qtbot.waitUntil(
        lambda: window.residual_plot.sample_count == 1,
        timeout=5000,
    )
    assert window.residual_plot.sample_count == 1


def test_close_leaves_detached_canonical_job_running(qtbot) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    window.show()
    controller.is_running = True

    closed = window.close()

    assert closed is True
    assert window.isVisible() is False
    assert controller.is_running is True


def _recovery_decision(
    state: RecoveryState,
    actions: tuple[RecoveryAction, ...],
    *,
    run_dir: Path | None = None,
) -> RecoveryDecision:
    return RecoveryDecision(
        job_id="job-desktop-recovery",
        state=state,
        code=f"JOB_{state.value}",
        reason_zh="确定性恢复诊断。",
        recovery_zh="只允许证据支持的操作。",
        allowed_actions=actions,
        worker_alive=state in {RecoveryState.RUNNING, RecoveryState.UNRESPONSIVE},
        child_alive=state == RecoveryState.ORPHANED_ACTIVE,
        writer_lock_held=state in {RecoveryState.RUNNING, RecoveryState.UNRESPONSIVE},
        run_dir=run_dir,
    )


def test_desktop_recovery_action_matrix_is_decision_driven(
    qtbot,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)

    assert "HTTP" in window.cancel_action.toolTip()
    assert "超时" in window.cancel_action.toolTip()

    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.RUNNING,
            (RecoveryAction.ATTACH, RecoveryAction.CANCEL),
        )
    )
    assert window.cancel_action.isEnabled() is True
    assert window.terminate_orphan_action.isEnabled() is False
    assert window.recover_finalize_action.isEnabled() is False

    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.UNRESPONSIVE,
            (RecoveryAction.ATTACH, RecoveryAction.CANCEL),
        )
    )
    assert window.cancel_action.isEnabled() is True
    assert window.resume_action.isEnabled() is False
    assert window.rerun_action.isEnabled() is False

    controller.is_running = True
    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.ORPHANED_ACTIVE,
            (RecoveryAction.INSPECT, RecoveryAction.TERMINATE_ORPHAN),
        )
    )
    assert window.terminate_orphan_action.isEnabled() is True
    assert window.cancel_action.isEnabled() is False
    assert window.recover_finalize_action.isEnabled() is False
    assert window.resume_action.isEnabled() is False
    assert window.rerun_action.isEnabled() is False

    controller.is_running = False
    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.ORPHANED_STOPPED,
            (RecoveryAction.RECOVER_FINALIZE,),
        )
    )
    assert window.terminate_orphan_action.isEnabled() is False
    assert window.recover_finalize_action.isEnabled() is True
    assert "OpenFOAM continuation" in window.recovery_hint_label.text()
    assert "JOB_ORPHANED_STOPPED" in window.recovery_hint_label.text()

    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.EVIDENCE_DAMAGED,
            (RecoveryAction.INSPECT,),
        )
    )
    assert window.cancel_action.isEnabled() is False
    assert window.terminate_orphan_action.isEnabled() is False
    assert window.recover_finalize_action.isEnabled() is False
    assert window.resume_action.isEnabled() is False
    assert window.rerun_action.isEnabled() is False


def test_desktop_real_controller_can_terminate_active_orphan(
    qtbot,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    job_root = project / "runs/job-orphan-active"
    job_root.mkdir(parents=True)
    task = project / "task.yaml"
    task.write_text("task_id: orphan-ui\n", encoding="utf-8")
    store = LocalJobStore(job_root)
    store.create(
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation="solve",
            arguments=("solve", str(task), "--run-root", str(job_root)),
        )
    )
    store.initialize_status()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    store.update_status(
        state=JobState.RUNNING,
        worker=current_process_identity().model_copy(
            update={"start_token": 0}
        ),
        current_child=process_identity(child.pid),
    )
    controller = DesktopJobController(discovery_interval_ms=20)
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    try:
        window.set_workspace(project)

        assert controller.recovery_decision is not None
        assert (
            controller.recovery_decision.state
            == RecoveryState.ORPHANED_ACTIVE
        )
        assert controller.is_running is True
        assert window.terminate_orphan_action.isEnabled() is True

        window.terminate_orphan_job()
        child.wait(timeout=5)

        assert controller.recovery_decision is not None
        assert (
            controller.recovery_decision.state
            == RecoveryState.ORPHANED_STOPPED
        )
        assert window.terminate_orphan_action.isEnabled() is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        controller.job_poll_timer.stop()


def test_desktop_complete_rerun_submits_a_new_detached_job(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    window.set_workspace(tmp_path)
    run_dir = _run(tmp_path)
    _open_run(qtbot, window, run_dir)
    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.FINALIZED,
            (RecoveryAction.REPORT, RecoveryAction.RERUN),
            run_dir=run_dir,
        )
    )

    window.start_rerun()

    arguments, job_root = controller.calls[-1]
    assert arguments[:2] == ["rerun", str(run_dir)]
    assert "--run-root" in arguments
    assert "--progress" in arguments
    assert job_root is not None
    assert job_root != run_dir.parent
    assert window.rerun_action.isEnabled() is False
    assert window.resume_action.isEnabled() is False


def test_desktop_labels_and_submits_strict_model_repair_resume(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    window.set_workspace(tmp_path)
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    summary = {
        **_summary(),
        "workflow_state": "DEFERRED",
        "native_status": "SOLVER_FAILED",
        "terminal_blocker": {
            "domain": "backend",
            "code": "OVERLOADED",
            "retryable": True,
            "detail": "backend overloaded",
            "step_id": None,
            "message": None,
            "recovery": None,
            "evidence_paths": [],
        },
        "resume": {
            "allowed": True,
            "from_stage": "MODEL_REPAIR_STARTED",
            "reason": "retryable repair request",
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    store.finalize(run_dir)
    _open_run(qtbot, window, run_dir)
    controller.emit_recovery(
        _recovery_decision(
            RecoveryState.FINALIZED,
            (
                RecoveryAction.REPORT,
                RecoveryAction.STRICT_RESUME,
                RecoveryAction.RERUN,
            ),
            run_dir=run_dir,
        )
    )

    assert window.resume_action.text() == "恢复模型修复"
    assert window.resume_action.isEnabled() is True
    window.start_strict_resume()

    arguments, job_root = controller.calls[-1]
    assert arguments[:2] == ["resume", str(run_dir)]
    assert job_root is not None
    assert "continuation" not in " ".join(arguments).lower()
