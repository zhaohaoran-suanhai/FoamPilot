"""Polling subprocess execution with truthful liveness events."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import TextIO

from .models import ActivitySource
from .reporter import ActivityReporter


@dataclass(frozen=True)
class SupervisedProcessResult:
    returncode: int | None
    stdout: str | None
    stderr: str | None
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float
    timed_out: bool
    pid: int


PopenFactory = Callable[..., subprocess.Popen[str]]
TickCallback = Callable[[float, int], None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _kill_owned_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()


def run_supervised_process(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    source: ActivitySource | str,
    reporter: ActivityReporter | None = None,
    stage: str | None = None,
    step_id: str | None = None,
    attempt: int | None = None,
    stdin_text: str | None = None,
    stdout: int | TextIO | None = subprocess.PIPE,
    stderr: int | TextIO | None = subprocess.PIPE,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    heartbeat_seconds: float = 5.0,
    popen_factory: PopenFactory = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime] = _utc_now,
    on_tick: TickCallback | None = None,
) -> SupervisedProcessResult:
    """Run fixed argv, emit heartbeat while silent, and always reap the child."""

    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of arguments, not a shell string")
    normalized = [str(argument) for argument in argv]
    if not normalized:
        raise ValueError("argv must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be positive")

    started_at = utc_now()
    started_mono = monotonic()
    try:
        process = popen_factory(
            normalized,
            shell=False,
            text=True,
            stdin=(subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL),
            stdout=stdout,
            stderr=stderr,
            cwd=Path(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        if reporter is not None:
            reporter.emit(
                kind="command",
                state="failed",
                source=source,
                elapsed_seconds=max(monotonic() - started_mono, 0),
                deadline_seconds=timeout_seconds,
                attempt=attempt,
                stage=stage,
                step_id=step_id,
                detail_code="PROCESS_LAUNCH_FAILED",
                message=f"process launch failed: {type(error).__name__}",
            )
        raise

    if reporter is not None:
        reporter.emit(
            kind="command",
            state="started",
            source=source,
            elapsed_seconds=0,
            deadline_seconds=timeout_seconds,
            attempt=attempt,
            stage=stage,
            step_id=step_id,
            pid=process.pid,
            message="external process started",
        )

    def notify_tick(elapsed: float) -> None:
        if on_tick is None:
            return
        try:
            on_tick(elapsed, process.pid)
        except Exception as error:
            if reporter is not None:
                reporter.emit(
                    kind="warning",
                    state="failed",
                    source=source,
                    elapsed_seconds=elapsed,
                    deadline_seconds=timeout_seconds,
                    attempt=attempt,
                    stage=stage,
                    step_id=step_id,
                    pid=process.pid,
                    detail_code="OBSERVABILITY_DEGRADED",
                    message=f"activity tick failed: {type(error).__name__}",
                )

    pending_input = stdin_text
    captured_stdout: str | None = None
    captured_stderr: str | None = None
    timed_out = False
    while True:
        elapsed = max(monotonic() - started_mono, 0)
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            _kill_owned_process_group(process)
            captured_stdout, captured_stderr = process.communicate()
            timed_out = True
            break
        try:
            captured_stdout, captured_stderr = process.communicate(
                input=pending_input,
                timeout=min(heartbeat_seconds, remaining),
            )
            break
        except subprocess.TimeoutExpired:
            pending_input = None
            elapsed = max(monotonic() - started_mono, 0)
            if elapsed >= timeout_seconds:
                _kill_owned_process_group(process)
                captured_stdout, captured_stderr = process.communicate()
                timed_out = True
                break
            if reporter is not None:
                reporter.emit(
                    kind="heartbeat",
                    state="alive",
                    source=source,
                    elapsed_seconds=elapsed,
                    deadline_seconds=timeout_seconds,
                    attempt=attempt,
                    stage=stage,
                    step_id=step_id,
                    pid=process.pid,
                    message="external process is still running",
                )
            notify_tick(elapsed)

    finished_at = utc_now()
    elapsed_seconds = max(monotonic() - started_mono, 0)
    notify_tick(elapsed_seconds)
    returncode = None if timed_out else process.returncode
    if reporter is not None:
        reporter.emit(
            kind="command",
            state=(
                "timed_out"
                if timed_out
                else ("completed" if returncode == 0 else "failed")
            ),
            source=source,
            elapsed_seconds=elapsed_seconds,
            deadline_seconds=timeout_seconds,
            attempt=attempt,
            stage=stage,
            step_id=step_id,
            pid=process.pid,
            detail_code=(
                "PROCESS_TIMEOUT"
                if timed_out
                else ("PROCESS_EXIT_NONZERO" if returncode != 0 else None)
            ),
            message=(
                "external process timed out"
                if timed_out
                else f"external process exited with return code {returncode}"
            ),
        )
    return SupervisedProcessResult(
        returncode=returncode,
        stdout=captured_stdout,
        stderr=captured_stderr,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=elapsed_seconds,
        timed_out=timed_out,
        pid=process.pid,
    )


__all__ = ["SupervisedProcessResult", "run_supervised_process"]
