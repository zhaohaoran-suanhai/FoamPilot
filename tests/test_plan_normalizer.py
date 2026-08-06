from __future__ import annotations

import pytest

from foampilot.plans import (
    CommandStage,
    NativeCommand,
    normalize_execution_plan,
    validate_execution_plan,
)

from .test_execution_plan import task as task_fixture
from .test_execution_plan import valid_plan


@pytest.fixture
def task(task_fixture):
    return task_fixture


def _launcher_plan(
    launcher: str,
    args: list[str],
    *,
    mpi_ranks: int = 1,
):
    plan = valid_plan().model_copy(deep=True)
    plan.commands = [
        plan.commands[0],
        NativeCommand(
            step_id="solve",
            stage="solve",
            executable=launcher,
            args=args,
            mpi_ranks=mpi_ranks,
            timeout_seconds=60,
        ),
    ]
    return plan


@pytest.mark.parametrize(
    ("launcher", "rank_flag"),
    [
        ("mpirun", "-n"),
        ("mpiexec", "-np"),
        ("orterun", "-n"),
    ],
)
def test_normalizer_unwraps_only_simple_local_mpi_solver_shape(
    task,
    launcher: str,
    rank_flag: str,
):
    original = _launcher_plan(
        launcher,
        [rank_flag, "4", "icoFoam", "-parallel"],
    )

    result = normalize_execution_plan(
        original,
        task,
        {"blockMesh", "icoFoam"},
    )

    solve = result.plan.commands[1]
    assert solve.executable == "icoFoam"
    assert solve.stage == "solve"
    assert solve.mpi_ranks == 4
    assert solve.args == []
    assert result.records[0].original_launcher == launcher
    assert result.records[0].solver == "icoFoam"
    assert original.commands[1].executable == launcher


@pytest.mark.parametrize(
    "args",
    [
        ["-n", "4", "--hostfile", "hosts", "icoFoam"],
        ["-n", "4", "icoFoam", "-parallel", "-case", "/case"],
        ["-n", "0", "icoFoam"],
        ["-n", "8", "icoFoam"],
        ["-n", "four", "icoFoam"],
        ["-n", "4", "missingFoam"],
        ["-n", "4", "icoFoam", "&&", "other"],
    ],
)
def test_unsafe_or_ambiguous_mpi_shapes_remain_for_policy_rejection(
    task,
    args: list[str],
):
    original = _launcher_plan("mpirun", args)

    result = normalize_execution_plan(
        original,
        task,
        {"blockMesh", "icoFoam"},
    )
    issues = validate_execution_plan(
        result.plan,
        task,
        {"blockMesh", "icoFoam", "mpirun"},
    )

    assert result.records == ()
    assert result.plan.commands[1].executable == "mpirun"
    assert "MPI_LAUNCHER_UNNORMALIZED" in {
        issue.code for issue in issues
    }


def test_conflicting_predeclared_mpi_ranks_are_not_normalized(task):
    original = _launcher_plan(
        "mpirun",
        ["-n", "4", "icoFoam"],
        mpi_ranks=2,
    )

    result = normalize_execution_plan(
        original,
        task,
        {"blockMesh", "icoFoam"},
    )

    assert result.records == ()
    assert result.plan.commands[1].mpi_ranks == 2


def test_known_utility_stage_is_normalized_without_mutating_evidence(task):
    original = valid_plan().model_copy(deep=True)
    original.commands.append(
        NativeCommand(
            step_id="post",
            stage="solve",
            executable="postProcess",
            args=["-func", "CourantNo"],
            mpi_ranks=1,
            timeout_seconds=60,
        )
    )

    result = normalize_execution_plan(
        original,
        task,
        {"blockMesh", "icoFoam", "postProcess"},
    )

    assert result.plan.commands[-1].stage == CommandStage.POSTPROCESS
    assert original.commands[-1].stage == CommandStage.SOLVE
    assert len(result.stage_records) == 1
    assert result.stage_records[0].step_id == "post"
    assert result.stage_records[0].original_stage == CommandStage.SOLVE
    assert result.stage_records[0].normalized_stage == CommandStage.POSTPROCESS
