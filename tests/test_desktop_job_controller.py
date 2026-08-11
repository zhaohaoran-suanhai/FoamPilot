from __future__ import annotations

from pathlib import Path
import sys

import pytest

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
