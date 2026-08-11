from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Thread
import time

from foampilot.activity import OperationCancelled
from foampilot.jobs import (
    JobState,
    LocalJobStore,
    build_job_spec,
    run_local_job,
)


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
