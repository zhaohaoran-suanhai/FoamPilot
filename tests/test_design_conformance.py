from __future__ import annotations

from copy import deepcopy

import pytest

from foampilot.authoring import CaseBundle
from foampilot.extensions import CapabilityRegistry
from foampilot.inspection import verify_design_conformance
from foampilot.manifests import CasePatch
from foampilot.plans import GeneratedFile
from foampilot.simulation import (
    FactEvidence,
    ResolvedValue,
    RiskDecision,
    SimulationIntent,
    canonical_sha256,
    freeze_case_design,
)
from tests.test_case_author import _bundle
from tests.test_plan_extensions import _context


def _fact(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="user_text",
        impact="high",
        evidence=(FactEvidence(kind="test_fact", detail="frozen test value"),),
        confirmed=True,
    )


def _design(**sections):
    base = _context().design
    proposal = base.proposal.model_copy(update=sections)
    decision = RiskDecision(
        state="READY_TO_AUTHOR",
        questions=(),
        reason_codes=("DESIGN_FACTS_RESOLVED",),
        proposal_sha256=canonical_sha256(proposal),
        required_extension_ids=tuple(sorted(base.extension_identities)),
        required_extension_identities=base.extension_identities,
    )
    return freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=SimulationIntent(),
    )


def _verify(design, bundle: CaseBundle):
    return verify_design_conformance(
        design=design,
        bundle=bundle,
        mesh_facts=(),
        extensions=CapabilityRegistry.planning_first_party(),
    )


def _codes(report) -> set[str]:
    return {item.code for item in report.issues}


def test_matching_bundle_conforms_to_frozen_design() -> None:
    report = _verify(_context().design, _bundle())

    assert report.passed is True


def test_bundle_cannot_change_frozen_solver() -> None:
    report = _verify(_context().design, _bundle(solver="icoFoam"))

    assert "DESIGN_CONFORMANCE_SOLVER_MISMATCH" in _codes(report)


def test_bundle_cannot_change_frozen_region_kind() -> None:
    design = _design(region_models=(_fact("regions.default.kind", "fluid"),))
    bundle = _bundle()
    manifest = bundle.manifest.model_copy(deep=True)
    manifest.regions[0].kind = "solid"

    report = _verify(design, bundle.model_copy(update={"manifest": manifest}))

    assert "DESIGN_CONFORMANCE_REGION_KIND_MISMATCH" in _codes(report)


def test_bundle_cannot_change_frozen_boundary_mesh_type() -> None:
    design = _design(
        boundary_designs=(
            _fact("boundaries.inlet.mesh_type", "patch"),
        )
    )
    bundle = _bundle()
    manifest = bundle.manifest.model_copy(
        update={
            "patches": [
                CasePatch(
                    name="inlet",
                    region="default",
                    mesh_type="wall",
                )
            ]
        }
    )

    report = _verify(design, bundle.model_copy(update={"manifest": manifest}))

    assert "DESIGN_CONFORMANCE_PATCH_TYPE_MISMATCH" in _codes(report)


def test_bundle_cannot_change_frozen_end_time() -> None:
    design = _design(time_design=(_fact("time.end_time", 40.0),))
    bundle = _bundle()
    files = deepcopy(bundle.files)
    files[0].content += "endTime 20;\n"

    report = _verify(design, bundle.model_copy(update={"files": files}))

    assert "DESIGN_CONFORMANCE_END_TIME_MISMATCH" in _codes(report)


def test_declared_transport_requires_foundation_physical_properties() -> None:
    design = _design(
        materials=(_fact("materials.fluid.nu", 5.0e-3),)
    )

    report = _verify(design, _bundle())

    assert "DESIGN_CONFORMANCE_REQUIRED_MODEL_FILE_MISSING" in _codes(report)


def test_extra_active_turbulence_model_contradicts_laminar_design() -> None:
    design = _design(
        physical_models=(_fact("physics.turbulence", "laminar"),)
    )
    bundle = _bundle()
    manifest = bundle.manifest.model_copy(
        update={
            "models": bundle.manifest.models.model_copy(
                update={"turbulence": "kEpsilon"}
            )
        }
    )

    report = _verify(design, bundle.model_copy(update={"manifest": manifest}))

    assert "DESIGN_CONFORMANCE_EXTRA_PHYSICAL_MODEL" in _codes(report)


def test_provided_mesh_bundle_cannot_be_authored_over() -> None:
    bundle = _bundle().model_copy(
        update={
            "files": [
                *_bundle().files,
                GeneratedFile(path="constant/polyMesh/points", content="bad\n"),
            ]
        }
    )

    report = _verify(_context().design, bundle)

    assert "DESIGN_CONFORMANCE_INPUT_MESH_OVERWRITE" in _codes(report)


def test_unregistered_semantic_relation_is_not_verified_not_false_pass() -> None:
    design = _design(
        numerical_design=(
            _fact("numerics.custom_unregistered_relation", "bounded"),
        )
    )

    report = _verify(design, _bundle())

    assert report.passed is True
    assert any(
        item.code == "DESIGN_CONFORMANCE_NOT_VERIFIED"
        and item.path == "numerics.custom_unregistered_relation"
        for item in report.advisories
    )
