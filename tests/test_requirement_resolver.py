from __future__ import annotations

import pytest

from foampilot.extensions import (
    CapabilityDescriptor,
    RequiredFact,
    SupportedTarget,
)
from foampilot.preprocessing import (
    BoundingBox,
    ExecutedMeshFacts,
    InputMeshFacts,
    MeshCheckFact,
    MeshQualityReport,
    MeshZoneFact,
)
from foampilot.simulation import (
    FactEvidence,
    RequirementGap,
    ResolvedValue,
    SimulationIntent,
    resolve_requirements,
)


def _fact(
    path: str,
    value: object,
    *,
    source: str = "user_text",
    impact: str = "high",
    confirmed: bool = True,
    detail: str = "explicit user fact",
) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source=source,
        impact=impact,
        evidence=(FactEvidence(kind="test_fact", detail=detail),),
        confirmed=confirmed,
    )


def _intent(*facts: ResolvedValue) -> SimulationIntent:
    return SimulationIntent(facts=facts)


def _mesh(*zones: tuple[str, int], length_unit: str = "m") -> InputMeshFacts:
    return InputMeshFacts(
        bundle_manifest_sha256="a" * 64,
        inspector_id="foampilot.mesh.poly-mesh",
        inspector_version="1.0.0",
        region=None,
        declared_length_unit=length_unit,
        source_member_sha256={"points": "b" * 64},
        points=8,
        faces=6,
        internal_faces=0,
        cells=max(1, sum(count for _, count in zones)),
        bounding_box_m=BoundingBox(
            minimum=(0, 0, 0),
            maximum=(1, 1, 1),
        ),
        patches=(),
        cell_zones=tuple(
            MeshZoneFact(name=name, element_count=count)
            for name, count in zones
        ),
        face_zones=(),
        point_zones=(),
        dimensionality_observations=(),
        topology_observations=(),
        warnings=(),
    )


def _capability(
    *requirements: RequiredFact,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        extension_id="foampilot.physics.incompressible",
        extension_version="1.0.0",
        capability_kinds=("physics:incompressible",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        required_facts=requirements,
    )


def test_user_zone_semantics_and_mesh_zone_existence_remain_distinct() -> None:
    resolved = resolve_requirements(
        intent=_intent(
            _fact("regions.porousBlockage.role", "porous_fluid")
        ),
        mesh_facts=(_mesh(("porousBlockage", 64)),),
        capabilities=(_capability(),),
    )

    assert (
        resolved.require("mesh.cell_zones.porousBlockage.count").source
        == "public_asset_fact"
    )
    assert resolved.require("regions.porousBlockage.role").source == "user_text"


def test_missing_geometry_unit_is_an_information_gap() -> None:
    resolved = resolve_requirements(
        intent=_intent(),
        mesh_facts=(),
        capabilities=(
            _capability(
                RequiredFact(
                    field_path="geometry.length_unit",
                    impact="high",
                    description="Physical length unit",
                )
            ),
        ),
    )

    gap = resolved.gaps[0]
    assert gap.field_path == "geometry.length_unit"
    assert gap.kind == "information_required"
    assert gap.candidates == ()


def test_nonexistent_zone_reference_is_a_referential_gap() -> None:
    resolved = resolve_requirements(
        intent=_intent(_fact("regions.missing.role", "porous_fluid")),
        mesh_facts=(_mesh(("porousBlockage", 64)),),
        capabilities=(_capability(),),
    )

    gap = next(item for item in resolved.gaps if item.field_path == "regions.missing.role")
    assert gap.code == "MESH_ZONE_REFERENCE_MISSING"
    assert gap.kind == "information_required"


def test_nonexistent_patch_role_is_a_referential_gap() -> None:
    resolved = resolve_requirements(
        intent=_intent(_fact("boundaries.missing.role", "inlet")),
        mesh_facts=(_mesh(("porousBlockage", 64)),),
        capabilities=(_capability(),),
    )

    gap = next(
        item for item in resolved.gaps if item.field_path == "boundaries.missing.role"
    )
    assert gap.code == "MESH_PATCH_REFERENCE_MISSING"
    assert gap.kind == "information_required"


def test_explicit_authority_wins_and_conflicts_are_preserved() -> None:
    resolved = resolve_requirements(
        intent=_intent(
            _fact(
                "physics.regime",
                "laminar",
                source="user_confirmation",
            ),
            _fact(
                "physics.regime.candidate",
                "turbulent",
                source="model_inference",
                confirmed=False,
            ),
        ),
        mesh_facts=(),
        capabilities=(
            _capability(
                RequiredFact(
                    field_path="physics.regime",
                    impact="high",
                    description="Flow regime",
                )
            ),
        ),
    )

    assert resolved.require("physics.regime").value == "laminar"
    assert resolved.require("physics.regime").source == "user_confirmation"


def test_unconfirmed_high_impact_model_inference_remains_a_gap() -> None:
    resolved = resolve_requirements(
        intent=_intent(
            _fact(
                "materials.fluid.nu",
                1e-6,
                source="model_inference",
                confirmed=False,
            )
        ),
        mesh_facts=(),
        capabilities=(
            _capability(
                RequiredFact(
                    field_path="materials.fluid.nu",
                    impact="high",
                    description="Kinematic viscosity",
                )
            ),
        ),
    )

    assert resolved.resolved == ()
    assert resolved.gaps[0].code == "HIGH_IMPACT_AUTHORITY_MISSING"
    assert resolved.gaps[0].kind == "confirmable"
    assert resolved.gaps[0].candidates[0].value == 1e-6


def test_low_impact_system_default_cannot_fill_high_impact_requirement() -> None:
    resolved = resolve_requirements(
        intent=_intent(
            _fact(
                "materials.fluid.nu",
                1e-6,
                source="system_default",
                impact="low",
                confirmed=False,
            )
        ),
        mesh_facts=(),
        capabilities=(
            _capability(
                RequiredFact(
                    field_path="materials.fluid.nu",
                    impact="high",
                    description="Kinematic viscosity",
                )
            ),
        ),
    )

    gap = resolved.gaps[0]
    assert gap.field_path == "materials.fluid.nu"
    assert gap.kind == "information_required"
    assert gap.code == "HIGH_IMPACT_AUTHORITY_MISSING"


def test_resolution_is_ordered_deduplicated_and_hash_stable() -> None:
    first = resolve_requirements(
        intent=_intent(
            _fact("physics.regime", "laminar"),
            _fact("physics.compressibility", "incompressible"),
        ),
        mesh_facts=(),
        capabilities=(_capability(),),
    )
    second = resolve_requirements(
        intent=_intent(
            _fact("physics.compressibility", "incompressible"),
            _fact("physics.regime", "laminar"),
        ),
        mesh_facts=(),
        capabilities=(_capability(),),
    )

    assert [item.field_path for item in first.resolved] == sorted(
        item.field_path for item in first.resolved
    )
    assert first.requirements_sha256 == second.requirements_sha256


def test_conflicting_equal_authority_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        SimulationIntent(
            facts=(
                _fact("physics.regime", "laminar"),
                _fact("physics.regime", "turbulent"),
            )
        )


def test_executed_mesh_probe_is_resolved_as_authoritative_fact() -> None:
    executed = ExecutedMeshFacts(
        mesh_check=MeshCheckFact(
            executed=True,
            executable_identity="/usr/bin/checkMesh",
            return_code=0,
            timed_out=False,
            mesh_ok=True,
            evidence_paths=("logs/checkMesh.log",),
        ),
        metrics=MeshQualityReport(
            strategy="provided",
            commands_completed=("checkMesh",),
            mesh_created=True,
            check_mesh_passed=True,
            patches=(),
            failed_requirements=(),
            warnings=(),
            evidence_files=("logs/checkMesh.log",),
        ),
    )

    result = resolve_requirements(
        intent=_intent(),
        mesh_facts=(),
        executed_mesh_facts=(executed,),
        capabilities=(
            _capability(
                RequiredFact(
                    field_path="mesh.check.mesh_ok",
                    impact="high",
                    description="Dynamic mesh validity",
                )
            ),
        ),
    )

    assert result.require("mesh.check.mesh_ok").value is True
    assert result.require("mesh.check.mesh_ok").source == "public_asset_fact"
    assert RequirementGap.__name__ == "RequirementGap"
