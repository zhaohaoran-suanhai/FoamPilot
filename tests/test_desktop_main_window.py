from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
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
from foampilot.desktop.main_window import FoamPilotMainWindow
from foampilot.workflow import WorkflowEvent, WorkflowStage


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
REAL_RUN = os.environ.get("FOAMPILOT_DESKTOP_REAL_RUN")


class RecordingJobController(QObject):
    job_started = Signal(str)
    output_received = Signal(str, str)
    run_discovered = Signal(object)
    job_finished = Signal(int, str)
    job_error = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.is_running = False
        self.calls: list[tuple[list[str], Path | None]] = []

    def start_cli(
        self,
        arguments: list[str] | tuple[str, ...],
        *,
        run_root: Path | None = None,
    ) -> None:
        self.calls.append((list(arguments), run_root))
        self.is_running = True
        self.job_started.emit(str(arguments[0]))

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


def test_open_verified_run_renders_read_only_snapshot(
    qtbot,
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    window.open_run(run_dir)

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

    window.open_run(finalized)
    assert window.manifest_label.text() == "Manifest: invalid"
    assert "hash mismatch" in window.overview_viewer.toPlainText()

    window.open_run(active)
    assert window.current_snapshot is not None
    assert window.manifest_label.text() == "Manifest: pending"


def test_refresh_reloads_the_opened_run(qtbot, tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)
    window.open_run(run_dir)
    (run_dir / "summary.json").write_text(
        json.dumps({**_summary(), "message": "refreshed"}),
        encoding="utf-8",
    )

    window.refresh_run()

    assert "refreshed" in window.overview_viewer.toPlainText()
    assert window.manifest_label.text() == "Manifest: invalid"


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

    window.open_run(run_dir)
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

    window.open_run(Path(str(REAL_RUN)))

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
    log = run_dir / "attempt-01/case/.foampilot/logs/solve.stdout.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "Time = 0.1\n"
        "smoothSolver: Solving for Ux, Initial residual = 0.2, "
        "Final residual = 0.01, No Iterations 2\n"
        "GAMG: Solving for p, Initial residual = 0.1, "
        "Final residual = 0.001, No Iterations 3\n",
        encoding="utf-8",
    )
    window = FoamPilotMainWindow()
    qtbot.addWidget(window)

    window.open_run(run_dir)

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

    assert window.current_snapshot is not None
    assert window.current_snapshot.run_dir == run_b.resolve()
    assert window.current_snapshot.run_dir != run_a.resolve()


def test_task_actions_build_fixed_cli_arguments(
    qtbot,
    tmp_path: Path,
) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    assert window.generate_draft_button.isEnabled() is False
    assert window.solve_button.isEnabled() is False

    window.set_workspace(tmp_path / "project")
    window.request_editor.setPlainText("求解一个二维不可压缩层流方腔。")
    assert window.generate_draft_button.isEnabled() is True
    window.generate_draft()

    arguments, run_root = controller.calls[-1]
    assert arguments[:3] == ["task", "draft", "--request-file"]
    assert arguments[-3:] == ["--backend", "auto", "--json"]
    assert run_root is None


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
    assert solve_arguments[-3:] == ["--backend", "auto", "--json"]
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

    assert window.current_snapshot is not None
    assert window.live_refresh_timer.isActive() is True
    log = run_dir / "attempt-01/case/.foampilot/logs/solve.stdout.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "Time = 1\nSolving for p, Initial residual = 0.1, "
        "Final residual = 0.01, No Iterations 1\n",
        encoding="utf-8",
    )
    window.live_refresh_timer.timeout.emit()

    assert window.residual_plot.sample_count == 1


def test_close_is_blocked_while_canonical_job_is_running(qtbot) -> None:
    controller = RecordingJobController()
    window = FoamPilotMainWindow(job_controller=controller)
    qtbot.addWidget(window)
    window.show()
    controller.is_running = True

    closed = window.close()

    assert closed is False
    assert window.isVisible() is True
    assert "求解仍在运行" in window.statusBar().currentMessage()
