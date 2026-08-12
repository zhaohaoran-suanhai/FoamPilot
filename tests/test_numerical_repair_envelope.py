from __future__ import annotations

import pytest

from foampilot.repair.envelope import authorize_repair
from foampilot.repair.models import (
    DesignChange,
    NumericalRepairEnvelope,
    NumericalRepairRule,
    RepairPolicy,
    RepairProposal,
)
from foampilot.simulation import (
    FactEvidence,
    ResolvedValue,
    RiskDecision,
    SimulationIntent,
    canonical_sha256,
    freeze_case_design,
)
from tests.test_plan_extensions import _context


def _fact(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="user_text",
        impact="high",
        evidence=(FactEvidence(kind="test_fact", detail="frozen value"),),
        confirmed=True,
    )


def _design():
    base = _context().design
    proposal = base.proposal.model_copy(
        update={
            "numerical_design": (
                _fact("numerics.delta_t", 0.02),
                _fact("numerics.max_co", 1.0),
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
    envelope = NumericalRepairEnvelope(
        rules=(
            NumericalRepairRule(
                field_path="numerics.delta_t",
                operators=("replace", "scale"),
                direction="decrease",
                minimum=0.002,
                maximum=0.02,
                authored_paths=("system/controlDict",),
                dictionary_keyword="deltaT",
            ),
            NumericalRepairRule(
                field_path="numerics.max_co",
                operators=("replace",),
                direction="decrease",
                minimum=0.1,
                maximum=1.0,
                authored_paths=("system/controlDict",),
                dictionary_keyword="maxCo",
            ),
        )
    )
    return freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=SimulationIntent(),
        numerical_repair_envelope=envelope,
    )


def _change(
    path: str,
    *,
    old: object = 0.02,
    new: object = 0.01,
    operator: str = "replace",
) -> RepairProposal:
    return RepairProposal(
        category="numerical",
        because="bounded stability adjustment",
        design_changes=(
            DesignChange(
                field_path=path,
                old_value=old,
                new_value=new,
                operator=operator,
            ),
        ),
        file_operations=(),
        expected_checks=("rerun failed stage",),
    )


def test_smaller_delta_t_inside_envelope_is_authorized() -> None:
    result = authorize_repair(
        proposal=_change("numerics.delta_t"),
        design=_design(),
        policy=RepairPolicy(automatic_numerical_repair=True),
    )

    assert result.state == "AUTHORIZED_AUTOMATIC"


@pytest.mark.parametrize(
    "path",
    [
        "materials.fluid.nu",
        "boundaries.inlet.value",
        "region_models.porous.coefficient",
        "time.end_time",
    ],
)
def test_physical_change_always_requires_confirmation(path: str) -> None:
    result = authorize_repair(
        proposal=_change(path, old=1.0, new=0.5),
        design=_design(),
        policy=RepairPolicy(),
    )

    assert result.state == "CONFIRMATION_REQUIRED"
    assert result.confirmation_paths == (path,)


def test_disabled_automatic_repair_is_a_normal_final_failure() -> None:
    result = authorize_repair(
        proposal=_change("numerics.delta_t"),
        design=_design(),
        policy=RepairPolicy(automatic_numerical_repair=False),
    )

    assert result.state == "FINALIZE_FAILED"
    assert result.reason_codes == ("AUTOMATIC_NUMERICAL_REPAIR_DISABLED",)


@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        (_change("numerics.unknown"), "REPAIR_FIELD_NOT_IN_ENVELOPE"),
        (
            _change("numerics.delta_t", old=0.02, new=0.03),
            "REPAIR_DIRECTION_VIOLATION",
        ),
        (
            _change("numerics.delta_t", old=0.02, new=0.001),
            "REPAIR_BOUND_VIOLATION",
        ),
        (
            _change(
                "numerics.delta_t",
                old=0.02,
                new=0.01,
                operator="offset",
            ),
            "REPAIR_OPERATOR_NOT_ALLOWED",
        ),
        (
            _change("numerics.delta_t", old=0.03, new=0.01),
            "REPAIR_OLD_VALUE_MISMATCH",
        ),
    ],
)
def test_unknown_direction_bound_operator_and_old_value_are_rejected(
    proposal,
    reason,
) -> None:
    result = authorize_repair(
        proposal=proposal,
        design=_design(),
        policy=RepairPolicy(),
    )

    assert result.state == "FINALIZE_FAILED"
    assert reason in result.reason_codes


def test_one_violation_rejects_a_multi_change_proposal_atomically() -> None:
    proposal = _change("numerics.delta_t").model_copy(
        update={
            "design_changes": (
                _change("numerics.delta_t").design_changes[0],
                _change("numerics.max_co", old=1.0, new=2.0).design_changes[0],
            )
        }
    )

    result = authorize_repair(
        proposal=proposal,
        design=_design(),
        policy=RepairPolicy(),
    )

    assert result.state == "FINALIZE_FAILED"
    assert "REPAIR_DIRECTION_VIOLATION" in result.reason_codes


def test_envelope_is_part_of_frozen_design_integrity() -> None:
    design = _design()
    changed = design.model_copy(
        update={"numerical_repair_envelope": NumericalRepairEnvelope()}
    )

    assert design.recompute_sha256() == design.design_sha256
    assert changed.recompute_sha256() != design.design_sha256
