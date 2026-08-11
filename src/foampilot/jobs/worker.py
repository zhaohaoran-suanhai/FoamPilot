"""One detached, writer-locked worker for one durable local job."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

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


class JobStatusWriteError(RuntimeError):
    """A critical durable control-plane update could not be persisted."""


_STATUS_WRITE_ATTEMPTS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _record_control_failure(
    store: LocalJobStore,
    error: BaseException,
) -> None:
    """Write independent best-effort evidence if job-status persistence fails."""

    path = store.root / "worker-control-failure.json"
    try:
        payload = {
            "schema_version": 1,
            "job_id": store.read_spec().job_id,
            "code": "JOB_STATUS_WRITE_FAILED",
            "occurred_at": _utc_now().isoformat(),
            "detail": type(error).__name__,
        }
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (FileExistsError, OSError, ValueError):
        pass


def _critical_status_update(
    store: LocalJobStore,
    **updates: object,
):
    last_error: Exception | None = None
    for _ in range(_STATUS_WRITE_ATTEMPTS):
        try:
            return store.update_status(**updates)
        except (OSError, ValueError) as error:
            last_error = error
    assert last_error is not None
    _record_control_failure(store, last_error)
    raise JobStatusWriteError(
        "JOB_STATUS_WRITE_FAILED: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def _default_cli_runner(arguments, *, activity_reporter) -> int:
    from foampilot.cli.main import main

    return main(list(arguments), activity_reporter=activity_reporter)


class _StatusListener:
    def __init__(self, store: LocalJobStore) -> None:
        self.store = store
        self.cancelled_process = False
        self.failure: Exception | None = None

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
            try:
                _critical_status_update(self.store, **updates)
            except JobStatusWriteError as error:
                self.failure = error
                raise


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
    stdout_path = store.root / "worker.stdout.log"
    stderr_path = store.root / "worker.stderr.log"
    events_path = store.root / "job-events.jsonl"
    stop = Event()

    with store.writer_lock():
        initial_status = store.read_status()
        if initial_status.state != JobState.SUBMITTED:
            raise RuntimeError(
                "JOB_WORKER_NOT_SUBMITTED: durable worker can only start "
                "a newly submitted job"
            )
        worker = current_process_identity()
        now = _utc_now()
        try:
            _critical_status_update(
                store,
                state=JobState.STARTING,
                worker=worker,
                started_at=now,
                last_heartbeat_at=now,
            )
        except JobStatusWriteError as error:
            with stderr_path.open("a", encoding="utf-8") as stderr_stream:
                print(str(error), file=stderr_stream)
            try:
                _critical_status_update(
                    store,
                    state=JobState.FAILED,
                    current_child=None,
                    finished_at=_utc_now(),
                    terminal_code="JOB_STATUS_WRITE_FAILED",
                )
            except JobStatusWriteError:
                raise error
            return 5
        try:
            spec = store.read_spec()
            if spec.job_id != initial_status.job_id:
                raise ValueError("job receipt and status IDs do not match")
            store.verify_inputs()
        except Exception as error:
            with stderr_path.open("a", encoding="utf-8") as stderr_stream:
                print(
                    "JOB_BOOTSTRAP_FAILED: "
                    f"{type(error).__name__}: {error}",
                    file=stderr_stream,
                )
            finished = _utc_now()
            _critical_status_update(
                store,
                state=JobState.FAILED,
                current_child=None,
                finished_at=finished,
                last_heartbeat_at=finished,
                terminal_code="JOB_BOOTSTRAP_FAILED",
            )
            return 5

        status_listener = _StatusListener(store)
        status_failures: list[Exception] = []
        status_failure = Event()

        def cancellation_requested() -> bool:
            return status_failure.is_set() or store.cancel_requested

        reporter = ActivityReporter(
            operation_id=spec.job_id,
            listeners=[JsonlActivitySink(events_path)],
            critical_listeners=[status_listener],
            cancel_requested=cancellation_requested,
        )

        def heartbeat() -> None:
            while not stop.wait(heartbeat_seconds):
                try:
                    current = store.read_status()
                    if current.state in {
                        JobState.CANCELLED,
                        JobState.COMPLETED,
                        JobState.FAILED,
                        JobState.INTERRUPTED,
                    }:
                        return
                    next_state = current.state
                    if store.cancel_requested and current.state in {
                        JobState.STARTING,
                        JobState.RUNNING,
                    }:
                        next_state = JobState.CANCEL_REQUESTED
                    _critical_status_update(
                        store,
                        state=next_state,
                        last_heartbeat_at=_utc_now(),
                    )
                except JobStatusWriteError as error:
                    status_failures.append(error)
                    status_failure.set()
                    return

        heartbeat_thread = Thread(
            target=heartbeat,
            name=f"foampilot-job-heartbeat-{spec.job_id}",
            daemon=True,
        )
        exit_code = 5
        internal_error: Exception | None = None
        try:
            _critical_status_update(store, state=JobState.RUNNING)
        except JobStatusWriteError as error:
            status_failures.append(error)
            internal_error = error
        else:
            heartbeat_thread.start()
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

        if status_listener.failure is not None:
            status_failures.append(status_listener.failure)
        if status_failures:
            internal_error = JobStatusWriteError(
                "JOB_STATUS_WRITE_FAILED: "
                f"{type(status_failures[0]).__name__}: {status_failures[0]}"
            )
            exit_code = 5

        finished = _utc_now()
        if isinstance(internal_error, JobStatusWriteError):
            terminal_state = JobState.FAILED
            terminal_code = "JOB_STATUS_WRITE_FAILED"
        elif exit_code == 130 or status_listener.cancelled_process:
            terminal_state = JobState.CANCELLED
            terminal_code = "USER_CANCELLED"
        elif internal_error is not None or exit_code == 5:
            terminal_state = JobState.FAILED
            terminal_code = "JOB_WORKER_FAILED"
        else:
            terminal_state = JobState.COMPLETED
            terminal_code = f"CLI_EXIT_{exit_code}"
        try:
            _critical_status_update(
                store,
                state=terminal_state,
                current_child=None,
                finished_at=finished,
                last_heartbeat_at=finished,
                terminal_code=terminal_code,
            )
        except JobStatusWriteError as error:
            with stderr_path.open("a", encoding="utf-8") as stderr_stream:
                print(str(error), file=stderr_stream)
            raise
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
