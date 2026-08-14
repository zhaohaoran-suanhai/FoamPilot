from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.acceptance import (
    AcceptanceCompiler,
    AcceptanceRequest,
    AcceptanceScope,
)
from foampilot.observations import ObservationRequest, ObservationScope, TimeSelection
from foampilot.simulation import FactEvidence


def _observation(kind: str = "continuity") -> ObservationRequest:
    return ObservationRequest(
        observation_id=kind,
        kind=kind,
        quantity=kind,
        dimension="1",
        scope=ObservationScope(kind="global"),
        time_selection=TimeSelection(kind="latest"),
        provenance=(FactEvidence(kind="user_quote", detail=kind),),
    )


def test_metric_without_limit_is_observation_only() -> None:
    compiled = AcceptanceCompiler().compile(
        observation_requests=(_observation(),),
        condition_requests=(),
    )

    assert compiled.conditions == ()
    assert compiled.observation_requests[0].kind == "continuity"


def test_explicit_continuity_limit_becomes_condition() -> None:
    compiled = AcceptanceCompiler().compile(
        observation_requests=(),
        condition_requests=(
            AcceptanceRequest(
                condition_id="continuity-limit",
                observation=_observation(),
                operator="less_equal",
                limit=1.0e-5,
                unit="1",
                scope=AcceptanceScope(time="latest"),
                source="user_text",
                confirmed=True,
                provenance=(
                    FactEvidence(
                        kind="user_quote",
                        detail="absolute cumulative continuity <= 1e-5",
                    ),
                ),
            ),
        ),
    )

    condition = compiled.conditions[0]
    assert condition.operator == "less_equal"
    assert condition.limit == pytest.approx(1.0e-5)
    assert compiled.observation_requests[0].observation_id == "continuity"


def test_unconfirmed_inferred_threshold_is_not_a_gate() -> None:
    request = AcceptanceRequest(
        condition_id="model-limit",
        observation=_observation(),
        operator="less_equal",
        limit=0.1,
        unit="1",
        scope=AcceptanceScope(time="latest"),
        source="model_inference",
        confirmed=False,
        provenance=(FactEvidence(kind="model_reason", detail="typical value"),),
    )

    compiled = AcceptanceCompiler().compile(
        observation_requests=(), condition_requests=(request,)
    )

    assert compiled.conditions == ()
    assert compiled.uncompiled[0].code == "ACCEPTANCE_CONFIRMATION_REQUIRED"


def test_equivalent_observations_with_distinct_provenance_are_merged() -> None:
    observation = _observation()
    acceptance_observation = observation.model_copy(
        update={
            "provenance": (
                FactEvidence(
                    kind="user_quote",
                    detail="continuity must remain finite",
                ),
            )
        }
    )
    request = AcceptanceRequest(
        condition_id="continuity-finite",
        observation=acceptance_observation,
        operator="finite",
        unit="1",
        scope=AcceptanceScope(time="latest"),
        source="user_text",
        confirmed=True,
        provenance=acceptance_observation.provenance,
    )

    compiled = AcceptanceCompiler().compile(
        observation_requests=(observation,),
        condition_requests=(request,),
    )

    assert len(compiled.conditions) == 1
    assert compiled.observation_requests[0].provenance == (
        *observation.provenance,
        *acceptance_observation.provenance,
    )


def test_history_observation_safely_covers_final_acceptance_observation() -> None:
    observation = _observation().model_copy(
        update={"time_selection": TimeSelection(kind="history")}
    )
    acceptance_observation = _observation().model_copy(
        update={"time_selection": TimeSelection(kind="final")}
    )
    request = AcceptanceRequest(
        condition_id="continuity-finite",
        observation=acceptance_observation,
        operator="finite",
        unit="1",
        scope=AcceptanceScope(time="final"),
        source="user_text",
        confirmed=True,
        provenance=acceptance_observation.provenance,
    )

    compiled = AcceptanceCompiler().compile(
        observation_requests=(observation,),
        condition_requests=(request,),
    )

    assert len(compiled.conditions) == 1
    assert compiled.observation_requests[0].time_selection.kind == "history"


def test_observations_with_same_id_and_distinct_scope_still_conflict() -> None:
    observation = _observation()
    conflicting = observation.model_copy(
        update={"scope": ObservationScope(kind="patch", names=("outlet",))}
    )
    request = AcceptanceRequest(
        condition_id="continuity-finite",
        observation=conflicting,
        operator="finite",
        unit="1",
        scope=AcceptanceScope(time="latest"),
        source="user_text",
        confirmed=True,
        provenance=conflicting.provenance,
    )

    with pytest.raises(
        ValueError,
        match="ACCEPTANCE_OBSERVATION_CONFLICT: continuity",
    ):
        AcceptanceCompiler().compile(
            observation_requests=(observation,),
            condition_requests=(request,),
        )


def test_all_scope_requires_a_history_observation() -> None:
    request = AcceptanceRequest(
        condition_id="all-time-limit",
        observation=_observation(),
        operator="less_equal",
        limit=0.1,
        unit="1",
        scope=AcceptanceScope(time="all"),
        source="user_text",
        confirmed=True,
        provenance=(FactEvidence(kind="user_quote", detail="all time <= 0.1"),),
    )
    compiled = AcceptanceCompiler().compile(
        observation_requests=(), condition_requests=(request,)
    )
    assert compiled.conditions == ()
    assert (
        compiled.uncompiled[0].code
        == "ACCEPTANCE_OBSERVATION_TIME_SCOPE_INSUFFICIENT"
    )


def test_closed_operators_reject_arbitrary_expression() -> None:
    payload = {
        "condition_id": "unsafe",
        "observation": _observation().model_dump(mode="json"),
        "operator": "eval_expression",
        "limit": 1,
        "unit": "1",
        "scope": {"time": "latest"},
        "source": "user_text",
        "confirmed": True,
        "provenance": [{"kind": "user_quote", "detail": "unsafe"}],
    }
    with pytest.raises(ValidationError):
        AcceptanceRequest.model_validate(payload)


def test_between_and_relative_error_require_complete_parameters() -> None:
    base = {
        "condition_id": "range",
        "observation": _observation().model_dump(mode="json"),
        "operator": "between",
        "unit": "1",
        "scope": {"time": "latest"},
        "source": "user_text",
        "confirmed": True,
        "provenance": [{"kind": "user_quote", "detail": "range"}],
    }
    with pytest.raises(ValidationError):
        AcceptanceRequest.model_validate(base)
