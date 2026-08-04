"""Direct execution of safety-validated typed OpenFOAM commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import subprocess

from foampilot.plans import NativeCommand
from foampilot.tasks import ResourceBudget

from .models import (
    PlanRunResult,
    PlanStepResult,
    RuntimeConfig,
)
from .sandbox import build_sandbox_prefix
from .sandbox import probe_bubblewrap


_SOURCE_AND_EXEC = (
    'source "$1" >/dev/null 2>&1; shift; cd /case; exec "$@"'
)
_SOURCE_AND_EXEC_HOST = (
    'source "$1" >/dev/null 2>&1; shift; cd "$1"; shift; exec "$@"'
)
_SHELL_TOKENS = {"&&", "||", ";", "|", "<", ">"}
_SHELL_MARKERS = ("$(", "`", "\n", "\r", "\0")
_MPI_HOST_OPTIONS = {
    "--host",
    "--hostfile",
    "-host",
    "-hostfile",
}
_FAILED_MESH_CHECKS = re.compile(
    r"\bFailed\s+[1-9]\d*\s+mesh checks?\b"
)
Executor = Callable[..., subprocess.CompletedProcess[str]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlanRunner:
    """Execute typed steps without accepting an Agent-authored shell."""

    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig,
        available_executables: set[str],
        workspace_root: str | Path,
        executor: Executor = subprocess.run,
    ) -> None:
        self.runtime_config = runtime_config
        self.available_executables = frozenset(available_executables)
        self.workspace_root = Path(workspace_root).resolve()
        self.executor = executor

    @classmethod
    def from_runtime_config(
        cls,
        runtime_config: RuntimeConfig,
        available_executables: set[str],
        *,
        workspace_root: str | Path,
    ) -> "PlanRunner":
        return cls(
            runtime_config=runtime_config,
            available_executables=available_executables,
            workspace_root=workspace_root,
        )

    def _validate_argument(self, argument: str) -> None:
        if argument in _SHELL_TOKENS or any(
            marker in argument for marker in _SHELL_MARKERS
        ):
            raise ValueError("shell syntax is forbidden in typed arguments")
        if any(character in argument for character in (";", "|", "<", ">")):
            raise ValueError("shell syntax is forbidden in typed arguments")
        path = PurePosixPath(argument)
        if ".." in path.parts:
            raise ValueError("parent traversal is forbidden in typed arguments")
        if path.is_absolute() and not (
            argument == "/case" or argument.startswith("/case/")
        ):
            raise ValueError("absolute path outside /case is forbidden")

    def _validate_commands(
        self,
        commands: Sequence[NativeCommand],
        budget: ResourceBudget,
    ) -> None:
        if not commands:
            raise ValueError("execution plan has no commands")
        if sum(command.timeout_seconds for command in commands) > (
            budget.max_wall_seconds
        ):
            raise ValueError("command timeout budget exceeds task wall budget")
        rank_limit = min(
            budget.max_mpi_ranks,
            self.runtime_config.max_mpi_ranks,
        )
        for command in commands:
            if command.executable not in self.available_executables:
                raise ValueError(
                    f"executable is not available: {command.executable}"
                )
            if command.mpi_ranks > rank_limit:
                raise ValueError(
                    f"MPI rank request {command.mpi_ranks} exceeds "
                    f"limit {rank_limit}"
                )
            if any(
                argument in _MPI_HOST_OPTIONS
                or any(
                    argument.startswith(f"{option}=")
                    for option in _MPI_HOST_OPTIONS
                )
                for argument in command.args
            ):
                raise ValueError("MPI host selection is forbidden")
            if command.mpi_ranks == 1 and "-parallel" in command.args:
                raise ValueError("serial steps must not use -parallel")
            for argument in command.args:
                self._validate_argument(argument)

    @staticmethod
    def _typed_argv(command: NativeCommand) -> list[str]:
        if command.mpi_ranks == 1:
            return [command.executable, *command.args]
        arguments = [
            argument
            for argument in command.args
            if argument != "-parallel"
        ]
        return [
            "mpirun",
            "-n",
            str(command.mpi_ranks),
            command.executable,
            *arguments,
            "-parallel",
        ]

    def _sandbox_command(
        self,
        *,
        case_dir: Path,
        command: NativeCommand,
        budget: ResourceBudget,
    ) -> tuple[list[str], list[str]]:
        typed = self._typed_argv(command)
        project = str(self.runtime_config.openfoam_root.resolve())
        full = build_sandbox_prefix(
            bubblewrap=self.runtime_config.bubblewrap,
            openfoam_root=self.runtime_config.openfoam_root,
            case_dir=case_dir,
            memory_mib=budget.memory_mib,
            cpu_seconds=command.timeout_seconds,
        ) + [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            _SOURCE_AND_EXEC,
            "foampilot",
            f"{project}/etc/bashrc",
            *typed,
        ]
        return full, typed

    def _host_command(
        self,
        *,
        case_dir: Path,
        command: NativeCommand,
        budget: ResourceBudget,
    ) -> tuple[list[str], list[str]]:
        typed = self._typed_argv(command)
        address_space = budget.memory_mib * 1024 * 1024
        full = [
            "/usr/bin/prlimit",
            f"--cpu={command.timeout_seconds}",
            f"--as={address_space}",
            "--",
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            _SOURCE_AND_EXEC_HOST,
            "foampilot",
            str(self.runtime_config.openfoam_root / "etc" / "bashrc"),
            str(case_dir),
            *typed,
        ]
        return full, typed

    def _execution_backend(self) -> tuple[str, str | None]:
        requested = self.runtime_config.execution_backend
        if requested != "auto":
            return requested, None
        ok, detail = probe_bubblewrap(self.runtime_config.bubblewrap)
        if ok:
            return "bubblewrap", None
        return "host", detail

    @staticmethod
    def _host_environment(case: Path) -> dict[str, str]:
        home = case / ".foampilot/host-home"
        temporary = case / ".foampilot/tmp"
        home.mkdir(parents=True, exist_ok=True)
        temporary.mkdir(parents=True, exist_ok=True)
        return {
            "HOME": str(home),
            "USER": "agent",
            "LOGNAME": "agent",
            "TMPDIR": str(temporary),
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        }

    def run(
        self,
        *,
        case_dir: str | Path,
        commands: Sequence[NativeCommand],
        budget: ResourceBudget,
    ) -> PlanRunResult:
        case = Path(case_dir).resolve()
        if (
            not case.is_relative_to(self.workspace_root)
            or not case.is_dir()
        ):
            raise ValueError(
                "case is missing or outside runner workspace"
            )
        self._validate_commands(commands, budget)
        log_directory = case / ".foampilot/logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        steps: list[PlanStepResult] = []
        failed_step_id: str | None = None
        run_timed_out = False
        execution_backend, fallback_reason = self._execution_backend()

        for index, command in enumerate(commands, start=1):
            stdout_path = (
                log_directory
                / f"{index:02d}-{command.step_id}.stdout.log"
            )
            stderr_path = (
                log_directory
                / f"{index:02d}-{command.step_id}.stderr.log"
            )
            if execution_backend == "bubblewrap":
                full_argv, typed_argv = self._sandbox_command(
                    case_dir=case,
                    command=command,
                    budget=budget,
                )
                executor_options = {}
            else:
                full_argv, typed_argv = self._host_command(
                    case_dir=case,
                    command=command,
                    budget=budget,
                )
                executor_options = {
                    "cwd": case,
                    "env": self._host_environment(case),
                }
            started = _utc_now()
            timed_out = False
            return_code: int | None
            with stdout_path.open(
                "w",
                encoding="utf-8",
            ) as stdout_stream, stderr_path.open(
                "w",
                encoding="utf-8",
            ) as stderr_stream:
                try:
                    completed = self.executor(
                        full_argv,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                        check=False,
                        timeout=command.timeout_seconds,
                        text=True,
                        shell=False,
                        **executor_options,
                    )
                    return_code = completed.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    return_code = None
            semantic_failure = False
            if (
                command.executable == "checkMesh"
                and return_code == 0
                and not timed_out
            ):
                check_text = "\n".join(
                    (
                        stdout_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        ),
                        stderr_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        ),
                    )
                )
                semantic_failure = bool(
                    _FAILED_MESH_CHECKS.search(check_text)
                )
            step = PlanStepResult(
                step_id=command.step_id,
                command=typed_argv,
                return_code=return_code,
                started_at=started,
                finished_at=_utc_now(),
                timed_out=timed_out,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                execution_backend=execution_backend,
                backend_fallback_reason=fallback_reason,
            )
            steps.append(step)
            if timed_out or return_code != 0 or semantic_failure:
                failed_step_id = command.step_id
                run_timed_out = timed_out
                break

        return PlanRunResult(
            case_dir=case,
            steps=steps,
            failed_step_id=failed_step_id,
            timed_out=run_timed_out,
        )
