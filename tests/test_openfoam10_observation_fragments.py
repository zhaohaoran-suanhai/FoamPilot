from __future__ import annotations

import pytest

from foampilot.observations import (
    EvidenceStrategy,
    ObservationItem,
    ObservationPlan,
    ObservationScope,
    TimeSelection,
    compile_foundation10_observations,
)
from foampilot.simulation import FactEvidence


def _item(kind: str, strategy: str, *, names=("outlet",), time="history"):
    return ObservationItem(
        observation_id=f"{kind}-item",
        kind=kind,
        quantity=kind,
        dimension="1",
        scope=ObservationScope(kind="patch", names=names),
        time_selection=TimeSelection(kind=time),
        evidence_strategy=EvidenceStrategy(
            kind=strategy,
            collector_id=(f"foundation10.{kind}" if strategy in {"runtime_configuration", "postprocess_command"} else None),
        ),
        provenance=(FactEvidence(kind="user_quote", detail=kind),),
    )


def test_final_field_metric_does_not_inject_runtime_fragment() -> None:
    fragments = compile_foundation10_observations(
        ObservationPlan(items=(_item("flow_rate", "postprocess_command", time="final"),))
    )

    assert fragments.system_files == ()
    assert fragments.commands[0].executable == "postProcess"
    assert fragments.commands[0].args == ["-func", "surfaceFieldValue"]


def test_flow_history_fragment_is_system_owned_and_allowlisted() -> None:
    fragments = compile_foundation10_observations(
        ObservationPlan(items=(_item("flow_rate", "runtime_configuration"),))
    )

    assert fragments.system_owned_paths == ("system/foampilot-observations",)
    text = fragments.system_files[0].content
    assert "surfaceFieldValue" in text
    assert "outlet" in text
    for forbidden in ("#codeStream", "#calc", "systemCall", "executeCalls"):
        assert forbidden not in text


def test_unsupported_collector_or_unsafe_scope_is_rejected() -> None:
    item = _item("flow_rate", "runtime_configuration").model_copy(
        update={
            "evidence_strategy": EvidenceStrategy(
                kind="runtime_configuration",
                collector_id="model.arbitrary",
            )
        }
    )
    with pytest.raises(ValueError, match="OBSERVATION_COLLECTOR_UNSUPPORTED"):
        compile_foundation10_observations(ObservationPlan(items=(item,)))

