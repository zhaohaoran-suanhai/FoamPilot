"""Safe normalization of one unambiguous local MPI command shape."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from foampilot.tasks import TaskSpec

from .models import CommandStage, ExecutionPlan


_MPI_LAUNCHERS = {"mpirun", "mpiexec", "orterun"}
_RANK_FLAGS = {"-n", "-np"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizationRecord(StrictModel):
    command_index: int = Field(ge=0)
    step_id: str
    original_launcher: str
    solver: str
    mpi_ranks: int = Field(ge=1)


class NormalizationResult(StrictModel):
    plan: ExecutionPlan
    records: tuple[NormalizationRecord, ...] = ()


def _simple_mpi_shape(
    *,
    executable: str,
    args: list[str],
    declared_ranks: int,
    max_ranks: int,
    available_executables: set[str],
) -> tuple[str, int] | None:
    if executable not in _MPI_LAUNCHERS:
        return None
    if len(args) not in {3, 4} or args[0] not in _RANK_FLAGS:
        return None
    if len(args) == 4 and args[3] != "-parallel":
        return None
    if not args[1].isdigit():
        return None
    ranks = int(args[1])
    solver = args[2]
    if (
        ranks < 1
        or ranks > max_ranks
        or declared_ranks not in {1, ranks}
        or solver not in available_executables
    ):
        return None
    return solver, ranks


def normalize_execution_plan(
    plan: ExecutionPlan,
    task: TaskSpec,
    available_executables: set[str],
) -> NormalizationResult:
    """Return a copied plan; never mutate model evidence in place."""

    normalized = plan.model_copy(deep=True)
    records: list[NormalizationRecord] = []
    for index, command in enumerate(normalized.commands):
        shape = _simple_mpi_shape(
            executable=command.executable,
            args=command.args,
            declared_ranks=command.mpi_ranks,
            max_ranks=task.resource_budget.max_mpi_ranks,
            available_executables=available_executables,
        )
        if shape is None:
            continue
        solver, ranks = shape
        records.append(
            NormalizationRecord(
                command_index=index,
                step_id=command.step_id,
                original_launcher=command.executable,
                solver=solver,
                mpi_ranks=ranks,
            )
        )
        command.executable = solver
        command.stage = CommandStage.SOLVE
        command.args = []
        command.mpi_ranks = ranks
    return NormalizationResult(
        plan=normalized,
        records=tuple(records),
    )
