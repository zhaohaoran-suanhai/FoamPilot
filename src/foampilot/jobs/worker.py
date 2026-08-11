"""One detached, writer-locked worker for one durable local job."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from typing import TextIO

from foampilot.activity import (
    ActivityEvent,
    ActivityReporter,
    JsonlActivitySink,
    OperationCancelled,
)

from .identity import current_process_identity, process_identity
from .models import JobState
from .store import LocalJobStore


CliRunner = Callable[..., int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_cli_runner(arguments, *, activity_reporter) -> int:
    from foampilot.cli.main import main

    return main(list(arguments), activity_reporter=activity_reporter)


class _StatusListener:
    def __init__(self, store: LocalJobStore) -> None:
        self.store = store
        self.cancelled_process = False

    def __call__(self, event: ActivityEvent) -> None:
        updates: dict[str, object] = {}
        if event.stage is not None:
            updates["current_stage"] = event.stage
        if event.step_id is not None:
            updates["current_step_id"] = event.step_id
        if event.run_id is not None:
            candidate = self.store.root / event.run_id
            if candidate.is_dir() and not candidate.is_symlink():
                updates["run_dir"] = event.run_id
        if event.kind == "command" and event.pid is not None:
            if event.state == "started":
                try:
                    updates["current_child"] = process_identity(event.pid)
                except (OSError, ValueError):
                    pass
            elif event.state in {
                "completed",
                "failed",
                "timed_out",
                "cancelled",
            }:
                updates["current_child"] = None
        if event.state == "cancelled":
            self.cancelled_process = True
            updates["state"] = JobState.CANCELLING
        if updates:
            self.store.update_status(**updates)


def run_local_job(
    job_root: str | Path,
    *,
    cli_runner: CliRunner = _default_cli_runner,
    heartbeat_seconds: float = 1.0,
) -> int:
    """Own one job, invoke the existing CLI service, and freeze its outcome."""

    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat interval must be positive")
    store = LocalJobStore(job_root)
    spec = store.read_spec()
    store.verify_inputs()
    stdout_path = store.root / "worker.stdout.log"
    stderr_path = store.root / "worker.stderr.log"
    events_path = store.root / "job-events.jsonl"
    stop = Event()

    with store.writer_lock():
        worker = current_process_identity()
        now = _utc_now()
        store.update_status(
            state=JobState.STARTING,
            worker=worker,
            started_at=now,
            last_heartbeat_at=now,
        )
        status_listener = _StatusListener(store)
        reporter = ActivityReporter(
            operation_id=spec.job_id,
            listeners=[JsonlActivitySink(events_path), status_listener],
            cancel_requested=lambda: store.cancel_requested,
        )

        def heartbeat() -> None:
            while not stop.wait(heartbeat_seconds):
                current = store.read_status()
                if current.state in {
                    JobState.CANCELLED,
                    JobState.COMPLETED,
                    JobState.FAILED,
                }:
                    return
                next_state = current.state
                if store.cancel_requested and current.state in {
                    JobState.STARTING,
                    JobState.RUNNING,
                }:
                    next_state = JobState.CANCEL_REQUESTED
                store.update_status(
                    state=next_state,
                    last_heartbeat_at=_utc_now(),
                )

        heartbeat_thread = Thread(
            target=heartbeat,
            name=f"foampilot-job-heartbeat-{spec.job_id}",
            daemon=True,
        )
        store.update_status(state=JobState.RUNNING)
        heartbeat_thread.start()
        exit_code = 5
        internal_error: Exception | None = None
        try:
            with stdout_path.open(
                "a", encoding="utf-8", buffering=1
            ) as stdout_stream, stderr_path.open(
                "a", encoding="utf-8", buffering=1
            ) as stderr_stream:
                with redirect_stdout(stdout_stream), redirect_stderr(
                    stderr_stream
                ):
                    try:
                        reporter.raise_if_cancelled()
                        exit_code = cli_runner(
                            spec.arguments,
                            activity_reporter=reporter,
                        )
                    except OperationCancelled:
                        exit_code = 130
                    except Exception as error:
                        internal_error = error
                        print(
                            "JOB_WORKER_INTERNAL_ERROR: "
                            f"{type(error).__name__}: {error}",
                            file=sys.stderr,
                        )
                        exit_code = 5
        finally:
            stop.set()
            heartbeat_thread.join(timeout=heartbeat_seconds + 0.5)

        finished = _utc_now()
        if exit_code == 130 or status_listener.cancelled_process:
            terminal_state = JobState.CANCELLED
            terminal_code = "USER_CANCELLED"
        elif internal_error is not None or exit_code == 5:
            terminal_state = JobState.FAILED
            terminal_code = "JOB_WORKER_FAILED"
        else:
            terminal_state = JobState.COMPLETED
            terminal_code = f"CLI_EXIT_{exit_code}"
        store.update_status(
            state=terminal_state,
            current_child=None,
            finished_at=finished,
            last_heartbeat_at=finished,
            terminal_code=terminal_code,
        )
        return exit_code


def launch_local_job(
    job_root: str | Path,
    *,
    program: str | None = None,
) -> int:
    """Start a detached worker and return its PID without owning its lifetime."""

    root = LocalJobStore(job_root).root
    process = subprocess.Popen(
        [
            program or sys.executable,
            "-m",
            "foampilot.cli.main",
            "worker",
            "run",
            str(root),
        ],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return process.pid


__all__ = ["launch_local_job", "run_local_job"]
