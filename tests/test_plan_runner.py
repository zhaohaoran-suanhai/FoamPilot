from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from foampilot.plans import NativeCommand
from foampilot.runtime import PlanRunner, RuntimeConfig
from foampilot.tasks import ResourceBudget


class RecordingExecutor:
    def __init__(
        self,
        return_codes: dict[str, int],
        stdout_by_marker: dict[str, str] | None = None,
    ) -> None:
        self.return_codes = return_codes
        self.stdout_by_marker = stdout_by_marker or {}
        self.invocations: list[list[str]] = []
        self.shell_values: list[bool] = []

    def __call__(self, command, **kwargs):
        invoked = list(command)
        self.invocations.append(invoked)
        self.shell_values.append(kwargs["shell"])
        marker = (
            "check"
            if "checkMesh" in invoked
            else "solve"
            if "buoyantFoam" in invoked
            else next(
                value
                for value in (
                    "mesh",
                    "check",
                    "solve",
                    "reconstruct",
                    "help",
                )
                if value in invoked
            )
        )
        kwargs["stdout"].write(
            self.stdout_by_marker.get(marker, f"{marker} stdout\n")
        )
        kwargs["stderr"].write(f"{marker} stderr\n")
        return subprocess.CompletedProcess(
            invoked,
            self.return_codes.get(marker, 0),
        )


def _config() -> RuntimeConfig:
    root = Path("/home/edwin/workplace/OpenFOAM-10")
    return RuntimeConfig(
        openfoam_root=root,
        tutorial_root=root / "tutorials",
        python_executable=Path("/home/edwin/feal-venv-py312/bin/python"),
        bubblewrap=Path("/usr/local/bin/bwrap"),
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
    stages = {
        "mesh": "mesh",
        "check": "check",
        "reconstruct": "reconstruct",
    }
    return NativeCommand(
        step_id=step_id,
        stage=stages.get(step_id, "solve"),
        executable=executable or step_id,
        args=args or [],
        mpi_ranks=mpi_ranks,
        timeout_seconds=timeout_seconds,
    )


def _runner(
    tmp_path: Path,
    executor: RecordingExecutor,
) -> PlanRunner:
    return PlanRunner(
        runtime_config=_config(),
        available_executables={
            "mesh",
            "check",
            "solve",
            "reconstruct",
            "buoyantFoam",
            "blockMesh",
            "checkMesh",
        },
        workspace_root=tmp_path,
        executor=executor,
    )


def test_runner_executes_argument_array_and_stops_at_first_failure(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(
        return_codes={"mesh": 0, "check": 2, "solve": 0}
    )
    runner = _runner(tmp_path, executor)
    case = tmp_path / "run/attempt-01/case"
    case.mkdir(parents=True)

    result = runner.run(
        case_dir=case,
        commands=[
            _command("mesh"),
            _command("check"),
            _command("solve"),
        ],
        budget=_budget(),
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
        stdout_by_marker={
            "check": (
                "Checking geometry...\n"
                "Failed 1 mesh checks.\n"
                "End\n"
            )
        },
    )
    runner = _runner(tmp_path, executor)
    case = tmp_path / "case"
    case.mkdir()

    result = runner.run(
        case_dir=case,
        commands=[
            _command("mesh_check", executable="checkMesh"),
            _command("solve"),
        ],
        budget=_budget(),
    )

    assert [step.step_id for step in result.steps] == ["mesh_check"]
    assert result.steps[0].return_code == 0
    assert result.failed_step_id == "mesh_check"
    assert len(executor.invocations) == 1


def test_runner_does_not_block_ambiguous_checkmesh_log(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(
        return_codes={"check": 0, "solve": 0},
        stdout_by_marker={"check": "Checking geometry...\nEnd\n"},
    )
    runner = _runner(tmp_path, executor)
    case = tmp_path / "case"
    case.mkdir()

    result = runner.run(
        case_dir=case,
        commands=[
            _command("mesh_check", executable="checkMesh"),
            _command("solve"),
        ],
        budget=_budget(),
    )

    assert result.passed
    assert len(executor.invocations) == 2


def test_runner_wraps_parallel_solver_but_not_agent_shell(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    runner = _runner(tmp_path, executor)
    case = tmp_path / "case"
    case.mkdir()

    result = runner.run(
        case_dir=case,
        commands=[
            _command(
                "solve",
                executable="buoyantFoam",
                mpi_ranks=4,
            )
        ],
        budget=_budget(max_mpi_ranks=4),
    )

    assert result.passed
    assert executor.invocations[0][-5:] == [
        "mpirun",
        "-n",
        "4",
        "buoyantFoam",
        "-parallel",
    ]


def test_runner_allows_parallel_installed_utility_without_phase_label(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    runner = _runner(tmp_path, executor)
    case = tmp_path / "case"
    case.mkdir()

    result = runner.run(
        case_dir=case,
        commands=[
            _command(
                "initialize",
                executable="buoyantFoam",
                mpi_ranks=2,
            )
        ],
        budget=_budget(max_mpi_ranks=2),
    )

    assert result.passed
    assert result.steps[0].command[-5:] == [
        "mpirun",
        "-n",
        "2",
        "buoyantFoam",
        "-parallel",
    ]


@pytest.mark.parametrize(
    ("command", "budget", "message"),
    [
        (
            _command("solve", mpi_ranks=4),
            _budget(max_mpi_ranks=2),
            "MPI rank",
        ),
        (
            _command("solve", executable="madeUpFoam"),
            _budget(),
            "not available",
        ),
        (
            _command("solve", args=["-case", "/private/case"]),
            _budget(),
            "absolute path",
        ),
        (
            _command("solve", timeout_seconds=181),
            _budget(max_wall_seconds=180),
            "timeout budget",
        ),
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
        runner.run(case_dir=case, commands=[command], budget=budget)


def test_runner_rejects_case_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    runner = PlanRunner(
        runtime_config=_config(),
        available_executables={"solve"},
        workspace_root=workspace,
        executor=RecordingExecutor({}),
    )

    with pytest.raises(ValueError, match="outside runner workspace"):
        runner.run(
            case_dir=external,
            commands=[_command("solve")],
            budget=_budget(),
        )
