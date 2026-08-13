from __future__ import annotations

import json

import pytest

from foampilot.models import InMemoryModelTraceSink
from foampilot.taskbuilder import extract_task_draft
from foampilot.tasks import PublicAsset
from tests.support.taskbuilder import (
    RecordingExtractionGateway,
    extraction_payload as _payload,
    task_extraction_budget as _budget,
)


def test_extractor_collapses_equivalent_duplicate_fact_paths() -> None:
    payload = _payload()
    payload["facts"].append(dict(payload["facts"][0]))
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert [item.path for item in draft.facts].count("physics.regime") == 1
    assert draft.fact_map()["physics.regime"].confirmed is True


def test_extractor_downgrades_conflicting_duplicate_fact_paths() -> None:
    payload = _payload()
    conflict = dict(payload["facts"][0])
    conflict["value"] = '"transient"'
    payload["facts"].append(conflict)
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["physics.regime"]
    assert fact.source == "model_inference"
    assert fact.confirmed is False
    assert "conflicting duplicate" in fact.evidence


def test_extractor_collapses_semantically_equivalent_json_fact_values() -> None:
    payload = _payload()
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"rho":1,"unit":"kg/m3"}',
        evidence="rho 1 kg/m3",
    )
    duplicate = dict(payload["facts"][0])
    duplicate["value"] = '{"unit":"kg/m3", "rho": 1}'
    payload["facts"].append(duplicate)
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use rho 1 kg/m3.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["materials.fluid"]
    assert fact.source == "user_text"
    assert fact.confirmed is True


def test_extractor_downgrades_invented_high_impact_property() -> None:
    payload = _payload(source="model_inference", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"value": 1e-6, "unit": "m2/s"}',
        evidence="typical water value",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve a flow, material not specified.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "incomplete"
    assert draft.facts[0].confirmed is False
    assert draft.facts[0].source == "model_inference"


def test_extractor_downgrades_user_fact_without_verbatim_evidence() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"value": 1e-6, "unit": "m2/s"}',
        evidence="typical water viscosity",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve a flow without a specified fluid property.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.facts[0].source == "model_inference"
    assert draft.facts[0].confirmed is False
    assert draft.status == "incomplete"


def test_extractor_confirms_user_fact_from_verbatim_evidence() -> None:
    gateway = RecordingExtractionGateway(_payload(confirmed=False))

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "incomplete"
    assert draft.facts[0].source == "user_text"
    assert draft.facts[0].confirmed is True


def test_extractor_does_not_confirm_value_unrelated_to_verbatim_evidence() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="physics.solver",
        value='"madeUpFoam"',
        evidence="flow",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve this flow on the supplied geometry.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    solver = draft.fact_map()["physics.solver"]
    assert solver.source == "user_text"
    assert solver.confirmed is False


def test_extractor_does_not_match_compressible_inside_incompressible() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="physics.compressibility",
        value='"compressible"',
        evidence="incompressible flow",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve an incompressible flow.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["physics.compressibility"]
    assert fact.confirmed is False


@pytest.mark.parametrize(
    ("path", "value", "evidence"),
    [
        ("physics.compressibility", "compressible", "不可压缩流动"),
        ("physics.regime", "steady", "非稳态启动流动"),
        ("physics.regime", "steady", "不是稳态流动"),
        ("physics.regime", "steady", "not steady flow"),
        ("boundaries", [{"role": "wall"}], "not a wall"),
    ],
)
def test_extractor_does_not_confirm_negated_chinese_alias(
    path: str,
    value: str,
    evidence: str,
) -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path=path,
        value=json.dumps(value),
        evidence=evidence,
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        f"求解{evidence}。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()[path].confirmed is False


def test_extractor_matches_equivalent_scientific_notation() -> None:
    payload = _payload(source="user_text", confirmed=False)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"nu":{"value":0.000001,"unit":"m2/s"}}',
        evidence="nu = 1e-6 m2/s",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use a fluid with nu = 1e-6 m2/s.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()["materials.fluid"].confirmed is True


def test_extractor_binds_nested_values_to_semantic_field_names() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"nu":{"value":0.000001,"unit":"m2/s"}}',
        evidence="alpha = 1e-6 m2/s",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use alpha = 1e-6 m2/s.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()["materials.fluid"].confirmed is False


def test_extractor_can_verify_explicit_boolean_user_fact() -> None:
    payload = _payload(source="user_text", confirmed=False)
    payload["facts"][0].update(
        path="boundaries",
        value='{"allow_reverse_flow":false}',
        evidence="allow_reverse_flow = false",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use the supplied geometry; allow_reverse_flow = false.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["boundaries"]
    assert fact.source == "user_text"
    assert fact.confirmed is True


def test_extractor_recursively_binds_nested_user_fact_values() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"nu":{"value":0.005,"unit":"m2/s"}}',
        evidence="nu is 0.005 m2/s",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use a fluid whose nu is 0.005 m2/s.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()["materials.fluid"].confirmed is True


def test_extractor_never_grants_public_asset_authority_to_model_fact() -> None:
    payload = _payload(source="public_asset", confirmed=True)
    payload["facts"][0].update(
        path="physics.solver",
        value='"madeUpFoam"',
        evidence="geometry/body.stl",
    )
    gateway = RecordingExtractionGateway(payload)
    asset = PublicAsset(
        path="geometry/body.stl",
        sha256="b" * 64,
        purpose="public body surface",
    )

    draft = extract_task_draft(
        "Solve this flow using the declared surface.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    solver = draft.fact_map()["physics.solver"]
    assert solver.source == "model_inference"
    assert solver.confirmed is False


def test_extractor_accepts_balanced_chinese_quote_around_user_evidence() -> None:
    payload = _payload(confirmed=False)
    payload["facts"][0]["evidence"] = "“稳态层流”"
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.facts[0].source == "user_text"
    assert draft.facts[0].confirmed is True


def test_extractor_does_not_allow_model_to_claim_user_confirmation() -> None:
    gateway = RecordingExtractionGateway(
        _payload(source="user_confirmation", confirmed=True)
    )

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "incomplete"
    assert draft.facts[0].source == "model_inference"
    assert draft.facts[0].confirmed is False
