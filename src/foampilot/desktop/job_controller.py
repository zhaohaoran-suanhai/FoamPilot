"""Fixed-argv QProcess boundary for interactive desktop jobs."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


class DesktopJobError(RuntimeError):
    """The desktop cannot safely start or bind a requested CLI job."""


_DEFAULT_COMMANDS = (
    "preflight",
    "model",
    "task",
    "validate",
    "solve",
    "report",
)


class DesktopJobController(QObject):
    """Run only registered FoamPilot CLI commands without a shell."""

    job_started = Signal(str)
    output_received = Signal(str, str)
    run_discovered = Signal(object)
    job_finished = Signal(int, str)
    job_error = Signal(str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        program: str | None = None,
        prefix_args: tuple[str, ...] = ("-m", "foampilot.cli.main"),
        allowed_commands: tuple[str, ...] = _DEFAULT_COMMANDS,
        discovery_interval_ms: int = 250,
    ) -> None:
        super().__init__(parent)
        if discovery_interval_ms < 1:
            raise ValueError("discovery interval must be positive")
        self.program = program or sys.executable
        self.prefix_args = prefix_args
        self.allowed_commands = frozenset(allowed_commands)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(
            QProcess.ProcessChannelMode.SeparateChannels
        )
        self.process.started.connect(self._on_started)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)
        self.discovery_timer = QTimer(self)
        self.discovery_timer.setInterval(discovery_interval_ms)
        self.discovery_timer.timeout.connect(self._discover_run)
        self._arguments: tuple[str, ...] = ()
        self._run_root: Path | None = None
        self._discovered_run: Path | None = None

    @property
    def is_running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    @property
    def current_run_dir(self) -> Path | None:
        return self._discovered_run

    def start_cli(
        self,
        arguments: list[str] | tuple[str, ...],
        *,
        run_root: str | Path | None = None,
    ) -> None:
        if self.is_running:
            raise DesktopJobError(
                "DESKTOP_JOB_BUSY: another desktop CLI job is running"
            )
        normalized = tuple(str(item) for item in arguments)
        if not normalized or normalized[0] not in self.allowed_commands:
            command = normalized[0] if normalized else "<empty>"
            raise DesktopJobError(
                f"DESKTOP_COMMAND_REJECTED: unregistered command {command}"
            )
        if any("\x00" in item for item in normalized):
            raise DesktopJobError(
                "DESKTOP_COMMAND_REJECTED: arguments contain a null byte"
            )
        resolved_root: Path | None = None
        if run_root is not None:
            source = Path(run_root)
            if source.is_symlink():
                raise DesktopJobError(
                    "DESKTOP_RUN_ROOT_INVALID: run root is a symbolic link"
                )
            resolved_root = source.resolve()
            if not resolved_root.is_dir():
                raise DesktopJobError(
                    f"DESKTOP_RUN_ROOT_INVALID: not a directory: {resolved_root}"
                )
        self._arguments = normalized
        self._run_root = resolved_root
        self._discovered_run = None
        self.process.setProgram(self.program)
        self.process.setArguments([*self.prefix_args, *normalized])
        self.process.start()

    def _on_started(self) -> None:
        if self._run_root is not None:
            self.discovery_timer.start()
            self._discover_run()
        self.job_started.emit(self._arguments[0])

    def _read_stdout(self) -> None:
        data = bytes(self.process.readAllStandardOutput())
        if data:
            self.output_received.emit(
                "stdout", data.decode("utf-8", errors="replace")
            )

    def _read_stderr(self) -> None:
        data = bytes(self.process.readAllStandardError())
        if data:
            self.output_received.emit(
                "stderr", data.decode("utf-8", errors="replace")
            )

    def _discover_run(self) -> None:
        if self._run_root is None or self._discovered_run is not None:
            return
        try:
            children = tuple(
                child.resolve()
                for child in sorted(
                    self._run_root.iterdir(), key=lambda item: item.name
                )
                if child.name.startswith("run-")
                and child.is_dir()
                and not child.is_symlink()
            )
        except OSError as error:
            self.job_error.emit("DESKTOP_RUN_DISCOVERY_FAILED", str(error))
            self.discovery_timer.stop()
            return
        if len(children) > 1:
            self.job_error.emit(
                "DESKTOP_RUN_DISCOVERY_AMBIGUOUS",
                f"unique job root contains {len(children)} runs",
            )
            self.discovery_timer.stop()
            return
        if children:
            self._discovered_run = children[0]
            self.discovery_timer.stop()
            self.run_discovered.emit(children[0])

    def _on_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        self._read_stdout()
        self._read_stderr()
        self._discover_run()
        self.discovery_timer.stop()
        status = (
            "normal"
            if exit_status == QProcess.ExitStatus.NormalExit
            else "crashed"
        )
        self.job_finished.emit(exit_code, status)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self.job_error.emit(
            "DESKTOP_PROCESS_FAILED",
            f"{error.name}: {self.process.errorString()}",
        )


__all__ = ["DesktopJobController", "DesktopJobError"]
