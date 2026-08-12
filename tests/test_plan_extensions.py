from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.environment import CommandFact
from foampilot.extensions import (
    CapabilityDescriptor,
    CapabilityRegistry,
    SupportedTarget,
)
from foampilot.extensions.planning import (
    PlanContext,
    PlanContributionError,
    PlanFragment,
)
from foampilot.manifests import CaseManifest, CaseModels, CaseRegion
from foampilot.simulation import FactEvidence, ResolvedValue, SimulationIntent
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
from foampilot.simulation.risk_gate import RiskDecision, freeze_case_design
from foampilot.tasks import OpenFOAMTarget, ResourceBudget


def _fact(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="user_text",
        impact="high",
        evidence=(FactEvidence(kind="test_fact", detail="explicit test fact"),),
        confirmed=True,
    )


def _context(
    *,
    mesh: str = "provided",
    ranks: int = 1,
    max_wall_seconds: int = 120,
    version: str = "10",
    executables: tuple[str, ...] = (
        "blockMesh",
        "checkMesh",
        "decomposePar",
        "pisoFoam",
        "reconstructPar",
    ),
    regions: tuple[str, ...] = ("default",),
) -> PlanContext:
    mesh_extension = (
        "foampilot.mesh.openfoam-provided"
        if mesh == "provided"
        else "foampilot.mesh.block-mesh"
    )
    solver_extension = (
        "foampilot.solver.foundation10-parallel"
        if ranks > 1
        else "foampilot.solver.foundation10-serial"
    )
    proposal = CaseDesignProposal(
        solver_family=_fact("solver.family", "pisoFoam"),
        physical_models=(
            _fact("physics.regime", "transient"),
            _fact("physics.family", "fluid"),
        ),
        materials=(),
        boundary_designs=(),
        initial_conditions=(),
        time_design=(),
        numerical_design=(),
        region_models=(),
        extension_decisions=(
            ExtensionDecision(
                extension_id=mesh_extension,
                schema_version=1,
                values=(_fact("mesh.strategy", mesh),),
                provenance=(
                    FactEvidence(kind="test_fact", detail="selected mesh"),
                ),
            ),
            ExtensionDecision(
                extension_id=solver_extension,
                schema_version=1,
                values=(_fact("execution.mpi_ranks", ranks),),
                provenance=(
                    FactEvidence(kind="test_fact", detail="selected runner"),
                ),
            ),
        ),
        uncertainties=(),
        alternatives=(),
        reasoning_evidence=(
            FactEvidence(kind="test_fact", detail="coherent design"),
        ),
        capability_conflicts=(),
    )
    registry = CapabilityRegistry.planning_first_party()
    identities = {
        item.extension_id: (
            f"{item.extension_version}/protocol-{item.protocol_version}"
        )
        for item in (
            registry.descriptor(mesh_extension),
            registry.descriptor(solver_extension),
        )
    }
    decision = RiskDecision(
        state="READY_TO_AUTHOR",
        questions=(),
        reason_codes=("DESIGN_FACTS_RESOLVED",),
        proposal_sha256="0" * 64,
        required_extension_ids=tuple(sorted(identities)),
        required_extension_identities=identities,
    )
    from foampilot.simulation import canonical_sha256

    decision = decision.model_copy(
        update={"proposal_sha256": canonical_sha256(proposal)}
    )
    design = freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=SimulationIntent(),
    )
    manifest = CaseManifest(
        solver_executable="pisoFoam",
        solver_family="incompressible-laminar",
        regime="transient",
        physics_family="fluid",
        mesh_family=mesh,
        dimensionality="2d",
        regions=[
            CaseRegion(
                name=name,
                kind="fluid",
                path_prefix="" if name == "default" else f"constant/{name}",
            )
            for name in regions
        ],
        models=CaseModels(transport="Newtonian"),
    )
    return PlanContext(
        design=design,
        manifest=manifest,
        target=OpenFOAMTarget(distribution="foundation", version=version),
        resource_budget=ResourceBudget(
            max_attempts=1,
            max_wall_seconds=max_wall_seconds,
            max_mpi_ranks=max(ranks, 1),
            memory_mib=512,
        ),
        command_facts=tuple(
            CommandFact(name=name, path=Path("/opt/openfoam/bin") / name)
            for name in executables
        ),
        mpi_available=True,
    )


def test_provided_mesh_contributes_check_but_no_mesh_generator() -> None:
    plan = CapabilityRegistry.planning_first_party().plan_for(
        _context(mesh="provided")
    )

    assert [(item.stage.value, item.executable) for item in plan.commands] == [
        ("check", "checkMesh"),
        ("solve", "pisoFoam"),
    ]


def test_parallel_fragment_never_contains_mpi_launcher() -> None:
    plan = CapabilityRegistry.planning_first_party().plan_for(
        _context(mesh="provided", ranks=4)
    )

    assert all(
        item.executable not in {"mpirun", "mpiexec", "orterun"}
        for item in plan.commands
    )
    assert [item.executable for item in plan.commands] == [
        "checkMesh",
        "decomposePar",
        "pisoFoam",
        "reconstructPar",
    ]
    assert plan.commands[2].mpi_ranks == 4


def test_block_mesh_order_and_required_authored_path_are_deterministic() -> None:
    plan = CapabilityRegistry.planning_first_party().plan_for(
        _context(mesh="blockMesh")
    )

    assert [item.executable for item in plan.commands] == [
        "blockMesh",
        "checkMesh",
        "pisoFoam",
    ]
    assert plan.required_authored_paths == ("system/blockMeshDict",)


def test_multi_region_mesh_commands_use_explicit_region_arguments() -> None:
    plan = CapabilityRegistry.planning_first_party().plan_for(
        _context(mesh="provided", regions=("fluid", "solid"))
    )

    assert [item.args for item in plan.commands[:2]] == [
        ["-region", "fluid"],
        ["-region", "solid"],
    ]


@pytest.mark.parametrize(
    ("context", "code"),
    [
        (_context(executables=("checkMesh",)), "PLAN_EXECUTABLE_UNAVAILABLE"),
        (_context(version="13"), "PLAN_TARGET_UNSUPPORTED"),
        (_context(max_wall_seconds=1), "PLAN_TIMEOUT_BUDGET_EXCEEDED"),
    ],
)
def test_plan_contributors_fail_closed(
    context: PlanContext,
    code: str,
) -> None:
    with pytest.raises(PlanContributionError, match=code):
        CapabilityRegistry.planning_first_party().plan_for(context)


def test_composition_rejects_duplicate_step_ids_across_fragments() -> None:
    class Contributor:
        def __init__(self, descriptor: CapabilityDescriptor) -> None:
            self.descriptor = descriptor

        def contribute(self, context: PlanContext) -> PlanFragment:
            del context
            from foampilot.plans import NativeCommand

            return PlanFragment(
                contributor_id=self.descriptor.extension_id,
                contributor_identity="1.0.0/protocol-1",
                commands=(
                    NativeCommand(
                        step_id="duplicate",
                        stage="check",
                        executable="checkMesh",
                        timeout_seconds=1,
                    ),
                ),
            )

    registry = CapabilityRegistry()
    descriptors = tuple(
        CapabilityDescriptor(
            extension_id=f"foampilot.test.contributor-{suffix}",
            extension_version="1.0.0",
            capability_kinds=(f"test:contributor-{suffix}",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
        )
        for suffix in ("a", "b")
    )
    for descriptor in descriptors:
        registry.register(descriptor, Contributor(descriptor))
    context = _context().model_copy(
        update={
            "design": _context().design.model_copy(
                update={
                    "extension_identities": {
                        descriptor.extension_id: "1.0.0/protocol-1"
                        for descriptor in reversed(descriptors)
                    }
                }
            )
        }
    )

    with pytest.raises(PlanContributionError, match="PLAN_DUPLICATE_STEP_ID"):
        registry.plan_for(context)
