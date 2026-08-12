from __future__ import annotations

import pytest

from foampilot.authoring import CaseBundle
from foampilot.extensions import CapabilityRegistry
from foampilot.plans import GeneratedFile
from foampilot.repair.coordinator import (
    AuthorizedRepairResult,
    apply_authorized_repair,
    coordinate_repair,
)
from foampilot.repair.models import (
    DesignChange,
    RepairAuthorization,
    RepairFileOperation,
    RepairPolicy,
    RepairProposal,
)
from tests.test_case_author import _bundle
from tests.test_numerical_repair_envelope import _design


class NeverCalledGateway:
    def __init__(self) -> None:
        self.requests = []

    def generate_structured(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        raise AssertionError("repair model must not be called")


def _numerical_proposal(*, hidden: bool = False) -> RepairProposal:
    control = (
        "FoamFile { class dictionary; }\n"
        "application pisoFoam;\n"
        "deltaT 0.01;\n"
        "maxCo 1.0;\n"
    )
    if hidden:
        control += "endTime 999;\n"
    return RepairProposal(
        category="numerical",
        because="reduce time step after a Courant failure",
        design_changes=(
            DesignChange(
                field_path="numerics.delta_t",
                old_value=0.02,
                new_value=0.01,
                operator="replace",
            ),
        ),
        file_operations=(
            RepairFileOperation(
                operation="replace",
                path="system/controlDict",
                content=control,
            ),
        ),
        expected_checks=("rerun solver",),
    )


def _current_bundle() -> CaseBundle:
    bundle = _bundle()
    files = [
        GeneratedFile(
            path=item.path,
            content=(item.content + "deltaT 0.02;\nmaxCo 1.0;\n")
            if item.path == "system/controlDict"
            else item.content,
        )
        for item in bundle.files
    ]
    return bundle.model_copy(update={"files": files})


def test_disabled_numerical_repair_makes_zero_repair_model_calls() -> None:
    gateway = NeverCalledGateway()

    decision = coordinate_repair(
        category="numerical",
        design=_design(),
        policy=RepairPolicy(automatic_numerical_repair=False),
        gateway=gateway,
    )

    assert decision.state == "FINALIZE_FAILED"
    assert gateway.requests == []
    assert decision.reason_codes == ("AUTOMATIC_NUMERICAL_REPAIR_DISABLED",)


def test_mechanical_repair_is_deterministic_and_makes_zero_model_calls() -> None:
    gateway = NeverCalledGateway()

    decision = coordinate_repair(
        category="mechanical",
        design=_design(),
        policy=RepairPolicy(),
        gateway=gateway,
    )

    assert decision.state == "MECHANICAL_PATCH"
    assert gateway.requests == []


def test_authorized_numerical_patch_derives_design_and_rechecks_conformance() -> None:
    proposal = _numerical_proposal()
    authorization = RepairAuthorization(
        state="AUTHORIZED_AUTOMATIC",
        reason_codes=("NUMERICAL_REPAIR_WITHIN_FROZEN_ENVELOPE",),
        authorized_paths=("numerics.delta_t",),
    )

    result = apply_authorized_repair(
        proposal=proposal,
        authorization=authorization,
        design=_design(),
        bundle=_current_bundle(),
        mesh_facts=(),
        extensions=CapabilityRegistry.planning_first_party(),
        public_asset_install_paths=("constant/polyMesh",),
        protected_paths=("/private/evaluator",),
    )

    assert isinstance(result, AuthorizedRepairResult)
    assert result.derived.parent_design_sha256 == _design().design_sha256
    assert result.design.design_sha256 != _design().design_sha256
    assert result.conformance.passed
    assert "deltaT 0.01;" in result.bundle.files[0].content


def test_undeclared_file_change_is_rejected_even_with_allowed_design_change() -> None:
    with pytest.raises(ValueError, match="UNDECLARED_SEMANTIC_CHANGE"):
        apply_authorized_repair(
            proposal=_numerical_proposal(hidden=True),
            authorization=RepairAuthorization(
                state="AUTHORIZED_AUTOMATIC",
                reason_codes=("NUMERICAL_REPAIR_WITHIN_FROZEN_ENVELOPE",),
                authorized_paths=("numerics.delta_t",),
            ),
            design=_design(),
            bundle=_current_bundle(),
            mesh_facts=(),
            extensions=CapabilityRegistry.planning_first_party(),
            public_asset_install_paths=("constant/polyMesh",),
            protected_paths=(),
        )


def test_full_case_regeneration_and_mesh_overwrite_are_rejected() -> None:
    proposal = _numerical_proposal().model_copy(
        update={
            "file_operations": (
                *_numerical_proposal().file_operations,
                RepairFileOperation(
                    operation="replace",
                    path="system/fvSchemes",
                    content="unrelated rewrite\n",
                ),
            )
        }
    )

    with pytest.raises(ValueError, match="REPAIR_FILE_NOT_AUTHORIZED"):
        apply_authorized_repair(
            proposal=proposal,
            authorization=RepairAuthorization(
                state="AUTHORIZED_AUTOMATIC",
                reason_codes=("NUMERICAL_REPAIR_WITHIN_FROZEN_ENVELOPE",),
                authorized_paths=("numerics.delta_t",),
            ),
            design=_design(),
            bundle=_current_bundle(),
            mesh_facts=(),
            extensions=CapabilityRegistry.planning_first_party(),
            public_asset_install_paths=("constant/polyMesh",),
            protected_paths=(),
        )


def test_physical_proposal_returns_confirmation_without_mutation() -> None:
    proposal = _numerical_proposal().model_copy(
        update={
            "category": "physical",
            "design_changes": (
                DesignChange(
                    field_path="materials.fluid.nu",
                    old_value=1.0e-6,
                    new_value=2.0e-6,
                    operator="replace",
                ),
            ),
            "file_operations": (),
        }
    )

    decision = coordinate_repair(
        category="physical",
        design=_design(),
        policy=RepairPolicy(),
        proposal=proposal,
    )

    assert decision.state == "CONFIRMATION_REQUIRED"
    assert decision.confirmation_paths == ("materials.fluid.nu",)
