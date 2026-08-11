from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import sys

import pytest
from PySide6.QtCore import QProcess

from foampilot.activity import ActivityEvent
from foampilot.jobs import (
    JobState,
    LocalJobStore,
    current_process_identity,
)
from foampilot.desktop.job_controller import (
    DesktopJobController,
    DesktopJobError,
)


def _python_controller() -> DesktopJobController:
    return DesktopJobController(
        program=sys.executable,
        prefix_args=(),
        allowed_commands=("-c",),
        discovery_interval_ms=20,
    )


def test_controller_delivers_separate_output_and_exit_code(qtbot) -> None:
    controller = _python_controller()
    stdout: list[str] = []
    stderr: list[str] = []
    finished: list[tuple[int, str]] = []
    controller.output_received.connect(
        lambda channel, text: (
            stdout.append(text) if channel == "stdout" else stderr.append(text)
        )
    )
    controller.job_finished.connect(
        lambda code, status: finished.append((code, status))
    )

    with qtbot.waitSignal(controller.job_finished, timeout=3000):
        controller.start_cli(
            [
                "-c",
                "import sys; print('OUT'); print('ERR', file=sys.stderr)",
            ]
        )

    assert "OUT" in "".join(stdout)
    assert "ERR" in "".join(stderr)
    assert finished == [(0, "normal")]
    assert controller.is_running is False


def test_controller_rejects_second_job_while_busy(qtbot) -> None:
    controller = _python_controller()
    controller.start_cli(
        ["-c", "import time; print('started', flush=True); time.sleep(0.4)"]
    )
    qtbot.waitUntil(lambda: controller.is_running, timeout=1000)

    with pytest.raises(DesktopJobError, match="DESKTOP_JOB_BUSY"):
        controller.start_cli(["-c", "print('second')"])

    with qtbot.waitSignal(controller.job_finished, timeout=3000):
        pass


def test_controller_discovers_unique_run_child(qtbot, tmp_path: Path) -> None:
    controller = _python_controller()
    run_root = tmp_path / "job"
    run_root.mkdir()
    discovered: list[Path] = []
    controller.run_discovered.connect(discovered.append)

    with qtbot.waitSignal(controller.run_discovered, timeout=3000):
        controller.start_cli(
            [
                "-c",
                "from pathlib import Path; import sys, time; "
                "(Path(sys.argv[1]) / 'run-test').mkdir(); time.sleep(0.2)",
                str(run_root),
            ],
            run_root=run_root,
        )

    assert discovered == [(run_root / "run-test").resolve()]
    with qtbot.waitSignal(controller.job_finished, timeout=3000):
        pass


def test_controller_rejects_unregistered_command() -> None:
    controller = _python_controller()

    with pytest.raises(DesktopJobError, match="DESKTOP_COMMAND_REJECTED"):
        controller.start_cli(["solve", "task.yaml"])


def test_controller_decodes_activity_and_preserves_invalid_stderr(qtbot) -> None:
    controller = _python_controller()
    event = ActivityEvent(
        sequence=1,
        operation_id="desktop-test",
        kind="heartbeat",
        state="alive",
        source="model",
        occurred_at=datetime.now(timezone.utc),
        stage="generation",
        message="model request is active",
    )
    activities: list[ActivityEvent] = []
    stderr: list[str] = []
    controller.activity_received.connect(activities.append)
    controller.output_received.connect(
        lambda channel, text: stderr.append(text)
        if channel == "stderr"
        else None
    )

    with qtbot.waitSignal(controller.job_finished, timeout=3000):
        controller.start_cli(
            [
                "-c",
                "import sys; "
                "sys.stderr.write(sys.argv[1] + '\\nlegacy diagnostic\\n')",
                event.model_dump_json(),
            ]
        )

    assert activities == [event]
    assert "legacy diagnostic" in "".join(stderr)
    assert event.operation_id not in "".join(stderr)


def test_controller_submits_detached_job_discovers_run_and_finishes(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    job_root = project / "runs/job-detached"
    job_root.mkdir(parents=True)
    task = project / "task.yaml"
    task.write_text("task_id: desktop\n", encoding="utf-8")
    launched: list[Path] = []
    monkeypatch.setattr(
        "foampilot.desktop.job_controller.launch_local_job",
        lambda root, **kwargs: launched.append(Path(root)) or 123,
    )
    controller = DesktopJobController(discovery_interval_ms=20)
    statuses = []
    health = []
    runs = []
    controller.job_status_changed.connect(statuses.append)
    controller.job_health_changed.connect(health.append)
    controller.run_discovered.connect(runs.append)

    controller.start_cli(
        ["solve", str(task), "--run-root", str(job_root), "--json"],
        run_root=job_root,
        project_root=project,
    )
    store = LocalJobStore(job_root)
    run = job_root / "run-test"
    run.mkdir()
    store.update_status(
        state=JobState.RUNNING,
        worker=current_process_identity(),
        run_dir=run.name,
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    controller._poll_job()

    assert launched == [job_root.resolve()]
    assert controller.process.state() == QProcess.ProcessState.NotRunning
    assert controller.is_running is True
    assert runs == [run.resolve()]
    assert statuses[-1].state == JobState.RUNNING
    assert health[-1] == "RUNNING"

    store.update_status(
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=30)
    )
    controller._poll_job()
    assert health[-1] == "UNRESPONSIVE"

    with qtbot.waitSignal(controller.job_finished, timeout=1000):
        store.update_status(
            state=JobState.COMPLETED,
            terminal_code="CLI_EXIT_4",
        )
        controller._poll_job()
    assert controller.is_running is False


def test_controller_attaches_existing_job_and_requests_cancel(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    job_root = project / "runs/job-existing"
    job_root.mkdir(parents=True)
    task = project / "task.yaml"
    task.write_text("task_id: desktop\n", encoding="utf-8")
    monkeypatch.setattr(
        "foampilot.desktop.job_controller.launch_local_job",
        lambda *args, **kwargs: 123,
    )
    submitter = DesktopJobController(discovery_interval_ms=20)
    submitter.start_cli(
        ["solve", str(task), "--run-root", str(job_root), "--json"],
        run_root=job_root,
        project_root=project,
    )
    submitter.job_poll_timer.stop()
    store = LocalJobStore(job_root)
    store.update_status(
        state=JobState.RUNNING,
        worker=current_process_identity(),
    )

    controller = DesktopJobController(discovery_interval_ms=20)
    controller.attach_job(job_root)
    controller.request_cancel()

    assert controller.current_job_dir == job_root.resolve()
    assert store.cancel_requested is True
    controller.job_poll_timer.stop()
