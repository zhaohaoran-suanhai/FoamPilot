from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foampilot.activity import ActivityEvent, ActivityReporter
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.plans import NativeCommand
from foampilot.runtime import PlanRunner, RuntimeConfig
from foampilot.runtime.models import ExecutionRiskReport, SandboxProbe
from foampilot.runtime.plan_runner import RuntimeExecutionError
from foampilot.tasks import ResourceBudget
from foampilot.workflow import WorkflowEvent, WorkflowEventState, WorkflowStage
from foampilot.workflow.store import WorkflowStore


class RecordingExecutor:
    def __init__(
        self,
        return_codes: dict[str, int],
        stdout_by_marker: dict[str, str] | None = None,
        stderr_by_marker: dict[str, str] | None = None,
        before_execute=None,
    ) -> None:
        self.return_codes = return_codes
        self.stdout_by_marker = stdout_by_marker or {}
        self.stderr_by_marker = stderr_by_marker or {}
        self.invocations: list[list[str]] = []
        self.shell_values: list[bool] = []
        self.environments: list[dict[str, str] | None] = []
        self.before_execute = before_execute

    def __call__(self, command, **kwargs):
        if self.before_execute is not None:
            self.before_execute()
        invoked = list(command)
        self.invocations.append(invoked)
        self.shell_values.append(kwargs["shell"])
        self.environments.append(kwargs.get("env"))
        names = {Path(value).name for value in invoked}
        marker = (
            "check"
            if "checkMesh" in names
            else "solve"
            if names & {"buoyantFoam", "icoFoam"}
            else next(
                value
                for value in ("mesh", "check", "solve", "reconstruct", "help")
                if value in names
            )
        )
        kwargs["stdout"].write(
            self.stdout_by_marker.get(marker, f"{marker} stdout\n")
        )
        kwargs["stderr"].write(
            self.stderr_by_marker.get(marker, f"{marker} stderr\n")
        )
        return subprocess.CompletedProcess(
            invoked,
            self.return_codes.get(marker, 0),
        )


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _config(
    tmp_path: Path,
    *,
    isolation: str = "sandbox_preferred",
    allow_dynamic_code_on_host: bool = False,
) -> RuntimeConfig:
    root = tmp_path / "OpenFOAM-10"
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc/bashrc").write_text("true\n", encoding="utf-8")
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(bwrap)
    return RuntimeConfig(
        openfoam_root=root,
        bubblewrap=bwrap,
        max_mpi_ranks=8,
        isolation=isolation,
        allow_dynamic_code_on_host=allow_dynamic_code_on_host,
    )


def _environment(config: RuntimeConfig, workspace: Path) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=config.openfoam_root,
        tutorial_root=None,
        workspace_root=workspace,
        workspace_writable=True,
        commands=[
            CommandFact(name=name, path=config.openfoam_root / "bin" / name)
            for name in (
                "mesh",
                "check",
                "solve",
                "reconstruct",
                "buoyantFoam",
                "icoFoam",
                "blockMesh",
                "checkMesh",
            )
        ],
        mpi_launcher=Path("/usr/bin/mpirun"),
        gmsh=None,
        max_mpi_ranks=8,
    )


def _budget(
    *,
    max_mpi_ranks: int = 2,
    max_wall_seconds: int = 180,
) -> ResourceBudget:
    return ResourceBudget(
        max_attempts=2,
        max_wall_seconds=max_wall_seconds,
        max_mpi_ranks=max_mpi_ranks,
        memory_mib=2048,
    )


def _command(
    step_id: str,
    *,
    executable: str | None = None,
    args: list[str] | None = None,
    mpi_ranks: int = 1,
    timeout_seconds: int = 30,
) -> NativeCommand:
    stages = {"mesh": "mesh", "check": "check", "reconstruct": "reconstruct"}
    return NativeCommand(
        step_id=step_id,
        stage=stages.get(step_id, "solve"),
        executable=executable or step_id,
        args=args or [],
        mpi_ranks=mpi_ranks,
        timeout_seconds=timeout_seconds,
    )


def _risk(level: str = "low") -> ExecutionRiskReport:
    return ExecutionRiskReport(
        risk_level=level,
        scanned_file_sha256={"system/controlDict": "a" * 64},
    )


def _probe(ok: bool, detail: str = "ok") -> SandboxProbe:
    return SandboxProbe(
        status="passed" if ok else "failed",
        ok=ok,
        builder_sha256="a" * 64 if ok else None,
        namespace_flags=("--unshare-net", "--unshare-pid"),
        mount_count=8 if ok else 0,
        protected_path_count=0,
        failure_code=None if ok else "NAMESPACE_UNAVAILABLE",
        return_code=0 if ok else 1,
        detail=detail,
    )


def _runner(
    tmp_path: Path,
    executor: RecordingExecutor | None,
    *,
    config: RuntimeConfig | None = None,
    sandbox_probe=lambda **_: _probe(True),
    activity_reporter: ActivityReporter | None = None,
    heartbeat_seconds: float = 5.0,
) -> PlanRunner:
    active_config = config or _config(tmp_path)
    kwargs = {}
    if executor is not None:
        kwargs["executor"] = executor
    return PlanRunner(
        runtime_config=active_config,
        environment=_environment(active_config, tmp_path),
        available_executables={
            "mesh",
            "check",
            "solve",
            "reconstruct",
            "buoyantFoam",
            "icoFoam",
            "blockMesh",
            "checkMesh",
        },
        workspace_root=tmp_path,
        sandbox_probe=sandbox_probe,
        activity_reporter=activity_reporter,
        heartbeat_seconds=heartbeat_seconds,
        **kwargs,
    )


def _run(
    runner: PlanRunner,
    case: Path,
    commands,
    budget=None,
    *,
    workflow: WorkflowStore | None = None,
    attempt: int | None = None,
):
    return runner.run(
        case_dir=case,
        commands=commands,
        budget=budget or _budget(),
        risk_report=_risk(),
        protected_paths=(),
        workflow=workflow,
        attempt=attempt,
    )


def test_runner_records_step_start_before_executor_and_terminal_after(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    case = run_dir / "attempt-01/case"
    case.mkdir(parents=True)
    workflow = WorkflowStore(run_dir=run_dir)

    def assert_started() -> None:
        events = [
            WorkflowEvent.model_validate_json(line)
            for line in workflow.events_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [(event.stage, event.state) for event in events] == [
            (WorkflowStage.OPENFOAM_STEP_STARTED, WorkflowEventState.STARTED)
        ]

    executor = RecordingExecutor(
        return_codes={"solve": 0},
        before_execute=assert_started,
    )

    _run(
        _runner(tmp_path, executor),
        case,
        [_command("solve")],
        workflow=workflow,
        attempt=1,
    )

    events = [
        WorkflowEvent.model_validate_json(line)
        for line in workflow.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(event.stage, event.state) for event in events] == [
        (WorkflowStage.OPENFOAM_STEP_STARTED, WorkflowEventState.STARTED),
        (WorkflowStage.OPENFOAM_STEP_COMPLETE, WorkflowEventState.COMPLETED),
    ]
    assert all(event.attempt == 1 for event in events)
    assert all(event.step_id == "solve" for event in events)


def test_runner_records_failed_terminal_step_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    case = run_dir / "attempt-01/case"
    case.mkdir(parents=True)
    workflow = WorkflowStore(run_dir=run_dir)
    executor = RecordingExecutor(return_codes={"solve": 7})

    _run(
        _runner(tmp_path, executor),
        case,
        [_command("solve")],
        workflow=workflow,
        attempt=1,
    )

    events = [
        WorkflowEvent.model_validate_json(line)
        for line in workflow.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1].stage == WorkflowStage.OPENFOAM_STEP_COMPLETE
    assert events[-1].state == WorkflowEventState.FAILED


def test_runner_streams_real_log_growth_and_residual_metric(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, isolation="trusted_host")
    solver = config.openfoam_root / "bin/icoFoam"
    solver.parent.mkdir(parents=True, exist_ok=True)
    solver.write_text(
        "#!/bin/sh\n"
        "echo 'Time = 0.5'\n"
        "echo 'Solving for Ux, Initial residual = 0.12, Final residual = 0.0002, No Iterations 3'\n"
        "sleep 0.08\n",
        encoding="utf-8",
    )
    solver.chmod(0o755)
    seen: list[ActivityEvent] = []
    reporter = ActivityReporter(operation_id="op-1", listeners=[seen.append])
    case = tmp_path / "run/attempt-01/case"
    case.mkdir(parents=True)

    result = _run(
        _runner(
            tmp_path,
            None,
            config=config,
            activity_reporter=reporter,
            heartbeat_seconds=0.02,
        ),
        case,
        [_command("solve", executable="icoFoam")],
    )

    assert result.passed
    assert any(event.kind == "log" and event.evidence_offset for event in seen)
    residuals = [event for event in seen if event.kind == "metric"]
    assert len(residuals) == 1
    assert residuals[0].metrics == {
        "field": "Ux",
        "initial_residual": 0.12,
        "final_residual": 0.0002,
        "solver_iterations": 3,
        "simulation_time": 0.5,
    }


def test_runner_executes_argument_array_and_stops_at_first_failure(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"mesh": 0, "check": 2, "solve": 0})
    case = tmp_path / "run/attempt-01/case"
    case.mkdir(parents=True)

    result = _run(
        _runner(tmp_path, executor),
        case,
        [_command("mesh"), _command("check"), _command("solve")],
    )

    assert [step.step_id for step in result.steps] == ["mesh", "check"]
    assert result.failed_step_id == "check"
    assert executor.shell_values == [False, False]
    assert result.steps[0].stdout_path.read_text() == "mesh stdout\n"


def test_runner_stops_before_solver_on_explicit_checkmesh_failure(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(
        return_codes={"check": 0, "solve": 0},
        stdout_by_marker={"check": "Checking geometry...\nFailed 1 mesh checks.\nEnd\n"},
    )
    case = tmp_path / "case"
    case.mkdir()

    result = _run(
        _runner(tmp_path, executor),
        case,
        [_command("mesh_check", executable="checkMesh"), _command("solve")],
    )

    assert [step.step_id for step in result.steps] == ["mesh_check"]
    assert result.steps[0].return_code == 0
    assert result.failed_step_id == "mesh_check"
    assert len(executor.invocations) == 1


def test_runner_does_not_block_ambiguous_checkmesh_log(tmp_path: Path) -> None:
    executor = RecordingExecutor(
        return_codes={"check": 0, "solve": 0},
        stdout_by_marker={"check": "Checking geometry...\nEnd\n"},
    )
    case = tmp_path / "case"
    case.mkdir()

    result = _run(
        _runner(tmp_path, executor),
        case,
        [_command("mesh_check", executable="checkMesh"), _command("solve")],
    )

    assert result.passed
    assert len(executor.invocations) == 2


def test_runner_wraps_parallel_solver_but_not_agent_shell(tmp_path: Path) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    case = tmp_path / "case"
    case.mkdir()

    result = _run(
        _runner(tmp_path, executor),
        case,
        [_command("solve", executable="buoyantFoam", mpi_ranks=4)],
        _budget(max_mpi_ranks=4),
    )

    assert result.passed
    assert executor.invocations[0][-5:] == [
        str(Path("/usr/bin/mpirun").resolve()),
        "-n",
        "4",
        str((_config(tmp_path).openfoam_root / "bin/buoyantFoam").resolve()),
        "-parallel",
    ]


def test_preferred_falls_back_before_first_step_only_for_low_risk(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    config = _config(tmp_path, isolation="sandbox_preferred")
    case = tmp_path / "case"
    case.mkdir()
    runner = _runner(
        tmp_path,
        executor,
        config=config,
        sandbox_probe=lambda **_: _probe(False, "Operation not permitted"),
    )

    result = _run(
        runner,
        case,
        [_command("solve", executable="icoFoam")],
    )

    assert result.execution_policy.actual_backend == "host"
    assert result.steps[0].execution_backend == "host"
    assert result.steps[0].backend_fallback_reason == "Operation not permitted"
    assert executor.invocations[0][0] == "/usr/bin/prlimit"
    assert executor.environments[0] is not None
    host_home = Path(executor.environments[0]["HOME"])
    assert host_home.is_relative_to(tmp_path)
    assert not host_home.is_relative_to(case)


def test_preferred_blocks_high_risk_when_probe_fails(tmp_path: Path) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    config = _config(tmp_path, isolation="sandbox_preferred")
    case = tmp_path / "case"
    case.mkdir()
    runner = _runner(
        tmp_path,
        executor,
        config=config,
        sandbox_probe=lambda **_: _probe(False, "Operation not permitted"),
    )

    with pytest.raises(RuntimeExecutionError) as captured:
        runner.run(
            case_dir=case,
            commands=[_command("solve")],
            budget=_budget(),
            risk_report=_risk("high"),
            protected_paths=(),
        )

    assert captured.value.code == "HOST_DYNAMIC_CODE_BLOCKED"
    assert captured.value.probe.status == "failed"
    assert executor.invocations == []


def test_host_policy_reclassifies_unreviewed_executable_before_launch(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    config = _config(tmp_path, isolation="trusted_host")
    environment = _environment(config, tmp_path).model_copy(
        update={
            "commands": [
                *_environment(config, tmp_path).commands,
                CommandFact(
                    name="wmake",
                    path=config.openfoam_root / "wmake/wmake",
                ),
            ]
        }
    )
    case = tmp_path / "case"
    case.mkdir()
    runner = PlanRunner(
        runtime_config=config,
        environment=environment,
        available_executables=environment.available_executable_names,
        workspace_root=tmp_path,
        executor=executor,
    )

    with pytest.raises(RuntimeExecutionError) as captured:
        runner.run(
            case_dir=case,
            commands=[_command("compile", executable="wmake")],
            budget=_budget(),
            risk_report=_risk("low"),
            protected_paths=(),
        )

    assert captured.value.code == "HOST_DYNAMIC_CODE_BLOCKED"
    assert executor.invocations == []


@pytest.mark.parametrize("isolation", ["trusted_host", "sandbox_required"])
def test_runner_executes_canonical_trusted_command_path(
    tmp_path: Path,
    isolation: str,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    trusted = tmp_path / "trusted-solvers"
    command_path = trusted / "bin/icoFoam"
    _write_executable(command_path)
    config = _config(tmp_path, isolation=isolation).model_copy(
        update={"trusted_readonly_roots": (trusted,)}
    )
    environment = _environment(config, tmp_path).model_copy(
        update={"commands": [CommandFact(name="icoFoam", path=command_path)]}
    )
    case = tmp_path / "case"
    case.mkdir()
    runner = PlanRunner(
        runtime_config=config,
        environment=environment,
        available_executables={"icoFoam"},
        workspace_root=tmp_path,
        executor=executor,
        sandbox_probe=lambda **_: _probe(True),
    )

    result = _run(
        runner,
        case,
        [_command("solve", executable="icoFoam")],
    )

    assert result.passed
    assert str(command_path.resolve()) in executor.invocations[0]
    assert result.steps[0].command == [str(command_path.resolve())]


def test_runner_rejects_mpi_launcher_outside_approved_roots(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, isolation="sandbox_required")
    environment = _environment(config, tmp_path).model_copy(
        update={"mpi_launcher": tmp_path / "untrusted/mpirun"}
    )

    with pytest.raises(ValueError, match="MPI launcher"):
        PlanRunner(
            runtime_config=config,
            environment=environment,
            available_executables=environment.available_executable_names,
            workspace_root=tmp_path,
        )


def test_sandbox_step_failure_never_switches_backend(tmp_path: Path) -> None:
    executor = RecordingExecutor(
        return_codes={"mesh": 1, "solve": 0},
        stderr_by_marker={"mesh": "bwrap: loopback: Operation not permitted\n"},
    )
    config = _config(tmp_path, isolation="sandbox_required")
    case = tmp_path / "case"
    case.mkdir()

    result = _run(
        _runner(tmp_path, executor, config=config),
        case,
        [_command("mesh"), _command("solve")],
    )

    assert [step.execution_backend for step in result.steps] == ["bubblewrap"]
    assert result.failed_step_id == "mesh"
    assert result.execution_error_code == "SANDBOX_SETUP_FAILED"
    assert len(executor.invocations) == 1


def test_runner_allows_parallel_installed_utility_without_phase_label(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    case = tmp_path / "case"
    case.mkdir()

    result = _run(
        _runner(tmp_path, executor),
        case,
        [_command("initialize", executable="buoyantFoam", mpi_ranks=2)],
        _budget(max_mpi_ranks=2),
    )

    assert result.passed
    assert result.steps[0].command[-5:] == [
        str(Path("/usr/bin/mpirun").resolve()),
        "-n",
        "2",
        str((_config(tmp_path).openfoam_root / "bin/buoyantFoam").resolve()),
        "-parallel",
    ]


@pytest.mark.parametrize(
    ("command", "budget", "message"),
    [
        (_command("solve", mpi_ranks=4), _budget(max_mpi_ranks=2), "MPI rank"),
        (_command("solve", executable="madeUpFoam"), _budget(), "not available"),
        (_command("solve", args=["-case", "/private/case"]), _budget(), "context override"),
        (_command("solve", args=["-case", "/case"]), _budget(), "context override"),
        (_command("solve", args=["-case=other-case"]), _budget(), "context override"),
        (_command("solve", args=["-roots", "other-case"]), _budget(), "context override"),
        (_command("solve", args=["-dict=/tmp/evil"]), _budget(), "absolute path"),
        (_command("solve", timeout_seconds=181), _budget(max_wall_seconds=180), "timeout budget"),
    ],
)
def test_runner_rejects_invalid_typed_commands(
    tmp_path: Path,
    command: NativeCommand,
    budget: ResourceBudget,
    message: str,
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    runner = _runner(tmp_path, RecordingExecutor({}))

    with pytest.raises(ValueError, match=message):
        runner.run(
            case_dir=case,
            commands=[command],
            budget=budget,
            risk_report=_risk(),
            protected_paths=(),
        )


def test_runner_rejects_case_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    config = _config(tmp_path)
    runner = PlanRunner(
        runtime_config=config,
        environment=_environment(config, workspace),
        available_executables={"solve"},
        workspace_root=workspace,
        executor=RecordingExecutor({}),
        sandbox_probe=lambda **_: _probe(True),
    )

    with pytest.raises(ValueError, match="outside runner workspace"):
        runner.run(
            case_dir=external,
            commands=[_command("solve")],
            budget=_budget(),
            risk_report=_risk(),
            protected_paths=(),
        )
