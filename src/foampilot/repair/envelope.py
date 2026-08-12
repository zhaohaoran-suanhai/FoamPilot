"""Deterministic authorization for frozen repair envelopes."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .models import RepairAuthorization, RepairPolicy, RepairProposal

if TYPE_CHECKING:
    from foampilot.simulation.risk_gate import CaseDesign


_PHYSICAL_PREFIXES = (
    "materials.",
    "boundaries.",
    "initial.",
    "regions.",
    "region_models.",
    "physics.",
    "solver.",
    "mesh.",
    "time.",
)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _same(left: object, right: object) -> bool:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is not None and right_number is not None:
        return math.isclose(
            left_number,
            right_number,
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        )
    return left == right


def authorize_repair(
    *,
    proposal: RepairProposal,
    design: CaseDesign,
    policy: RepairPolicy,
) -> RepairAuthorization:
    """Authorize numerical changes only; physical changes need confirmation."""

    physical = tuple(
        item.field_path
        for item in proposal.design_changes
        if item.field_path.startswith(_PHYSICAL_PREFIXES)
        and not item.field_path.startswith("numerics.")
    )
    if physical:
        return RepairAuthorization(
            state="CONFIRMATION_REQUIRED",
            reason_codes=("PHYSICAL_CHANGE_REQUIRES_CONFIRMATION",),
            confirmation_paths=physical,
        )

    if proposal.category != "numerical":
        return RepairAuthorization(
            state="FINALIZE_FAILED",
            reason_codes=("REPAIR_CATEGORY_NOT_AUTOMATIC",),
        )
    if not policy.automatic_numerical_repair:
        return RepairAuthorization(
            state="FINALIZE_FAILED",
            reason_codes=("AUTOMATIC_NUMERICAL_REPAIR_DISABLED",),
        )

    facts = {
        item.field_path: item.value for item in design.proposal.iter_values()
    }
    rules = {
        item.field_path: item
        for item in design.numerical_repair_envelope.rules
    }
    reasons: list[str] = []
    authorized: list[str] = []
    if not proposal.design_changes:
        reasons.append("REPAIR_DESIGN_CHANGE_MISSING")

    for change in proposal.design_changes:
        rule = rules.get(change.field_path)
        if rule is None:
            reasons.append("REPAIR_FIELD_NOT_IN_ENVELOPE")
            continue
        if change.operator not in rule.operators:
            reasons.append("REPAIR_OPERATOR_NOT_ALLOWED")
        frozen = facts.get(change.field_path, object())
        if not _same(frozen, change.old_value):
            reasons.append("REPAIR_OLD_VALUE_MISMATCH")
        old = _number(change.old_value)
        new = _number(change.new_value)
        if old is None or new is None:
            reasons.append("REPAIR_NON_NUMERICAL_VALUE")
            continue
        if rule.direction == "decrease" and new >= old:
            reasons.append("REPAIR_DIRECTION_VIOLATION")
        elif rule.direction == "increase" and new <= old:
            reasons.append("REPAIR_DIRECTION_VIOLATION")
        if (
            (rule.minimum is not None and new < rule.minimum)
            or (rule.maximum is not None and new > rule.maximum)
        ):
            reasons.append("REPAIR_BOUND_VIOLATION")
        authorized.append(change.field_path)

    if reasons:
        return RepairAuthorization(
            state="FINALIZE_FAILED",
            reason_codes=tuple(dict.fromkeys(reasons)),
        )
    return RepairAuthorization(
        state="AUTHORIZED_AUTOMATIC",
        reason_codes=("NUMERICAL_REPAIR_WITHIN_FROZEN_ENVELOPE",),
        authorized_paths=tuple(authorized),
    )


__all__ = ["authorize_repair"]
