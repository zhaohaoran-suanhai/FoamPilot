from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from foampilot.activity import (
    ActivityEvent,
    ActivityReporter,
    run_supervised_process,
)


def _reporter() -> tuple[ActivityReporter, list[ActivityEvent]]:
    seen: list[ActivityEvent] = []
    return ActivityReporter(operation_id="op-1", listeners=[seen.append]), seen


def test_silent_child_emits_heartbeat_before_completion() -> None:
    reporter, seen = _reporter()

    result = run_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(0.12)"],
        timeout_seconds=1,
        heartbeat_seconds=0.03,
        reporter=reporter,
        source="model",
        stage="generation",
    )

    assert result.returncode == 0
    assert not result.timed_out
    assert seen[0].state == "started"
    assert any(event.kind == "heartbeat" for event in seen)
    assert seen[-1].state == "completed"
    assert all(event.pid == result.pid for event in seen)


def test_supervised_process_calls_tick_during_run_and_at_completion() -> None:
    ticks: list[tuple[float, int]] = []

    result = run_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(0.08)"],
        timeout_seconds=1,
        heartbeat_seconds=0.02,
        source="runner",
        on_tick=lambda elapsed, pid: ticks.append((elapsed, pid)),
    )

    assert result.returncode == 0
    assert len(ticks) >= 2
    assert all(pid == result.pid for _, pid in ticks)
    assert ticks == sorted(ticks)


def test_child_timeout_is_reported_and_reaped() -> None:
    reporter, seen = _reporter()

    result = run_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        timeout_seconds=0.08,
        heartbeat_seconds=0.02,
        reporter=reporter,
        source="runner",
        stage="solve",
    )

    assert result.timed_out
    assert result.returncode is None
    assert seen[-1].state == "timed_out"
    with pytest.raises(ProcessLookupError):
        os.kill(result.pid, 0)


def test_supervised_process_captures_output_and_sends_stdin() -> None:
    result = run_supervised_process(
        [
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.read(); print(data.upper()); "
            "print('warning', file=sys.stderr)",
        ],
        timeout_seconds=1,
        stdin_text="hello",
        source="model",
    )

    assert result.stdout == "HELLO\n"
    assert result.stderr == "warning\n"


def test_supervised_process_writes_to_explicit_log_streams(tmp_path: Path) -> None:
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        result = run_supervised_process(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            timeout_seconds=1,
            stdout=stdout,
            stderr=stderr,
            source="runner",
        )

    assert result.returncode == 0
    assert result.stdout is None
    assert result.stderr is None
    assert stdout_path.read_text(encoding="utf-8") == "out\n"
    assert stderr_path.read_text(encoding="utf-8") == "err\n"


def test_supervised_process_rejects_shell_string() -> None:
    with pytest.raises(TypeError, match="argv must be a sequence"):
        run_supervised_process(
            "echo unsafe",
            timeout_seconds=1,
            source="runner",
        )


def test_supervised_process_reports_launch_failure() -> None:
    reporter, seen = _reporter()

    with pytest.raises(FileNotFoundError):
        run_supervised_process(
            ["/definitely/missing/foampilot-command"],
            timeout_seconds=1,
            reporter=reporter,
            source="runner",
        )

    assert seen[-1].state == "failed"
    assert seen[-1].detail_code == "PROCESS_LAUNCH_FAILED"
