from __future__ import annotations

import pytest

from foampilot.agent.native_orchestrator import (
    _author_target_facts,
    _complete_planning_extensions,
    _production_capability_registry,
)
from foampilot.authoring import CaseBundle
from foampilot.extensions import CapabilityRegistry
from foampilot.extensions.physics.foundation10_porous import (
    FOUNDATION10_POROUS_EXTENSION_ID,
    canonicalize_foundation10_porous_proposal,
    foundation10_porous_descriptor,
)
from foampilot.inspection import verify_design_conformance
from foampilot.manifests import CasePatch, family_contract
from foampilot.plans import GeneratedFile
from foampilot.preprocessing import (
    BoundingBox,
    InputMeshFacts,
    MeshPatchFact,
    MeshZoneFact,
)
from foampilot.routing import CapabilityProfile
from foampilot.simulation import (
    DesignCandidate,
    FactEvidence,
    ResolvedValue,
    RiskDecision,
    SimulationIntent,
    Uncertainty,
    canonical_sha256,
    freeze_case_design,
)
from foampilot.simulation.design import ExtensionDecision
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload, resolved_fact
from tests.test_case_author import _bundle
from tests.test_plan_extensions import _context


def _task(*, confirmed: bool = True) -> TaskSpec:
    payload = canonical_task_payload(
        {
            "schema_version": 2,
            "task_id": "porous-foundation10",
            "title": "Porous blockage",
            "prompt": "Use pisoFoam for transient laminar porous flow.",
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 1,
                "max_wall_seconds": 120,
                "max_mpi_ranks": 1,
                "memory_mib": 512,
            },
            "required_outputs": ["velocity", "pressure"],
            "acceptance_requirements": ["normal completion"],
            "public_checks": [],
            "public_assets": [
                {
                    "path": "mesh/native",
                    "sha256": "a" * 64,
                    "purpose": "provided OpenFOAM mesh",
                    "kind": "directory",
                    "install_path": "constant/polyMesh",
                    "bundle_manifest_sha256": "a" * 64,
                }
            ],
            "geometry": {
                "mode": "openfoam_mesh",
                "dimensionality": "two_d",
                "description": "channel with one porous cell zone",
                "length_unit": "m",
                "assets": [
                    {
                        "path": "mesh/native",
                        "format": "openfoam_mesh",
                        "role": "fluid_mesh",
                    }
                ],
                "patch_roles": [
                    {"name": "inlet", "role": "inlet"},
                    {"name": "outlet", "role": "outlet"},
                    {"name": "top", "role": "symmetry"},
                    {"name": "bottom", "role": "symmetry"},
                    {"name": "frontAndBack", "role": "empty"},
                ],
                "region_roles": [
                    {"name": "porousBlockage", "role": "porous"},
                ],
            },
            "mesh": {"strategy": "provided"},
            "explicit_facts": [
                resolved_fact("physics.solver", "pisoFoam"),
            ],
            "protected_paths": [],
        }
    )
    if not confirmed:
        for fact in payload["explicit_facts"]:
            if fact["field_path"] == "geometry.input":
                fact["source"] = "model_inference"
                fact["confirmed"] = False
    return TaskSpec.model_validate(payload)


def _capability() -> CapabilityProfile:
    return CapabilityProfile(
        physics_family="fluid",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        turbulence="laminar",
        solver_family="incompressible-laminar",
        solver_executable="pisoFoam",
        mesh_family="provided",
        parallel_expected=False,
        confidence="high",
    )


def _fact(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="user_confirmation",
        impact="high",
        evidence=(FactEvidence(kind="test_fact", detail="confirmed candidate"),),
        confirmed=True,
    )


def _porous_design():
    base = _context().design
    descriptor = foundation10_porous_descriptor("porousBlockage", "inlet")
    proposal = base.proposal.model_copy(
        update={
            "materials": (
                _fact(
                    "materials.fluid.nu",
                    {"value": 5.0e-3, "unit": "m2/s"},
                ),
            ),
            "boundary_designs": (
                _fact(
                    "boundaries.inlet.velocity",
                    {"value": [1.0, 0.0, 0.0], "unit": "m/s"},
                ),
            ),
            "time_design": (_fact("time.end", 10.0),),
            "numerical_design": (_fact("numerics.delta_t", 0.05),),
            "region_models": (
                _fact("regions.porousBlockage.role", "porous"),
                _fact(
                    "regions.porousBlockage.coordinate_system",
                    {
                        "type": "cartesian",
                        "origin": [0.0, 0.0, 0.0],
                        "axes": {
                            "e1": [1.0, 0.0, 0.0],
                            "e2": [0.0, 1.0, 0.0],
                        },
                    },
                ),
                _fact(
                    "regions.porousBlockage.porosity_model",
                    "DarcyForchheimer",
                ),
                _fact(
                    "regions.porousBlockage.darcy_coefficient",
                    {"value": 1000.0, "unit": "1/m2"},
                ),
                _fact(
                    "regions.porousBlockage.forchheimer_coefficient",
                    {"value": 0.0, "unit": "1/m"},
                ),
            ),
            "extension_decisions": (
                *base.proposal.extension_decisions,
                ExtensionDecision(
                    extension_id=FOUNDATION10_POROUS_EXTENSION_ID,
                    schema_version=1,
                    values=(),
                    provenance=(
                        FactEvidence(
                            kind="test_fact",
                            detail="selected porous capability",
                        ),
                    ),
                ),
            ),
            "uncertainties": (
                Uncertainty(
                    question_id="confirm_inlet_velocity",
                    field_path="boundaries.inlet.velocity",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认入口速度？",
                    reason_zh="模型候选需要确认。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="legacy_inlet_candidate",
                            value={
                                "vector": [0.001, 0.0, 0.0],
                                "units": "m/s",
                            },
                            rationale="聚合候选。",
                            evidence=(
                                FactEvidence(
                                    kind="test_fact",
                                    detail="pre-projection candidate",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="minimum_cell_scale",
                    field_path="mesh.minimum_effective_cell_length",
                    impact="medium",
                    kind="information_required",
                    prompt_zh="需要最小单元尺度。",
                    reason_zh="无法从紧凑网格摘要唯一确定。",
                ),
                Uncertainty(
                    question_id="porous_zone_extent",
                    field_path="regions.porousBlockage.geometric_extent",
                    impact="medium",
                    kind="information_required",
                    prompt_zh="需要多孔区包络。",
                    reason_zh="当前只提供权威 cellZone。",
                ),
                Uncertainty(
                    question_id="porous_sampling_scope",
                    field_path="observations.porous_upstream_downstream_sampling",
                    impact="low",
                    kind="information_required",
                    prompt_zh="需要内部采样面。",
                    reason_zh="不能虚构内部采样位置。",
                ),
            ),
        }
    )
    identities = {
        **base.extension_identities,
        FOUNDATION10_POROUS_EXTENSION_ID: (
            f"{descriptor.extension_version}/protocol-{descriptor.protocol_version}"
        ),
    }
    decision = RiskDecision(
        state="READY_TO_AUTHOR",
        questions=(),
        reason_codes=("DESIGN_FACTS_RESOLVED",),
        proposal_sha256=canonical_sha256(proposal),
        required_extension_ids=tuple(sorted(identities)),
        required_extension_identities=identities,
    )
    return freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=SimulationIntent(),
    )


def _mesh() -> InputMeshFacts:
    return InputMeshFacts(
        bundle_manifest_sha256="a" * 64,
        inspector_id="foampilot.mesh.poly-mesh",
        inspector_version="1.0.0",
        region=None,
        declared_length_unit="m",
        source_member_sha256={"points": "b" * 64},
        points=8,
        faces=6,
        internal_faces=1,
        cells=2,
        bounding_box_m=BoundingBox(minimum=(0, 0, 0), maximum=(1, 1, 0.1)),
        patches=(
            MeshPatchFact(
                name="inlet", patch_type="patch", start_face=1, face_count=1
            ),
        ),
        cell_zones=(MeshZoneFact(name="porousBlockage", element_count=1),),
        face_zones=(),
        point_zones=(),
        dimensionality_observations=(),
        topology_observations=(),
        warnings=(),
    )


def _porous_bundle() -> CaseBundle:
    base = _bundle()
    manifest = base.manifest.model_copy(
        update={
            "patches": [CasePatch(name="inlet", region="default", mesh_type="patch")]
        }
    )
    files = [
        item.model_copy(
            update={
                "content": (
                    item.content + "endTime 10;\ndeltaT 0.05;\n"
                )
            }
        )
        if item.path == "system/controlDict"
        else item.model_copy(
            update={
                "content": (
                    item.content
                    + "boundaryField\n{\n"
                    + "    inlet\n    {\n"
                    + "        type fixedValue;\n"
                    + "        value uniform (1 0 0);\n"
                    + "    }\n}\n"
                )
            }
        )
        if item.path == "0/U"
        else item
        for item in base.files
    ]
    return base.model_copy(
        update={
            "manifest": manifest,
            "files": [
                *files,
                GeneratedFile(
                    path="constant/physicalProperties",
                    content="nu [0 2 -1 0 0 0 0] 5e-3;\n",
                ),
                GeneratedFile(
                    path="constant/fvModels",
                    content=(
                        "porosity { type explicitPorositySource; "
                        "explicitPorositySourceCoeffs { selectionMode cellZone; "
                        "cellZone porousBlockage; type DarcyForchheimer; "
                        "d (1000 1000 1000); f (0 0 0); "
                        "coordinateSystem porousBlockage; } }\n"
                    ),
                ),
                GeneratedFile(
                    path="constant/coordinateSystems",
                    content=(
                        "porousBlockage { type cartesian; origin (0 0 0); "
                        "coordinateRotation { type axesRotation; "
                        "e1 (1 0 0); e2 (0 1 0); } }\n"
                    ),
                ),
            ],
        }
    )


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.planning_first_party()
    descriptor = foundation10_porous_descriptor("porousBlockage", "inlet")
    registry.register(descriptor, object())
    return registry


def test_porous_extension_is_selected_only_from_confirmed_geometry_role() -> None:
    selected = _production_capability_registry(_capability(), _task())
    assert FOUNDATION10_POROUS_EXTENSION_ID in selected.extension_ids()
    descriptor = selected.descriptor(FOUNDATION10_POROUS_EXTENSION_ID)
    requirements = {item.field_path: item for item in descriptor.required_facts}
    assert requirements["regions.porousBlockage.role"].resolution == "user_or_asset"
    assert requirements["time.end"].resolution == "designer_candidate"

    unconfirmed = _production_capability_registry(
        _capability(), _task(confirmed=False)
    )
    assert FOUNDATION10_POROUS_EXTENSION_ID not in unconfirmed.extension_ids()


def test_porous_author_target_requires_model_dictionaries() -> None:
    target = _author_target_facts(
        task=_task(),
        design=_porous_design(),
        capability=_capability(),
        extensions=_registry(),
    )

    assert "constant/fvModels" in target.required_authored_paths
    assert "constant/coordinateSystems" in target.required_authored_paths
    assert "constant/momentumTransport" in target.required_authored_paths
    assert any(
        "selectionMode and cellZone directly inside"
        in rule
        and "explicitPorositySourceCoeffs"
        in rule
        for rule in target.extension_authoring_rules
    )
    assert any(
        "plain d and f vectors" in rule
        for rule in target.extension_authoring_rules
    )


def test_porous_descriptor_owns_its_required_authored_paths() -> None:
    descriptor = foundation10_porous_descriptor("porousBlockage", "inlet")

    assert descriptor.required_authored_paths == (
        "constant/coordinateSystems",
        "constant/fvModels",
    )
    assert descriptor.authoring_rules


def test_pisofoam_family_contract_requires_momentum_transport() -> None:
    contract = family_contract("pisoFoam")

    assert contract is not None
    assert "constant/momentumTransport" in contract.required_files


def test_porous_extension_projects_supported_aggregate_candidate_shapes() -> None:
    proposal = _porous_design().proposal
    porous_decision = next(
        item
        for item in proposal.extension_decisions
        if item.extension_id == FOUNDATION10_POROUS_EXTENSION_ID
    )
    aggregate = proposal.model_copy(
        update={
            "materials": (
                _fact(
                    "materials.fluid",
                    {
                        "rheology": "newtonian_constant_viscosity",
                        "kinematic_viscosity": {
                            "value": 1.0e-5,
                            "units": "m2/s",
                        },
                    },
                ),
            ),
            "boundary_designs": (
                _fact(
                    "boundaries.inlet.velocity",
                    {
                        "type": "fixed_uniform_velocity",
                        "vector": [0.001, 0.0, 0.0],
                        "units": "m/s",
                    },
                ),
            ),
            "time_design": (
                _fact("time.delta_t", {"value": 5.0, "units": "s"}),
                _fact("time.end", {"value": 24000.0, "units": "s"}),
            ),
            "numerical_design": (
                _fact("numerics.delta_t", {"value": 5.0, "units": "s"}),
            ),
            "region_models": (
                _fact(
                    "region_models.porousBlockage",
                    {"model": "explicitPorositySource/DarcyForchheimer"},
                ),
                _fact("regions.porousBlockage.role", "porous"),
            ),
            "extension_decisions": (
                *(
                    item
                    for item in proposal.extension_decisions
                    if item.extension_id != FOUNDATION10_POROUS_EXTENSION_ID
                ),
                porous_decision.model_copy(
                    update={
                        "values": (
                            _fact(
                                "regions.porousBlockage.porosity_model",
                                "explicitPorositySource/DarcyForchheimer",
                            ),
                            _fact(
                                "regions.porousBlockage.darcy_coefficient",
                                {
                                    "tensor_diagonal": [1.0e5, 1.0e5, 1.0e5],
                                    "units": "1/m2",
                                },
                            ),
                            _fact(
                                "regions.porousBlockage.forchheimer_coefficient",
                                {
                                    "tensor_diagonal": [0.0, 0.0, 0.0],
                                    "units": "1/m",
                                },
                            ),
                        )
                    }
                ),
            ),
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        aggregate,
        cell_zone="porousBlockage",
    )
    facts = {item.field_path: item.value for item in canonical.iter_values()}

    assert facts["materials.fluid.nu"] == {"value": 1.0e-5, "unit": "m2/s"}
    assert facts["boundaries.inlet.velocity"] == {
        "value": [0.001, 0.0, 0.0],
        "unit": "m/s",
    }
    assert facts["regions.porousBlockage.porosity_model"] == "DarcyForchheimer"
    assert facts["regions.porousBlockage.darcy_coefficient"] == {
        "value": 1.0e5,
        "unit": "1/m2",
    }
    assert facts["regions.porousBlockage.forchheimer_coefficient"] == {
        "value": 0.0,
        "unit": "1/m",
    }
    assert "materials.fluid" not in facts
    assert "region_models.porousBlockage" not in facts
    assert "time.delta_t" not in facts
    assert canonical.uncertainties[0].candidates[0].value == {
        "value": [0.001, 0.0, 0.0],
        "unit": "m/s",
    }
    assert [item.field_path for item in canonical.uncertainties] == [
        "boundaries.inlet.velocity"
    ]
    assert any(
        item.kind == "design_reporting_limitation"
        and "observations.porous_upstream_downstream_sampling" in item.detail
        for item in canonical.reasoning_evidence
    )


def test_porous_extension_lifts_common_required_facts_from_decision() -> None:
    proposal = _porous_design().proposal
    porous_decision = next(
        item
        for item in proposal.extension_decisions
        if item.extension_id == FOUNDATION10_POROUS_EXTENSION_ID
    )
    moved_paths = {
        "materials.fluid.nu",
        "boundaries.inlet.velocity",
        "time.end",
        "numerics.delta_t",
        "regions.porousBlockage.role",
        "regions.porousBlockage.porosity_model",
        "regions.porousBlockage.darcy_coefficient",
        "regions.porousBlockage.forchheimer_coefficient",
    }
    moved = tuple(
        item for item in proposal.iter_values() if item.field_path in moved_paths
    )
    aggregate = proposal.model_copy(
        update={
            "materials": (),
            "boundary_designs": (),
            "time_design": (),
            "numerical_design": (),
            "region_models": (),
            "extension_decisions": tuple(
                item.model_copy(update={"values": moved})
                if item.extension_id == porous_decision.extension_id
                else item
                for item in proposal.extension_decisions
            ),
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        aggregate,
        cell_zone="porousBlockage",
    )
    facts = {item.field_path: item.value for item in canonical.iter_values()}
    decision = next(
        item
        for item in canonical.extension_decisions
        if item.extension_id == FOUNDATION10_POROUS_EXTENSION_ID
    )

    assert moved_paths <= set(facts)
    assert {item.field_path for item in decision.values} == {
        "regions.porousBlockage.porosity_model",
        "regions.porousBlockage.darcy_coefficient",
        "regions.porousBlockage.forchheimer_coefficient",
    }


def test_porous_extension_projects_real_designer_vector_shapes() -> None:
    proposal = _porous_design().proposal
    porous_decision = next(
        item
        for item in proposal.extension_decisions
        if item.extension_id == FOUNDATION10_POROUS_EXTENSION_ID
    )
    proposal = proposal.model_copy(
        update={
            "region_models": (
                _fact("regions.porousBlockage.role", "porous"),
                _fact(
                    "regions.porousBlockage.porosity_model",
                    {
                        "resistance_law": "DarcyForchheimer",
                        "selection": "cellZone",
                        "source_type": "explicitPorositySource",
                    },
                ),
                _fact(
                    "regions.porousBlockage.darcy_coefficient",
                    {
                        "coordinate_basis": "cartesian",
                        "unit": "1/m2",
                        "vector": [1000.0, 1000.0, 1000.0],
                    },
                ),
                _fact(
                    "regions.porousBlockage.forchheimer_coefficient",
                    {
                        "enabled": False,
                        "unit": "1/m",
                        "vector": [0.0, 0.0, 0.0],
                    },
                ),
            ),
            "extension_decisions": tuple(
                item.model_copy(update={"values": ()})
                if item.extension_id == FOUNDATION10_POROUS_EXTENSION_ID
                else item
                for item in proposal.extension_decisions
            ),
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        proposal,
        cell_zone="porousBlockage",
    )
    facts = {item.field_path: item.value for item in canonical.iter_values()}

    assert facts["regions.porousBlockage.porosity_model"] == "DarcyForchheimer"
    assert facts["regions.porousBlockage.darcy_coefficient"] == {
        "value": 1000.0,
        "unit": "1/m2",
    }
    assert facts["regions.porousBlockage.forchheimer_coefficient"] == {
        "value": 0.0,
        "unit": "1/m",
    }


def test_porous_projection_does_not_invent_missing_units() -> None:
    base = _porous_design().proposal
    proposal = base.model_copy(
        update={
            "materials": (
                _fact(
                    "materials.fluid",
                    {"kinematic_viscosity": {"value": 1.0e-5}},
                ),
            ),
            "boundary_designs": (
                _fact(
                    "boundaries.inlet.velocity",
                    {"vector": [0.001, 0.0, 0.0]},
                ),
            ),
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        proposal,
        cell_zone="porousBlockage",
    )
    facts = {item.field_path: item.value for item in canonical.iter_values()}

    assert "materials.fluid.nu" not in facts
    assert facts["materials.fluid"] == {
        "kinematic_viscosity": {"value": 1.0e-5}
    }
    assert facts["boundaries.inlet.velocity"] == {
        "vector": [0.001, 0.0, 0.0]
    }


def test_porous_projection_rejects_conflicting_alias_representations() -> None:
    base = _porous_design().proposal
    proposal = base.model_copy(
        update={
            "region_models": (
                _fact("regions.porousBlockage.role", "porous"),
                _fact(
                    "regions.porousBlockage.porosity_model",
                    {
                        "source_type": "explicitPorositySource",
                        "model": "codedSource",
                        "resistance_law": "DarcyForchheimer",
                        "selection": "cellZone",
                    },
                ),
                _fact(
                    "regions.porousBlockage.darcy_coefficient",
                    {
                        "value": 1000.0,
                        "vector": [2000.0, 2000.0, 2000.0],
                        "unit": "1/m2",
                    },
                ),
            ),
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        proposal,
        cell_zone="porousBlockage",
    )
    facts = {item.field_path: item.value for item in canonical.iter_values()}

    assert isinstance(facts["regions.porousBlockage.porosity_model"], dict)
    assert isinstance(facts["regions.porousBlockage.darcy_coefficient"], dict)


def test_porous_projection_rejects_unsupported_units_and_incomplete_model() -> None:
    base = _porous_design().proposal
    proposal = base.model_copy(
        update={
            "boundary_designs": (
                _fact(
                    "boundaries.inlet.velocity",
                    {"vector": [1.0, 0.0, 0.0], "unit": "ft/s"},
                ),
            ),
            "region_models": (
                _fact("regions.porousBlockage.role", "porous"),
                _fact(
                    "regions.porousBlockage.porosity_model",
                    "explicitPorositySource",
                ),
                _fact(
                    "regions.porousBlockage.darcy_coefficient",
                    {"value": 1000.0, "unit": "1/ft2"},
                ),
            ),
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        proposal,
        cell_zone="porousBlockage",
    )
    facts = {item.field_path: item.value for item in canonical.iter_values()}

    assert facts["boundaries.inlet.velocity"] == {
        "vector": [1.0, 0.0, 0.0],
        "unit": "ft/s",
    }
    assert (
        facts["regions.porousBlockage.porosity_model"]
        == "explicitPorositySource"
    )
    assert facts["regions.porousBlockage.darcy_coefficient"] == {
        "value": 1000.0,
        "unit": "1/ft2",
    }


def test_porous_extension_downgrades_non_authoring_mesh_metadata_questions() -> None:
    proposal = _porous_design().proposal.model_copy(
        update={
            "uncertainties": (
                Uncertainty(
                    question_id="confirm_minimum_cell_length",
                    field_path="mesh.minimum_cell_length",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认最小单元尺度？",
                    reason_zh="仅用于估计 Courant 数。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="inferred_spacing",
                            value={"value": 0.125, "unit": "m"},
                            rationale="由规则网格计数推断。",
                            evidence=(
                                FactEvidence(
                                    kind="mesh_topology_inference",
                                    detail="inferred from counts and bounds",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="provide_porous_zone_extent",
                    field_path=(
                        "mesh.cell_zones.porousBlockage.spatial_extent"
                    ),
                    impact="medium",
                    kind="information_required",
                    prompt_zh="提供多孔区空间范围？",
                    reason_zh="仅用于给输出时刻标注事件。",
                ),
            )
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        proposal,
        cell_zone="porousBlockage",
    )

    assert canonical.uncertainties == ()
    limitations = [
        item.detail
        for item in canonical.reasoning_evidence
        if item.kind == "design_reporting_limitation"
    ]
    assert any("mesh.minimum_cell_length" in item for item in limitations)
    assert any(
        "mesh.cell_zones.porousBlockage.spatial_extent" in item
        for item in limitations
    )


def test_porous_extension_projects_candidate_only_required_values() -> None:
    base = _porous_design().proposal
    proposal = base.model_copy(
        update={
            "boundary_designs": (),
            "region_models": tuple(
                item
                for item in base.region_models
                if item.field_path
                not in {
                    "regions.porousBlockage.porosity_model",
                    "regions.porousBlockage.darcy_coefficient",
                    "regions.porousBlockage.forchheimer_coefficient",
                }
            ),
            "extension_decisions": tuple(
                item.model_copy(update={"values": ()})
                if item.extension_id == FOUNDATION10_POROUS_EXTENSION_ID
                else item
                for item in base.extension_decisions
            ),
            "uncertainties": (
                Uncertainty(
                    question_id="confirm_inlet_velocity",
                    field_path="boundaries.inlet.velocity",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认入口速度？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="inlet_velocity",
                            value={
                                "unit": "m/s",
                                "vector": [0.001, 0.0, 0.0],
                            },
                            rationale="低速入口。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="low-speed inlet",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_porosity_model",
                    field_path="regions.porousBlockage.porosity_model",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认多孔模型？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="explicit_porosity_source",
                            value={
                                "coordinate_system": "porousAxes",
                                "resistance_model": "DarcyForchheimer",
                                "selection_mode": "cellZone",
                                "source_type": "explicitPorositySource",
                            },
                            rationale="Foundation 10 体积源。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="selected bounded porous capability",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_darcy",
                    field_path="regions.porousBlockage.darcy_coefficient",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认 Darcy 系数？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="darcy_isotropic",
                            value={
                                "unit": "1/m2",
                                "coordinate_basis": "global_cartesian",
                                "value": [1.0e5, 1.0e5, 1.0e5],
                            },
                            rationale="各向同性基线。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="isotropic Darcy baseline",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_forchheimer",
                    field_path=(
                        "regions.porousBlockage.forchheimer_coefficient"
                    ),
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认 Forchheimer 系数？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="forchheimer_zero",
                            value={
                                "unit": "1/m",
                                "coordinate_basis": "global_cartesian",
                                "diagonal": [0.0, 0.0, 0.0],
                            },
                            rationale="Darcy 主导。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="Darcy-dominated baseline",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_nu_alias",
                    field_path="materials.kinematic_viscosity",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认运动黏度？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="nu_alias",
                            value={"value": 1.0e-4, "unit": "m2/s"},
                            rationale="层流基线。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="laminar viscosity baseline",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_end_alias",
                    field_path="time.end_time",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认结束时间？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="end_alias",
                            value={"value": 2400.0, "unit": "s"},
                            rationale="三个对流时间。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="three convective times",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_delta_t_alias",
                    field_path="time.time_step_control",
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认时间步？",
                    reason_zh="模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="delta_t_alias",
                            value={
                                "delta_t": {"value": 0.5, "unit": "s"},
                                "maximum_courant": 0.5,
                                "mode": "fixed",
                            },
                            rationale="固定小时间步。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="bounded Courant baseline",
                                ),
                            ),
                        ),
                    ),
                ),
                Uncertainty(
                    question_id="confirm_redundant_darcy_alias",
                    field_path=(
                        "region_models.porousBlockage.darcy_resistance"
                    ),
                    impact="high",
                    kind="confirmable",
                    prompt_zh="确认 Darcy 阻力？",
                    reason_zh="冗余模型候选。",
                    candidates=(
                        DesignCandidate(
                            candidate_id="redundant_darcy_alias",
                            value={
                                "value": [1.0e5, 1.0e5, 1.0e5],
                                "unit": "1/m2",
                            },
                            rationale="各向同性基线。",
                            evidence=(
                                FactEvidence(
                                    kind="design_basis",
                                    detail="redundant Darcy alias",
                                ),
                            ),
                        ),
                    ),
                ),
            )
        }
    )

    canonical = canonicalize_foundation10_porous_proposal(
        proposal,
        cell_zone="porousBlockage",
        inlet_patch="inlet",
    )
    candidates = {
        item.field_path: item.candidates[0].value
        for item in canonical.uncertainties
    }

    assert candidates["boundaries.inlet.velocity"] == {
        "value": [0.001, 0.0, 0.0],
        "unit": "m/s",
    }
    assert candidates["regions.porousBlockage.porosity_model"] == (
        "DarcyForchheimer"
    )
    assert candidates["regions.porousBlockage.darcy_coefficient"] == {
        "value": 1.0e5,
        "unit": "1/m2",
    }
    assert candidates["regions.porousBlockage.forchheimer_coefficient"] == {
        "value": 0.0,
        "unit": "1/m",
    }
    assert candidates["materials.fluid.nu"] == {
        "value": 1.0e-4,
        "unit": "m2/s",
    }
    assert candidates["time.end"] == {"value": 2400.0, "unit": "s"}
    assert candidates["numerics.delta_t"] == {"value": 0.5, "unit": "s"}
    assert "materials.kinematic_viscosity" not in candidates
    assert "time.end_time" not in candidates
    assert "time.time_step_control" not in candidates
    assert "region_models.porousBlockage.darcy_resistance" not in candidates


def test_planning_completion_replaces_model_owned_system_extension_values() -> None:
    base_proposal = _porous_design().proposal
    bridge = ExtensionDecision(
        extension_id="foampilot.bridge.solver.pisofoam",
        schema_version=1,
        values=(),
        provenance=(
            FactEvidence(kind="test_fact", detail="selected solver bridge"),
        ),
    )
    proposal = base_proposal.model_copy(
        update={
            "boundary_designs": (
                _fact(
                    "boundaries.inlet.startup_profile",
                    {
                        "duration_s": 10.0,
                        "profile": "smooth_ramp",
                    },
                ),
                _fact(
                    "boundaries.inlet.velocity",
                    {"value": [0.05, 0.0, 0.0], "unit": "m/s"},
                ),
            ),
            "extension_decisions": tuple(
                item.model_copy(
                    update={
                        "values": (
                            (
                                *item.values,
                                _fact(
                                    "porous.implementation",
                                    "model-owned-value",
                                ),
                            )
                            if item.extension_id
                            == FOUNDATION10_POROUS_EXTENSION_ID
                            else (
                                _fact(
                                    {
                                        "foampilot.mesh.openfoam-provided": (
                                            "mesh.workflow"
                                        ),
                                        "foampilot.solver.foundation10-serial": (
                                            "execution.mode"
                                        ),
                                    }[item.extension_id],
                                    "model-owned-value",
                                ),
                            )
                        )
                    }
                )
                for item in base_proposal.extension_decisions
            )
            + (
                bridge.model_copy(
                    update={"values": (_fact("application", "wrong-solver"),)}
                ),
            ),
        }
    )
    registry = _production_capability_registry(_capability(), _task())

    completed = _complete_planning_extensions(
        proposal,
        registry=registry,
        task=_task(),
        capability=_capability(),
    )
    decisions = {
        item.extension_id: {fact.field_path: fact for fact in item.values}
        for item in completed.extension_decisions
    }

    assert decisions["foampilot.bridge.solver.pisofoam"] == {}
    assert decisions["foampilot.mesh.openfoam-provided"]["mesh.strategy"].value == (
        "provided"
    )
    assert decisions["foampilot.mesh.openfoam-provided"][
        "mesh.strategy"
    ].confirmed
    assert decisions["foampilot.solver.foundation10-serial"][
        "execution.mpi_ranks"
    ].value == 1
    assert decisions[FOUNDATION10_POROUS_EXTENSION_ID].keys() == {
        "regions.porousBlockage.porosity_model",
        "regions.porousBlockage.darcy_coefficient",
        "regions.porousBlockage.forchheimer_coefficient",
    }
    assert all(
        item.field_path != "boundaries.inlet.startup_profile"
        for item in completed.boundary_designs
    )


def test_planning_completion_restores_bridge_for_frozen_solver() -> None:
    proposal = _porous_design().proposal.model_copy(
        update={
            "extension_decisions": tuple(
                item
                for item in _porous_design().proposal.extension_decisions
                if not item.extension_id.startswith(
                    "foampilot.bridge.solver."
                )
            )
        }
    )
    registry = _production_capability_registry(_capability(), _task())

    completed = _complete_planning_extensions(
        proposal,
        registry=registry,
        task=_task(),
        capability=_capability(),
    )
    decisions = {
        item.extension_id: item for item in completed.extension_decisions
    }

    bridge = decisions["foampilot.bridge.solver.pisofoam"]
    assert bridge.values == ()
    assert bridge.provenance[0].kind == "deterministic_capability"


def test_planning_completion_binds_confirmed_case_only_control_to_runner() -> None:
    task = _task().model_copy(
        update={
            "explicit_facts": (
                *_task().explicit_facts,
                ResolvedValue.model_validate(
                    resolved_fact("execution.run_solver", False)
                ),
            )
        }
    )
    base = _porous_design().proposal
    proposal = base.model_copy(
        update={
            "numerical_design": (
                *base.numerical_design,
                _fact("execution.run_solver", True),
            )
        }
    )
    registry = _production_capability_registry(_capability(), task)

    completed = _complete_planning_extensions(
        proposal,
        registry=registry,
        task=task,
        capability=_capability(),
    )
    runner = next(
        item
        for item in completed.extension_decisions
        if item.extension_id == "foampilot.solver.foundation10-serial"
    )

    run_facts = [
        item
        for item in completed.iter_values()
        if item.field_path == "execution.run_solver"
    ]
    assert run_facts == [
        next(
            item
            for item in runner.values
            if item.field_path == "execution.run_solver"
        )
    ]
    assert run_facts[0].value is False
    assert run_facts[0].confirmed is True


def test_planning_completion_uses_the_confirmed_inlet_name_for_projection() -> None:
    task = _task()
    assert task.geometry is not None
    geometry = task.geometry.model_copy(
        update={
            "patch_roles": [
                item.model_copy(update={"name": "feed"})
                if item.role == "inlet"
                else item
                for item in task.geometry.patch_roles
            ]
        }
    )
    task = task.model_copy(
        update={
            "explicit_facts": [
                item.model_copy(update={"value": geometry.model_dump(mode="json")})
                if item.field_path == "geometry.input"
                else item
                for item in task.explicit_facts
            ]
        }
    )
    proposal = _porous_design().proposal.model_copy(
        update={
            "boundary_designs": (
                _fact(
                    "boundaries.feed.velocity",
                    {"vector": [0.001, 0.0, 0.0], "units": "m/s"},
                ),
            )
        }
    )
    registry = _production_capability_registry(_capability(), task)

    completed = _complete_planning_extensions(
        proposal,
        registry=registry,
        task=task,
        capability=_capability(),
    )
    facts = {item.field_path: item.value for item in completed.iter_values()}

    assert facts["boundaries.feed.velocity"] == {
        "value": [0.001, 0.0, 0.0],
        "unit": "m/s",
    }


def test_porous_conformance_blocks_missing_or_wrong_model_source() -> None:
    design = _porous_design()
    bundle = _porous_bundle()
    missing = bundle.model_copy(
        update={
            "files": [item for item in bundle.files if item.path != "constant/fvModels"]
        }
    )
    wrong = bundle.model_copy(
        update={
            "files": [
                item.model_copy(
                    update={"content": item.content.replace("porousBlockage", "wrongZone")}
                )
                if item.path == "constant/fvModels"
                else item
                for item in bundle.files
            ]
        }
    )

    missing_report = verify_design_conformance(
        design=design, bundle=missing, mesh_facts=(_mesh(),), extensions=_registry()
    )
    wrong_report = verify_design_conformance(
        design=design, bundle=wrong, mesh_facts=(_mesh(),), extensions=_registry()
    )

    assert "DESIGN_CONFORMANCE_POROUS_MODEL_MISSING" in {
        item.code for item in missing_report.issues
    }
    assert "DESIGN_CONFORMANCE_POROUS_ZONE_MISMATCH" in {
        item.code for item in wrong_report.issues
    }


@pytest.mark.parametrize(
    "content",
    [
        (
            "source { type explicitPorositySource; } "
            "coeffs { selectionMode cellZone; cellZone porousBlockage; "
            "type DarcyForchheimer; d (1000 1000 1000); f (0 0 0); "
            "coordinateSystem porousBlockage; }\n"
        ),
        (
            "source { type explicitPorositySource; "
            "explicitPorositySourceCoeffs { selectionMode all; "
            "cellZone porousBlockage; type DarcyForchheimer; "
            "d (1000 1000 1000); f (0 0 0); "
            "coordinateSystem porousBlockage; } }\n"
        ),
        (
            "source { type explicitPorositySource; cellZone porousBlockage; "
            "type DarcyForchheimer; d (1000 1000 1000); f (0 0 0); "
            "coordinateSystem porousBlockage; "
            "explicitPorositySourceCoeffs { selectionMode cellZone; } }\n"
        ),
        (
            "a { type explicitPorositySource; explicitPorositySourceCoeffs { "
            "selectionMode cellZone; cellZone porousBlockage; "
            "type DarcyForchheimer; d (1000 1000 1000); f (0 0 0); "
            "coordinateSystem porousBlockage; } } "
            "b { type explicitPorositySource; explicitPorositySourceCoeffs { "
            "selectionMode cellZone; cellZone porousBlockage; "
            "type DarcyForchheimer; d (1000 1000 1000); f (0 0 0); "
            "coordinateSystem porousBlockage; } }\n"
        ),
        (
            "source { type explicitPorositySource; type codedSource; "
            "explicitPorositySourceCoeffs { selectionMode cellZone; "
            "selectionMode all; cellZone porousBlockage; "
            "coordinateSystem porousBlockage; type DarcyForchheimer; "
            "d (1000 1000 1000); d (999 999 999); f (0 0 0); } }\n"
        ),
    ],
)
def test_porous_conformance_requires_one_coherent_nested_source(
    content: str,
) -> None:
    source = _porous_bundle()
    bundle = source.model_copy(
        update={
            "files": [
                item.model_copy(update={"content": content})
                if item.path == "constant/fvModels"
                else item
                for item in source.files
            ]
        }
    )

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle,
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_MODEL_MISMATCH" in {
        item.code for item in report.issues
    }


def test_porous_conformance_rejects_duplicate_direct_coefficient_keys() -> None:
    content = (
        "source { type explicitPorositySource; "
        "explicitPorositySourceCoeffs { selectionMode cellZone; "
        "type DarcyForchheimer; cellZone porousBlockage; "
        "cellZone porousBlockage; coordinateSystem porousBlockage; "
        "coordinateSystem porousBlockage; d (1000 1000 1000); "
        "d (1000 1000 1000); f (0 0 0); f (0 0 0); } }\n"
    )
    source = _porous_bundle()
    bundle = source.model_copy(
        update={
            "files": [
                item.model_copy(update={"content": content})
                if item.path == "constant/fvModels"
                else item
                for item in source.files
            ]
        }
    )

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle,
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )
    codes = {item.code for item in report.issues}

    assert {
        "DESIGN_CONFORMANCE_POROUS_ZONE_MISMATCH",
        "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_MISSING",
        "DESIGN_CONFORMANCE_POROUS_DARCY_MISMATCH",
        "DESIGN_CONFORMANCE_POROUS_FORCHHEIMER_MISMATCH",
    } <= codes


def test_matching_porous_bundle_and_complete_patch_manifest_conform() -> None:
    report = verify_design_conformance(
        design=_porous_design(),
        bundle=_porous_bundle(),
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert report.passed is True


def test_porous_coordinate_system_may_have_a_distinct_referenced_name() -> None:
    bundle = _porous_bundle()
    files = [
        item.model_copy(
            update={
                "content": item.content.replace(
                    "coordinateSystem porousBlockage;",
                    "coordinateSystem globalCartesian;",
                )
            }
        )
        if item.path == "constant/fvModels"
        else item.model_copy(
            update={
                "content": item.content.replace(
                    "porousBlockage {",
                    "globalCartesian {",
                )
            }
        )
        if item.path == "constant/coordinateSystems"
        else item
        for item in bundle.files
    ]

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle.model_copy(update={"files": files}),
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_MISSING" not in {
        item.code for item in report.issues
    }


def test_porous_coordinate_system_reference_must_resolve() -> None:
    bundle = _porous_bundle()
    files = [
        item.model_copy(
            update={
                "content": item.content.replace(
                    "coordinateSystem porousBlockage;",
                    "coordinateSystem missingCartesian;",
                )
            }
        )
        if item.path == "constant/fvModels"
        else item
        for item in bundle.files
    ]

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle.model_copy(update={"files": files}),
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_MISSING" in {
        item.code for item in report.issues
    }


@pytest.mark.parametrize(
    "content",
    [
        "porousBlockage { nonsense broken; }\n",
        (
            "porousBlockage { type cartesian; type cartesian; "
            "origin (0 0 0); coordinateRotation { type axesRotation; "
            "e1 (1 0 0); e2 (0 1 0); } }\n"
        ),
        (
            "porousBlockage { type cartesian; coordinateRotation { "
            "type axesRotation; e1 (1 0 0); e2 (0 1 0); } }\n"
        ),
        (
            "porousBlockage { type cartesian; origin (0 0 0); "
            "coordinateRotation { type axesRotation; "
            "e1 (0 0 0); e2 (0 1 0); } }\n"
        ),
        (
            "porousBlockage { type cartesian; origin (0 0 0); "
            "coordinateRotation { type axesRotation; "
            "e1 (1 0 0); e2 (2 0 0); } }\n"
        ),
    ],
)
def test_porous_coordinate_system_requires_valid_foundation10_structure(
    content: str,
) -> None:
    source = _porous_bundle()
    bundle = source.model_copy(
        update={
            "files": [
                item.model_copy(update={"content": content})
                if item.path == "constant/coordinateSystems"
                else item
                for item in source.files
            ]
        }
    )

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle,
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_INVALID" in {
        item.code for item in report.issues
    }


def test_porous_coordinate_system_must_match_frozen_axes() -> None:
    source = _porous_bundle()
    bundle = source.model_copy(
        update={
            "files": [
                item.model_copy(
                    update={
                        "content": (
                            "porousBlockage { type cartesian; origin (0 0 0); "
                            "coordinateRotation { type axesRotation; "
                            "e1 (0 1 0); e2 (0 0 1); } }\n"
                        )
                    }
                )
                if item.path == "constant/coordinateSystems"
                else item
                for item in source.files
            ]
        }
    )

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle,
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_MISMATCH" in {
        item.code for item in report.issues
    }


def test_porous_conformance_does_not_treat_outlet_behavior_as_inlet_vector() -> None:
    base = _porous_design()
    proposal = base.proposal.model_copy(
        update={
            "boundary_designs": (
                *base.proposal.boundary_designs,
                _fact(
                    "boundaries.outlet.velocity",
                    {"behavior": "zero_normal_gradient"},
                ),
            )
        }
    )
    decision = RiskDecision(
        state="READY_TO_AUTHOR",
        questions=(),
        reason_codes=("DESIGN_FACTS_RESOLVED",),
        proposal_sha256=canonical_sha256(proposal),
        required_extension_ids=tuple(sorted(base.extension_identities)),
        required_extension_identities=base.extension_identities,
    )
    design = freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=SimulationIntent(),
    )

    report = verify_design_conformance(
        design=design,
        bundle=_porous_bundle(),
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_INLET_VELOCITY_MISMATCH" not in {
        item.code for item in report.issues
    }


def test_porous_conformance_blocks_inlet_velocity_drift() -> None:
    bundle = _porous_bundle()
    wrong = bundle.model_copy(
        update={
            "files": [
                item.model_copy(
                    update={
                        "content": item.content.replace(
                            "value uniform (1 0 0)",
                            "value uniform (2 0 0)",
                        )
                    }
                )
                if item.path == "0/U"
                else item
                for item in bundle.files
            ]
        }
    )

    report = verify_design_conformance(
        design=_porous_design(),
        bundle=wrong,
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_POROUS_INLET_VELOCITY_MISMATCH" in {
        item.code for item in report.issues
    }


def test_provided_mesh_patch_may_not_be_omitted_from_manifest() -> None:
    bundle = _porous_bundle()
    manifest = bundle.manifest.model_copy(update={"patches": []})
    report = verify_design_conformance(
        design=_porous_design(),
        bundle=bundle.model_copy(update={"manifest": manifest}),
        mesh_facts=(_mesh(),),
        extensions=_registry(),
    )

    assert "DESIGN_CONFORMANCE_INPUT_PATCH_MISSING" in {
        item.code for item in report.issues
    }


def test_pisofoam_has_a_blocking_family_contract() -> None:
    contract = family_contract("pisoFoam")
    assert contract is not None
    assert "constant/physicalProperties" in contract.required_files
    assert contract.required_field_names == ("U", "p")
