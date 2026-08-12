"""Plan commands for deterministic Foundation blockMesh generation."""

from foampilot.plans import NativeCommand

from ..models import CapabilityDescriptor, SupportedTarget
from ..planning import (
    PlanContext,
    PlanFragment,
    command_timeout,
    descriptor_identity,
    require_context,
)


class BlockMeshPlanContributor:
    descriptor = CapabilityDescriptor(
        extension_id="foampilot.mesh.block-mesh",
        extension_version="1.0.0",
        capability_kinds=("mesh:blockmesh",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        required_executables=("blockMesh", "checkMesh"),
        input_contracts=("foampilot.simulation.CaseDesign:1",),
        output_contracts=("foampilot.extensions.PlanFragment:1",),
    )

    def contribute(self, context: PlanContext) -> PlanFragment:
        require_context(context, self.descriptor)
        commands: list[NativeCommand] = []
        for region in context.manifest.regions:
            args = [] if region.name == "default" else ["-region", region.name]
            token = region.name.lower()
            commands.extend(
                (
                    NativeCommand(
                        step_id=f"block-mesh-{token}",
                        stage="mesh",
                        executable="blockMesh",
                        args=args,
                        mpi_ranks=1,
                        timeout_seconds=command_timeout(
                            context,
                            fraction=0.1,
                        ),
                    ),
                    NativeCommand(
                        step_id=f"check-mesh-{token}",
                        stage="check",
                        executable="checkMesh",
                        args=args,
                        mpi_ranks=1,
                        timeout_seconds=command_timeout(
                            context,
                            fraction=0.1,
                        ),
                    ),
                )
            )
        return PlanFragment(
            contributor_id=self.descriptor.extension_id,
            contributor_identity=descriptor_identity(self.descriptor),
            commands=tuple(commands),
            required_authored_paths=("system/blockMeshDict",),
        )
