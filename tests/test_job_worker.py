from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Thread, current_thread
import time

import pytest

from foampilot.activity import OperationCancelled
from foampilot.jobs import (
    JobState,
    LocalJobStore,
    RecoveryState,
    build_job_spec,
    reconcile_job,
    run_local_job,
)
from foampilot.jobs.worker import JobStatusWriteError


def _job(tmp_path: Path, *, operation: str = "plan") -> LocalJobStore:
    project = tmp_path / "project"
    job_root = project / "runs/job-worker"
    job_root.mkdir(parents=True)
    task = project / "tasks/task.yaml"
    task.parent.mkdir()
    task.write_text("task_id: worker-test\n", encoding="utf-8")
    arguments = (
        ("plan", str(task), "--output", str(project / "plan.json"), "--json")
        if operation == "plan"
        else ("solve", str(task), "--run-root", str(job_root), "--json")
    )
    store = LocalJobStore(job_root)
    store.create(
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation=operation,
            arguments=arguments,
        )
    )
    store.initialize_status()
    return store


def test_worker_updates_heartbeat_activity_and_terminal_status(
    tmp_path: Path,
) -> None:
    store = _job(tmp_path)

    def fake_cli(argv, *, activity_reporter) -> int:
        assert argv[0] == "plan"
        print("final-json")
        activity_reporter.emit(
            kind="stage",
            state="started",
            source="model",
            stage="generation",
            message="generation started",
        )
        time.sleep(0.08)
        activity_reporter.emit(
            kind="stage",
            state="completed",
            source="model",
            stage="generation",
            message="generation completed",
        )
        return 0

    exit_code = run_local_job(
        store.root,
        cli_runner=fake_cli,
        heartbeat_seconds=0.02,
    )

    status = store.read_status()
    assert exit_code == 0
    assert status.state == JobState.COMPLETED
    assert status.worker is not None
    assert status.worker.pid == os.getpid()
    assert status.revision >= 6
    assert status.last_heartbeat_at is not None
    assert status.finished_at is not None
    assert status.current_child is None
    assert (store.root / "worker.stdout.log").read_text() == "final-json\n"
    events = [
        json.loads(line)
        for line in (store.root / "job-events.jsonl").read_text().splitlines()
    ]
    assert [item["state"] for item in events] == ["started", "completed"]


def test_worker_preserves_confirmation_required_as_normal_cli_terminal(
    tmp_path: Path,
) -> None:
    store = _job(tmp_path, operation="solve")

    exit_code = run_local_job(
        store.root,
        cli_runner=lambda argv, *, activity_reporter: 4,
    )

    status = store.read_status()
    assert exit_code == 4
    assert status.state == JobState.COMPLETED
    assert status.terminal_code == "CLI_EXIT_4"


def test_worker_cancel_request_is_observed_and_terminal_once(
    tmp_path: Path,
) -> None:
    store = _job(tmp_path, operation="solve")

    def cancellable_cli(argv, *, activity_reporter) -> int:
        del argv
        while not activity_reporter.is_cancel_requested():
            time.sleep(0.01)
        raise OperationCancelled()

    result: list[int] = []
    thread = Thread(
        target=lambda: result.append(
            run_local_job(
                store.root,
                cli_runner=cancellable_cli,
                heartbeat_seconds=0.02,
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    while store.read_status().state != JobState.RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    first = store.request_cancel(requested_by="test")
    second = store.request_cancel(requested_by="ignored")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result == [130]
    assert first == second
    status = store.read_status()
    assert status.state == JobState.CANCELLED
    assert status.finished_at is not None
    assert status.terminal_code == "USER_CANCELLED"


def test_worker_pre_run_cancel_reconciles_as_legitimate_terminal(
    tmp_path: Path,
) -> None:
    store = _job(tmp_path, operation="solve")
    store.request_cancel(requested_by="test")
    cli_called = False

    def forbidden_cli(argv, *, activity_reporter):
        nonlocal cli_called
        del argv, activity_reporter
        cli_called = True
        return 0

    exit_code = run_local_job(store.root, cli_runner=forbidden_cli)
    decision = reconcile_job(store.root)

    assert exit_code == 130
    assert cli_called is False
    assert store.read_status().state == JobState.CANCELLED
    assert decision.state == RecoveryState.FINALIZED
    assert decision.code == "USER_CANCELLED"


def test_worker_persists_bootstrap_failure_when_input_changed(
    tmp_path: Path,
) -> None:
    store = _job(tmp_path)
    task = store.read_spec().project_root / "tasks/task.yaml"
    task.write_text("task_id: changed-after-submit\n", encoding="utf-8")

    exit_code = run_local_job(store.root)

    status = store.read_status()
    assert exit_code == 5
    assert status.state == JobState.FAILED
    assert status.terminal_code == "JOB_BOOTSTRAP_FAILED"
    assert status.worker is not None
    assert "job input changed" in (
        store.root / "worker.stderr.log"
    ).read_text(encoding="utf-8")


def test_worker_rejects_launch_for_terminal_job(tmp_path: Path) -> None:
    store = _job(tmp_path)
    store.update_status(
        state=JobState.COMPLETED,
        terminal_code="CLI_EXIT_0",
    )

    try:
        run_local_job(store.root)
    except RuntimeError as error:
        assert "JOB_WORKER_NOT_SUBMITTED" in str(error)
    else:
        raise AssertionError("terminal job was launched again")

    assert store.read_status().state == JobState.COMPLETED


def test_worker_treats_activity_status_write_failure_as_fatal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _job(tmp_path)
    original = LocalJobStore.update_status
    failure_count = 0

    def fail_child_record(self, **updates):
        nonlocal failure_count
        if updates.get("current_child") is not None:
            failure_count += 1
            raise OSError("simulated status write failure")
        return original(self, **updates)

    monkeypatch.setattr(LocalJobStore, "update_status", fail_child_record)

    def fake_cli(argv, *, activity_reporter) -> int:
        del argv
        activity_reporter.emit(
            kind="command",
            state="started",
            source="model",
            pid=os.getpid(),
        )
        return 0

    exit_code = run_local_job(store.root, cli_runner=fake_cli)

    status = store.read_status()
    assert exit_code == 5
    assert failure_count == 3
    assert status.state == JobState.FAILED
    assert status.terminal_code == "JOB_STATUS_WRITE_FAILED"


def test_worker_heartbeat_status_failure_cancels_work_and_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _job(tmp_path)
    original = LocalJobStore.update_status
    failure_count = 0

    def fail_heartbeat(self, **updates):
        nonlocal failure_count
        if (
            current_thread().name.startswith("foampilot-job-heartbeat-")
            and updates.get("last_heartbeat_at") is not None
        ):
            failure_count += 1
            raise OSError("simulated heartbeat write failure")
        return original(self, **updates)

    monkeypatch.setattr(LocalJobStore, "update_status", fail_heartbeat)

    def wait_for_control_failure(argv, *, activity_reporter) -> int:
        del argv
        deadline = time.monotonic() + 2
        while not activity_reporter.is_cancel_requested():
            assert time.monotonic() < deadline
            time.sleep(0.005)
        return 0

    exit_code = run_local_job(
        store.root,
        cli_runner=wait_for_control_failure,
        heartbeat_seconds=0.01,
    )

    status = store.read_status()
    assert exit_code == 5
    assert failure_count == 3
    assert status.state == JobState.FAILED
    assert status.terminal_code == "JOB_STATUS_WRITE_FAILED"


def test_worker_running_status_failure_prevents_cli_and_terminalizes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _job(tmp_path)
    original = LocalJobStore.update_status
    attempts = 0
    cli_called = False

    def fail_running(self, **updates):
        nonlocal attempts
        if updates.get("state") == JobState.RUNNING:
            attempts += 1
            raise OSError("simulated RUNNING write failure")
        return original(self, **updates)

    def forbidden_cli(argv, *, activity_reporter):
        nonlocal cli_called
        del argv, activity_reporter
        cli_called = True
        return 0

    monkeypatch.setattr(LocalJobStore, "update_status", fail_running)

    exit_code = run_local_job(store.root, cli_runner=forbidden_cli)

    assert exit_code == 5
    assert attempts == 3
    assert cli_called is False
    assert store.read_status().state == JobState.FAILED
    assert store.read_status().terminal_code == "JOB_STATUS_WRITE_FAILED"
    failure = json.loads(
        (store.root / "worker-control-failure.json").read_text()
    )
    assert failure["code"] == "JOB_STATUS_WRITE_FAILED"


def test_worker_terminal_status_failure_is_explicit_and_durable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _job(tmp_path)
    original = LocalJobStore.update_status
    attempts = 0

    def fail_completed(self, **updates):
        nonlocal attempts
        if updates.get("state") == JobState.COMPLETED:
            attempts += 1
            raise OSError("simulated terminal write failure")
        return original(self, **updates)

    monkeypatch.setattr(LocalJobStore, "update_status", fail_completed)

    exit_code = run_local_job(
        store.root,
        cli_runner=lambda argv, *, activity_reporter: 0,
    )

    assert exit_code == 5
    assert attempts == 3
    assert store.read_status().state == JobState.FAILED
    assert store.read_status().terminal_code == "JOB_STATUS_WRITE_FAILED"
    failure = json.loads(
        (store.root / "worker-control-failure.json").read_text()
    )
    assert failure["code"] == "JOB_STATUS_WRITE_FAILED"
