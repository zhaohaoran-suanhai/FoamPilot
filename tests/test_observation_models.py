from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.observations import (
    EvidenceStrategy,
    ObservationItem,
    ObservationPlan,
    ObservationScope,
    TimeSelection,
)
from foampilot.simulation import FactEvidence


def _item(observation_id: str = "outlet-flow") -> ObservationItem:
    return ObservationItem(
        observation_id=observation_id,
        kind="flow_rate",
        quantity="volumetric_flow_rate",
        dimension="L3/T",
        scope=ObservationScope(kind="patch", names=("outlet",)),
        time_selection=TimeSelection(kind="history"),
        evidence_strategy=EvidenceStrategy(
            kind="runtime_configuration",
            collector_id="foundation10.surface-field-value",
        ),
        provenance=(FactEvidence(kind="user_quote", detail="outlet flow history"),),
    )


def test_observation_plan_is_frozen_hashable_and_roundtrips() -> None:
    plan = ObservationPlan(items=(_item(),))

    assert plan.canonical_sha256() == plan.canonical_sha256()
    assert ObservationPlan.model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(ValidationError):
        plan.items = ()


def test_observation_plan_rejects_duplicate_ids_and_unsafe_names() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ObservationPlan(items=(_item(), _item()))
    with pytest.raises(ValidationError):
        ObservationScope(kind="patch", names=("../outlet",))


def test_observation_requires_explicit_scope_time_dimension_and_provenance() -> None:
    payload = _item().model_dump(mode="python")
    for field in ("scope", "time_selection", "dimension", "provenance"):
        invalid = dict(payload)
        invalid.pop(field)
        with pytest.raises(ValidationError):
            ObservationItem.model_validate(invalid)
