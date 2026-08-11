"""Fixed-argv QProcess boundary for interactive desktop jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from foampilot.activity import ActivityEvent
from foampilot.jobs import (
    JobOperation,
    JobState,
    LocalJobStore,
    build_job_spec,
    launch_local_job,
    process_identity_matches,
)


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
    activity_received = Signal(object)
    job_status_changed = Signal(object)
    job_health_changed = Signal(str)
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
        heartbeat_stale_seconds: float = 5.0,
    ) -> None:
        super().__init__(parent)
        if discovery_interval_ms < 1:
            raise ValueError("discovery interval must be positive")
        if heartbeat_stale_seconds <= 0:
            raise ValueError("heartbeat stale threshold must be positive")
        self.program = program or sys.executable
        self.prefix_args = prefix_args
        self.allowed_commands = frozenset(allowed_commands)
        self.heartbeat_stale_seconds = heartbeat_stale_seconds
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
        self.job_poll_timer = QTimer(self)
        self.job_poll_timer.setInterval(discovery_interval_ms)
        self.job_poll_timer.timeout.connect(self._poll_job)
        self._arguments: tuple[str, ...] = ()
        self._run_root: Path | None = None
        self._discovered_run: Path | None = None
        self._stderr_buffer = ""
        self._active_store: LocalJobStore | None = None
        self._job_event_offset = 0
        self._job_event_buffer = ""
        self._job_log_offsets = {"stdout": 0, "stderr": 0}
        self._last_job_revision = 0
        self._terminal_emitted = False
        self._last_job_health = ""

    @property
    def is_running(self) -> bool:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return True
        if self._active_store is None:
            return False
        try:
            return self._active_store.read_status().state in {
                JobState.SUBMITTED,
                JobState.STARTING,
                JobState.RUNNING,
                JobState.CANCEL_REQUESTED,
                JobState.CANCELLING,
            }
        except (OSError, ValueError):
            return False

    @property
    def current_run_dir(self) -> Path | None:
        return self._discovered_run

    @property
    def current_job_dir(self) -> Path | None:
        return self._active_store.root if self._active_store is not None else None

    @property
    def current_arguments(self) -> tuple[str, ...]:
        return self._arguments

    def start_cli(
        self,
        arguments: list[str] | tuple[str, ...],
        *,
        run_root: str | Path | None = None,
        project_root: str | Path | None = None,
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
        self._stderr_buffer = ""
        operation = self._job_operation(normalized)
        if resolved_root is not None and operation is not None:
            project = (
                Path(project_root).resolve()
                if project_root is not None
                else resolved_root.parent.parent.resolve()
            )
            store = LocalJobStore(resolved_root)
            store.create(
                build_job_spec(
                    job_root=resolved_root,
                    project_root=project,
                    operation=operation,
                    arguments=normalized,
                )
            )
            store.initialize_status()
            self._bind_store(store)
            launch_local_job(store.root, program=self.program)
            self.job_poll_timer.start()
            self.job_started.emit(operation.value)
            return
        self.process.setProgram(self.program)
        self.process.setArguments([*self.prefix_args, *normalized])
        self.process.start()

    @staticmethod
    def _job_operation(arguments: tuple[str, ...]) -> JobOperation | None:
        if arguments[:2] == ("task", "draft"):
            return JobOperation.DRAFT
        if arguments and arguments[0] == "plan":
            return JobOperation.PLAN
        if arguments and arguments[0] == "solve":
            return JobOperation.SOLVE
        if arguments and arguments[0] == "resume":
            return JobOperation.RESUME
        return None

    def _bind_store(self, store: LocalJobStore) -> None:
        self._active_store = store
        self._job_event_offset = 0
        self._job_event_buffer = ""
        self._job_log_offsets = {"stdout": 0, "stderr": 0}
        self._last_job_revision = 0
        self._terminal_emitted = False
        self._last_job_health = ""

    def attach_job(self, job_root: str | Path) -> None:
        if self.is_running:
            raise DesktopJobError(
                "DESKTOP_JOB_BUSY: another desktop CLI job is running"
            )
        store = LocalJobStore(job_root)
        spec = store.read_spec()
        status = store.read_status()
        if status.worker is not None and status.state in {
            JobState.STARTING,
            JobState.RUNNING,
            JobState.CANCEL_REQUESTED,
            JobState.CANCELLING,
        } and not process_identity_matches(status.worker):
            raise DesktopJobError(
                "JOB_WORKER_IDENTITY_MISMATCH: worker is no longer owned"
            )
        self._arguments = spec.arguments
        self._run_root = store.root
        self._discovered_run = None
        self._bind_store(store)
        self.job_poll_timer.start()
        self.job_started.emit(spec.operation.value)
        self._poll_job()

    def attach_latest(self, runs_root: str | Path) -> Path | None:
        root = Path(runs_root).resolve()
        candidates = [
            path
            for path in sorted(root.glob("job-*"), reverse=True)
            if path.is_dir()
            and not path.is_symlink()
            and (path / "job.json").is_file()
            and (path / "job-status.json").is_file()
        ]
        for path in candidates:
            try:
                status = LocalJobStore(path).read_status()
            except (OSError, ValueError):
                continue
            if status.state in {
                JobState.SUBMITTED,
                JobState.STARTING,
                JobState.RUNNING,
                JobState.CANCEL_REQUESTED,
                JobState.CANCELLING,
            }:
                self.attach_job(path)
                return path.resolve()
        return None

    def request_cancel(self) -> None:
        if self._active_store is None or not self.is_running:
            raise DesktopJobError("DESKTOP_JOB_NOT_RUNNING: no active job")
        self._active_store.request_cancel(requested_by="desktop")
        self._poll_job()

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
            self._consume_stderr(data.decode("utf-8", errors="replace"))

    def _consume_stderr(self, text: str) -> None:
        self._stderr_buffer += text
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            self._consume_stderr_line(line.rstrip("\r"))

    def _consume_stderr_line(self, line: str) -> None:
        try:
            event = ActivityEvent.model_validate_json(line)
        except (ValueError, TypeError):
            self.output_received.emit("stderr", line + "\n")
            return
        self.activity_received.emit(event)

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

    def _read_job_events(self) -> None:
        if self._active_store is None:
            return
        path = self._active_store.root / "job-events.jsonl"
        if not path.is_file() or path.is_symlink():
            return
        size = path.stat().st_size
        if size < self._job_event_offset:
            self._job_event_offset = 0
            self._job_event_buffer = ""
        with path.open("rb") as stream:
            stream.seek(self._job_event_offset)
            payload = stream.read()
        self._job_event_offset += len(payload)
        self._job_event_buffer += payload.decode("utf-8", errors="replace")
        while "\n" in self._job_event_buffer:
            line, self._job_event_buffer = self._job_event_buffer.split("\n", 1)
            try:
                event = ActivityEvent.model_validate_json(line)
            except ValueError:
                self.output_received.emit("stderr", line + "\n")
                continue
            self.activity_received.emit(event)

    def _read_job_log(self, channel: str) -> None:
        if self._active_store is None:
            return
        path = self._active_store.root / f"worker.{channel}.log"
        if not path.is_file() or path.is_symlink():
            return
        offset = self._job_log_offsets[channel]
        size = path.stat().st_size
        if size < offset:
            offset = 0
        with path.open("rb") as stream:
            stream.seek(offset)
            payload = stream.read()
        self._job_log_offsets[channel] = offset + len(payload)
        if payload:
            self.output_received.emit(
                channel, payload.decode("utf-8", errors="replace")
            )

    def _poll_job(self) -> None:
        store = self._active_store
        if store is None:
            self.job_poll_timer.stop()
            return
        try:
            status = store.read_status()
            self._read_job_events()
            self._read_job_log("stdout")
            self._read_job_log("stderr")
        except (OSError, ValueError) as error:
            self.job_error.emit("DESKTOP_JOB_STATUS_INVALID", str(error))
            return
        if status.revision > self._last_job_revision:
            self._last_job_revision = status.revision
            self.job_status_changed.emit(status)
        health = status.state.value
        if status.state in {
            JobState.STARTING,
            JobState.RUNNING,
            JobState.CANCEL_REQUESTED,
            JobState.CANCELLING,
        }:
            heartbeat = status.last_heartbeat_at
            if heartbeat is None or (
                datetime.now(timezone.utc) - heartbeat
            ).total_seconds() > self.heartbeat_stale_seconds:
                health = "UNRESPONSIVE"
        if health != self._last_job_health:
            self._last_job_health = health
            self.job_health_changed.emit(health)
        if status.run_dir is not None and self._discovered_run is None:
            run = store.root / status.run_dir
            if run.is_dir() and not run.is_symlink():
                self._discovered_run = run.resolve()
                self.run_discovered.emit(self._discovered_run)
        elif self._discovered_run is None:
            self._discover_run()
        if status.state in {
            JobState.CANCELLED,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.INTERRUPTED,
        } and not self._terminal_emitted:
            self._terminal_emitted = True
            self.job_poll_timer.stop()
            if status.state == JobState.CANCELLED:
                exit_code, exit_status = 130, "cancelled"
            elif status.state == JobState.FAILED:
                exit_code, exit_status = 5, "crashed"
            elif status.state == JobState.INTERRUPTED:
                exit_code, exit_status = 5, "interrupted"
            else:
                raw = status.terminal_code or "CLI_EXIT_5"
                try:
                    exit_code = int(raw.removeprefix("CLI_EXIT_"))
                except ValueError:
                    exit_code = 5
                exit_status = "normal"
            self.job_finished.emit(exit_code, exit_status)

    def _on_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        self._read_stdout()
        self._read_stderr()
        if self._stderr_buffer:
            self.output_received.emit("stderr", self._stderr_buffer)
            self._stderr_buffer = ""
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
