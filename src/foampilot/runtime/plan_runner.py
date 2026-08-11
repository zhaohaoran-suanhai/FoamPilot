"""Direct execution of safety-validated typed OpenFOAM commands."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from foampilot.activity import ActivityReporter, run_supervised_process
from foampilot.environment.models import EnvironmentSnapshot
from foampilot.plans import NativeCommand
from foampilot.tasks import ResourceBudget
from foampilot.workflow import (
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowStore,
)

from .models import (
    ExecutionPolicyDecision,
    ExecutionRiskReport,
    PlanRunResult,
    PlanStepResult,
    RuntimeConfig,
    SandboxProbe,
)
from .policy import decide_execution_policy
from .risk import with_command_risk
from .sandbox import (
    SandboxBuildError,
    build_sandbox_argv,
    not_requested_probe,
    probe_sandbox,
)
from .telemetry import IncrementalOpenFOAMLogParser, ResidualMetric


_SOURCE_AND_EXEC_HOST = (
    'source "$1" >/dev/null 2>&1; shift; cd "$1"; shift; exec "$@"'
)
_SHELL_TOKENS = {"&&", "||", ";", "|", "<", ">"}
_SHELL_MARKERS = ("$(", "`", "\n", "\r", "\0")
_MPI_HOST_OPTIONS = {"--host", "--hostfile", "-host", "-hostfile"}
_CONTEXT_OVERRIDE_OPTIONS = {
    "-case",
    "--case",
    "-roots",
    "--roots",
    "-hostroots",
    "--hostroots",
}
_FAILED_MESH_CHECKS = re.compile(r"\bFailed\s+[1-9]\d*\s+mesh checks?\b")
Executor = Callable[..., subprocess.CompletedProcess[str]]
SandboxProbeCallable = Callable[..., SandboxProbe]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _StepLogObserver:
    def __init__(
        self,
        *,
        stdout_path: Path,
        evidence_path: str,
        reporter: ActivityReporter | None,
        attempt: int | None,
        stage: str,
        step_id: str,
    ) -> None:
        self.stdout_path = stdout_path
        self.evidence_path = evidence_path
        self.reporter = reporter
        self.attempt = attempt
        self.stage = stage
        self.step_id = step_id
        self.offset = 0
        self.parser = IncrementalOpenFOAMLogParser()

    def _metric(
        self,
        metric: ResidualMetric,
        *,
        elapsed: float,
        pid: int,
    ) -> None:
        if self.reporter is None:
            return
        values: dict[str, float | int | str] = {
            "field": metric.field,
            "initial_residual": metric.initial_residual,
            "final_residual": metric.final_residual,
            "solver_iterations": metric.solver_iterations,
        }
        if metric.simulation_time is not None:
            values["simulation_time"] = metric.simulation_time
        if metric.iteration is not None:
            values["iteration"] = metric.iteration
        self.reporter.emit(
            kind="metric",
            state="progressed",
            source="runner",
            elapsed_seconds=elapsed,
            attempt=self.attempt,
            stage=self.stage,
            step_id=self.step_id,
            pid=pid,
            message="OpenFOAM residual updated",
            metrics=values,
            evidence_path=self.evidence_path,
            evidence_offset=self.offset,
        )

    def poll(self, elapsed: float, pid: int) -> None:
        if self.reporter is None or not self.stdout_path.is_file():
            return
        size = self.stdout_path.stat().st_size
        if size <= self.offset:
            return
        with self.stdout_path.open("rb") as stream:
            stream.seek(self.offset)
            payload = stream.read(size - self.offset)
        self.offset = size
        self.reporter.emit(
            kind="log",
            state="progressed",
            source="runner",
            elapsed_seconds=elapsed,
            attempt=self.attempt,
            stage=self.stage,
            step_id=self.step_id,
            pid=pid,
            message="OpenFOAM stdout log grew",
            metrics={"new_bytes": len(payload)},
            evidence_path=self.evidence_path,
            evidence_offset=self.offset,
        )
        for metric in self.parser.feed(payload.decode("utf-8", errors="replace")):
            self._metric(metric, elapsed=elapsed, pid=pid)

    def finish(self, elapsed: float, pid: int) -> None:
        self.poll(elapsed, pid)
        for metric in self.parser.finish():
            self._metric(metric, elapsed=elapsed, pid=pid)


class RuntimeExecutionError(RuntimeError):
    def __init__(
        self,
        decision: ExecutionPolicyDecision,
        probe: SandboxProbe,
    ) -> None:
        super().__init__(decision.code)
        self.code = decision.code
        self.decision = decision
        self.probe = probe


class PlanRunner:
    """Execute typed steps after freezing one policy decision per attempt."""

    emits_live_workflow = True

    def __init__(
        self,
        *,
        runtime_config: RuntimeConfig,
        environment: EnvironmentSnapshot,
        available_executables: set[str],
        workspace_root: str | Path,
        executor: Executor = subprocess.run,
        sandbox_probe: SandboxProbeCallable = probe_sandbox,
        activity_reporter: ActivityReporter | None = None,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.runtime_config = runtime_config
        self.environment = environment
        approved_roots = (
            runtime_config.openfoam_root.resolve(),
            *(path.resolve() for path in runtime_config.trusted_readonly_roots),
        )
        command_paths: dict[str, Path] = {}
        for command in environment.commands:
            resolved = command.path.resolve()
            if not any(resolved.is_relative_to(root) for root in approved_roots):
                raise ValueError(
                    f"command path is outside approved runtime roots: {command.name}"
                )
            previous = command_paths.get(command.name)
            if previous is not None and previous != resolved:
                raise ValueError(
                    f"duplicate command name resolves to multiple paths: {command.name}"
                )
            command_paths[command.name] = resolved
        if environment.gmsh is not None:
            gmsh = environment.gmsh.resolve()
            if gmsh != Path("/usr/bin/gmsh"):
                raise ValueError("gmsh path is outside the approved system location")
            command_paths["gmsh"] = gmsh
        if environment.mpi_launcher is not None:
            mpi_launcher = environment.mpi_launcher.resolve()
            system_root = Path("/usr").resolve()
            if not (
                mpi_launcher.is_relative_to(system_root)
                or any(
                    mpi_launcher.is_relative_to(root)
                    for root in approved_roots
                )
            ):
                raise ValueError("MPI launcher path is outside approved runtime roots")
        self.command_paths = command_paths
        self.available_executables = frozenset(available_executables) & frozenset(
            command_paths
        )
        self.workspace_root = Path(workspace_root).resolve()
        self.executor = executor
        self.sandbox_probe = sandbox_probe
        self.activity_reporter = activity_reporter
        self.heartbeat_seconds = heartbeat_seconds

    @classmethod
    def from_runtime_config(
        cls,
        runtime_config: RuntimeConfig,
        available_executables: set[str],
        *,
        environment: EnvironmentSnapshot,
        workspace_root: str | Path,
        activity_reporter: ActivityReporter | None = None,
        heartbeat_seconds: float = 5.0,
    ) -> "PlanRunner":
        return cls(
            runtime_config=runtime_config,
            environment=environment,
            available_executables=available_executables,
            workspace_root=workspace_root,
            activity_reporter=activity_reporter,
            heartbeat_seconds=heartbeat_seconds,
        )

    def _validate_argument(self, argument: str) -> None:
        if argument in _SHELL_TOKENS or any(
            marker in argument for marker in _SHELL_MARKERS
        ):
            raise ValueError("shell syntax is forbidden in typed arguments")
        if any(character in argument for character in (";", "|", "<", ">")):
            raise ValueError("shell syntax is forbidden in typed arguments")
        values = [argument]
        if argument.startswith("-") and "=" in argument:
            values.append(argument.split("=", 1)[1])
        for value in values:
            path = PurePosixPath(value)
            if ".." in path.parts:
                raise ValueError("parent traversal is forbidden in typed arguments")
            if path.is_absolute():
                raise ValueError("absolute paths are forbidden in typed arguments")

    def _validate_commands(
        self,
        commands: Sequence[NativeCommand],
        budget: ResourceBudget,
    ) -> None:
        if not commands:
            raise ValueError("execution plan has no commands")
        if sum(command.timeout_seconds for command in commands) > budget.max_wall_seconds:
            raise ValueError("command timeout budget exceeds task wall budget")
        rank_limit = min(budget.max_mpi_ranks, self.runtime_config.max_mpi_ranks)
        for command in commands:
            if command.executable not in self.available_executables:
                raise ValueError(f"executable is not available: {command.executable}")
            if command.mpi_ranks > rank_limit:
                raise ValueError(
                    f"MPI rank request {command.mpi_ranks} exceeds limit {rank_limit}"
                )
            if any(
                argument in _MPI_HOST_OPTIONS
                or any(argument.startswith(f"{option}=") for option in _MPI_HOST_OPTIONS)
                for argument in command.args
            ):
                raise ValueError("MPI host selection is forbidden")
            if any(
                argument.casefold().split("=", 1)[0]
                in _CONTEXT_OVERRIDE_OPTIONS
                for argument in command.args
            ):
                raise ValueError("case or distributed root context override is forbidden")
            if command.mpi_ranks == 1 and "-parallel" in command.args:
                raise ValueError("serial steps must not use -parallel")
            for argument in command.args:
                self._validate_argument(argument)

    def _typed_argv(self, command: NativeCommand) -> list[str]:
        executable = str(self.command_paths[command.executable])
        if command.mpi_ranks == 1:
            return [executable, *command.args]
        if self.environment.mpi_launcher is None:
            raise ValueError("MPI launcher is unavailable")
        arguments = [argument for argument in command.args if argument != "-parallel"]
        return [
            str(self.environment.mpi_launcher.resolve()),
            "-n",
            str(command.mpi_ranks),
            executable,
            *arguments,
            "-parallel",
        ]

    def _sandbox_command(
        self,
        *,
        case_dir: Path,
        command: NativeCommand,
        budget: ResourceBudget,
        protected_paths: Sequence[Path],
    ) -> tuple[list[str], list[str]]:
        typed = self._typed_argv(command)
        launch = build_sandbox_argv(
            config=self.runtime_config,
            environment=self.environment,
            case_dir=case_dir,
            protected_paths=protected_paths,
            memory_mib=budget.memory_mib,
            cpu_seconds=command.timeout_seconds,
            typed_argv=typed,
        )
        return list(launch.argv), typed

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
            str(self.runtime_config.openfoam_root / "etc/bashrc"),
            str(case_dir),
            *typed,
        ]
        return full, typed

    def _host_environment(self) -> dict[str, str]:
        home = self.workspace_root / ".foampilot/runtime-host-home"
        temporary = self.workspace_root / ".foampilot/runtime-tmp"
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

    def _freeze_policy(
        self,
        *,
        case: Path,
        commands: Sequence[NativeCommand],
        budget: ResourceBudget,
        risk_report: ExecutionRiskReport,
        protected_paths: Sequence[Path],
    ) -> tuple[SandboxProbe, ExecutionPolicyDecision]:
        if self.runtime_config.isolation == "trusted_host":
            probe = not_requested_probe()
        else:
            probe = self.sandbox_probe(
                config=self.runtime_config,
                environment=self.environment,
                case_dir=case,
                protected_paths=protected_paths,
                memory_mib=budget.memory_mib,
                cpu_seconds=max(command.timeout_seconds for command in commands),
            )
        effective_risk = with_command_risk(risk_report, commands)
        decision = decide_execution_policy(self.runtime_config, effective_risk, probe)
        if not decision.allowed:
            raise RuntimeExecutionError(decision, probe)
        return probe, decision

    def run(
        self,
        *,
        case_dir: str | Path,
        commands: Sequence[NativeCommand],
        budget: ResourceBudget,
        risk_report: ExecutionRiskReport,
        protected_paths: Sequence[Path],
        workflow: WorkflowStore | None = None,
        attempt: int | None = None,
    ) -> PlanRunResult:
        case = Path(case_dir).resolve()
        if not case.is_relative_to(self.workspace_root) or not case.is_dir():
            raise ValueError("case is missing or outside runner workspace")
        self._validate_commands(commands, budget)
        probe, decision = self._freeze_policy(
            case=case,
            commands=commands,
            budget=budget,
            risk_report=risk_report,
            protected_paths=protected_paths,
        )

        log_directory = case / ".foampilot/logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        steps: list[PlanStepResult] = []
        failed_step_id: str | None = None
        run_timed_out = False
        execution_error_code: str | None = None

        for index, command in enumerate(commands, start=1):
            stdout_path = log_directory / f"{index:02d}-{command.step_id}.stdout.log"
            stderr_path = log_directory / f"{index:02d}-{command.step_id}.stderr.log"
            try:
                if decision.actual_backend == "bubblewrap":
                    full_argv, typed_argv = self._sandbox_command(
                        case_dir=case,
                        command=command,
                        budget=budget,
                        protected_paths=protected_paths,
                    )
                    executor_options: dict[str, Any] = {}
                else:
                    full_argv, typed_argv = self._host_command(
                        case_dir=case,
                        command=command,
                        budget=budget,
                    )
                    executor_options = {
                        "cwd": case,
                        "env": self._host_environment(),
                    }
            except SandboxBuildError:
                failed_step_id = command.step_id
                execution_error_code = "SANDBOX_SETUP_FAILED"
                break

            started = _utc_now()
            if workflow is not None:
                workflow.record(
                    WorkflowEvent.started(
                        stage=WorkflowStage.OPENFOAM_STEP_STARTED,
                        sequence=workflow.next_sequence,
                        occurred_at=started,
                        attempt=attempt,
                        step_id=command.step_id,
                        detail="typed OpenFOAM command started",
                    )
                )
            timed_out = False
            cancelled = False
            return_code: int | None
            finished = started
            evidence_path = stdout_path.relative_to(
                self.workspace_root
            ).as_posix()
            log_observer = _StepLogObserver(
                stdout_path=stdout_path,
                evidence_path=evidence_path,
                reporter=self.activity_reporter,
                attempt=attempt,
                stage=command.stage.value,
                step_id=command.step_id,
            )
            with stdout_path.open("w", encoding="utf-8") as stdout_stream, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_stream:
                if self.executor is subprocess.run:
                    completed = run_supervised_process(
                        full_argv,
                        timeout_seconds=command.timeout_seconds,
                        source="runner",
                        reporter=self.activity_reporter,
                        stage=command.stage.value,
                        step_id=command.step_id,
                        attempt=attempt,
                        stdout=stdout_stream,
                        stderr=stderr_stream,
                        heartbeat_seconds=self.heartbeat_seconds,
                        on_tick=log_observer.poll,
                        **executor_options,
                    )
                    return_code = completed.returncode
                    timed_out = completed.timed_out
                    cancelled = completed.cancelled
                    started = completed.started_at
                    finished = completed.finished_at
                    log_observer.finish(completed.elapsed_seconds, completed.pid)
                else:
                    try:
                        legacy_completed = self.executor(
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
                        return_code = legacy_completed.returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        return_code = None
                    finished = _utc_now()

            semantic_failure = False
            if command.executable == "checkMesh" and return_code == 0 and not timed_out:
                check_text = "\n".join(
                    (
                        stdout_path.read_text(encoding="utf-8", errors="replace"),
                        stderr_path.read_text(encoding="utf-8", errors="replace"),
                    )
                )
                semantic_failure = bool(_FAILED_MESH_CHECKS.search(check_text))
            step = PlanStepResult(
                step_id=command.step_id,
                command=typed_argv,
                return_code=return_code,
                started_at=started,
                finished_at=finished,
                timed_out=timed_out,
                cancelled=cancelled,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                execution_backend=decision.actual_backend,
                backend_fallback_reason=decision.fallback_reason,
            )
            steps.append(step)
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace").lstrip()
            sandbox_setup_failure = (
                decision.actual_backend == "bubblewrap"
                and return_code not in (None, 0)
                and (stderr_text.startswith("bwrap:") or stderr_text.startswith("prlimit:"))
            )
            if sandbox_setup_failure:
                execution_error_code = "SANDBOX_SETUP_FAILED"
            step_failed = (
                timed_out or cancelled or return_code != 0 or semantic_failure
            )
            if workflow is not None:
                workflow.record(
                    WorkflowEvent(
                        sequence=workflow.next_sequence,
                        stage=WorkflowStage.OPENFOAM_STEP_COMPLETE,
                        state=(
                            WorkflowEventState.CANCELLED
                            if cancelled
                            else (
                                WorkflowEventState.FAILED
                                if step_failed
                                else WorkflowEventState.COMPLETED
                            )
                        ),
                        occurred_at=finished,
                        attempt=attempt,
                        step_id=command.step_id,
                        detail=(
                            f"return_code={return_code}; timed_out={timed_out}; "
                            f"cancelled={cancelled}"
                        ),
                        evidence_paths=[
                            stdout_path.relative_to(workflow.run_dir).as_posix(),
                            stderr_path.relative_to(workflow.run_dir).as_posix(),
                        ],
                    )
                )
            if step_failed:
                failed_step_id = None if cancelled else command.step_id
                run_timed_out = timed_out
                break

        return PlanRunResult(
            case_dir=case,
            steps=steps,
            failed_step_id=failed_step_id,
            timed_out=run_timed_out,
            cancelled=any(step.cancelled for step in steps),
            sandbox_probe=probe,
            execution_policy=decision,
            execution_error_code=execution_error_code,
        )
