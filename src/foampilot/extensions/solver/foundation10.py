"""Serial and Runner-owned-MPI Foundation v10 solver plans."""

from foampilot.plans.models import NativeCommand

from ..models import CapabilityDescriptor, SupportedTarget
from ..planning import (
    PlanContext,
    PlanContributionError,
    PlanFragment,
    command_timeout,
    descriptor_identity,
    require_context,
)


class Foundation10SerialSolverPlanContributor:
    descriptor = CapabilityDescriptor(
        extension_id="foampilot.solver.foundation10-serial",
        extension_version="1.0.0",
        capability_kinds=("execution:serial",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        input_contracts=("foampilot.simulation.CaseDesign:1",),
        output_contracts=("foampilot.extensions.PlanFragment:1",),
    )

    def contribute(self, context: PlanContext) -> PlanFragment:
        solver = str(context.design.proposal.solver_family.value)
        descriptor = self.descriptor.model_copy(
            update={"required_executables": (solver,)}
        )
        require_context(context, descriptor)
        ranks = int(context.design_value("execution.mpi_ranks", 1))
        if ranks != 1:
            raise PlanContributionError(
                "PLAN_SERIAL_RANK_MISMATCH: serial contributor requires rank 1"
            )
        return PlanFragment(
            contributor_id=self.descriptor.extension_id,
            contributor_identity=descriptor_identity(self.descriptor),
            commands=(
                NativeCommand(
                    step_id="solve",
                    stage="solve",
                    executable=solver,
                    args=[],
                    mpi_ranks=1,
                    timeout_seconds=command_timeout(
                        context,
                        fraction=0.7,
                    ),
                ),
            ),
        )


class Foundation10ParallelSolverPlanContributor:
    descriptor = CapabilityDescriptor(
        extension_id="foampilot.solver.foundation10-parallel",
        extension_version="1.0.0",
        capability_kinds=("execution:parallel",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        required_executables=("decomposePar", "reconstructPar"),
        input_contracts=("foampilot.simulation.CaseDesign:1",),
        output_contracts=("foampilot.extensions.PlanFragment:1",),
    )

    def contribute(self, context: PlanContext) -> PlanFragment:
        solver = str(context.design.proposal.solver_family.value)
        descriptor = self.descriptor.model_copy(
            update={
                "required_executables": (
                    *self.descriptor.required_executables,
                    solver,
                )
            }
        )
        require_context(context, descriptor)
        ranks = int(context.design_value("execution.mpi_ranks", 1))
        if ranks < 2 or ranks > context.resource_budget.max_mpi_ranks:
            raise PlanContributionError(
                "PLAN_PARALLEL_RANK_INVALID: ranks exceed frozen budget"
            )
        if not context.mpi_available:
            raise PlanContributionError("PLAN_MPI_UNAVAILABLE")
        return PlanFragment(
            contributor_id=self.descriptor.extension_id,
            contributor_identity=descriptor_identity(self.descriptor),
            commands=(
                NativeCommand(
                    step_id="decompose",
                    stage="decompose",
                    executable="decomposePar",
                    args=[],
                    mpi_ranks=1,
                    timeout_seconds=command_timeout(context, fraction=0.1),
                ),
                NativeCommand(
                    step_id="solve",
                    stage="solve",
                    executable=solver,
                    args=[],
                    mpi_ranks=ranks,
                    timeout_seconds=command_timeout(context, fraction=0.6),
                ),
                NativeCommand(
                    step_id="reconstruct",
                    stage="reconstruct",
                    executable="reconstructPar",
                    args=[],
                    mpi_ranks=1,
                    timeout_seconds=command_timeout(context, fraction=0.1),
                ),
            ),
            required_authored_paths=("system/decomposeParDict",),
        )
