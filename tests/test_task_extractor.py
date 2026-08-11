from __future__ import annotations

import pytest

from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.taskbuilder import extract_task_draft
from foampilot.taskbuilder.extraction import _ExtractedTaskDraft
from foampilot.tasks import PublicAsset
from foampilot.models.schema import strict_response_schema


class RecordingExtractionGateway:
    primary_backend_id = "recording"
    primary_model = "recording-extractor"
    policy_sha256 = "a" * 64

    def __init__(self, payload) -> None:
        self.payload = payload
        self.requests = []

    def generate_structured(self, request, schema, *, budget, trace):
        del trace
        assert budget.stage == ModelStage.TASK_EXTRACTION
        self.requests.append(request)
        value = schema.model_validate(self.payload)
        return ModelResult(
            value=value,
            logical_request_id="extract-1",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def _budget():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.TASK_EXTRACTION,
        request_timeout_seconds=60,
        stage_deadline_seconds=90,
        max_transport_attempts=2,
    )


def _payload(*, source="user_text", confirmed=True):
    return {
        "schema_version": 1,
        "facts": [
            {
                "path": "physics.regime",
                "value": '"steady"',
                "source": source,
                "evidence": "稳态层流",
                "impact": "high",
                "confirmed": confirmed,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def test_extraction_response_schema_encodes_arbitrary_fact_values_as_json_text() -> None:
    schema = strict_response_schema(_ExtractedTaskDraft.model_json_schema())

    fact_schema = schema["$defs"]["_ExtractedFact"]
    assert fact_schema["properties"]["value"] == {"type": "string"}

    def empty_schemas(value):
        if isinstance(value, dict):
            if not value:
                yield value
            for item in value.values():
                yield from empty_schemas(item)
        elif isinstance(value, list):
            for item in value:
                yield from empty_schemas(item)

    assert list(empty_schemas(schema)) == []


def test_extraction_transport_model_rejects_invalid_domain_path_early() -> None:
    payload = _payload()
    payload["facts"][0]["path"] = "initial_conditions.U"

    with pytest.raises(ValueError, match="string_pattern_mismatch"):
        _ExtractedTaskDraft.model_validate(payload)


def test_extraction_transport_model_rejects_duplicate_fact_paths_early() -> None:
    payload = _payload()
    payload["facts"].append(dict(payload["facts"][0]))

    with pytest.raises(ValueError, match="duplicate fact paths"):
        _ExtractedTaskDraft.model_validate(payload)


def test_extractor_uses_structured_stage_for_chinese_request() -> None:
    gateway = RecordingExtractionGateway(_payload())

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        protected_paths=("/private/target",),
    )

    assert draft.status == "confirmed"
    assert draft.facts[0].source == "user_text"
    assert gateway.requests[0].purpose == "extract-cfd-task-draft"
    assert "不得虚构" in gateway.requests[0].system_prompt
    assert "initial.conditions" in gateway.requests[0].system_prompt
    assert 'physics.regime 只能是 "steady" 或 "transient"' in (
        gateway.requests[0].system_prompt
    )
    assert "reference cell" in gateway.requests[0].system_prompt
    assert 'dimensionality="two_d"' in gateway.requests[0].system_prompt
    assert "target_cell_count" in gateway.requests[0].system_prompt
    assert '{"name":"top","role":"wall"}' in gateway.requests[0].system_prompt
    assert "patch name 不得使用中文" in gateway.requests[0].system_prompt
    assert "require_check_mesh_pass" in gateway.requests[0].system_prompt
    assert "layer_count=null" in gateway.requests[0].system_prompt
    assert "/private/target" not in gateway.requests[0].user_prompt


def test_extractor_sends_only_declared_asset_metadata() -> None:
    gateway = RecordingExtractionGateway(_payload())
    asset = PublicAsset(
        path="geometry/body.stl",
        sha256="b" * 64,
        purpose="public body surface",
    )

    draft = extract_task_draft(
        "Use the attached body surface.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.assets == [asset]
    prompt = gateway.requests[0].user_prompt
    assert "geometry/body.stl" in prompt
    assert "b" * 64 in prompt
    assert "/home/" not in prompt


def test_extractor_downgrades_invented_high_impact_property() -> None:
    payload = _payload(source="model_inference", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid.nu",
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

    assert draft.status == "ready_for_confirmation"
    assert draft.facts[0].confirmed is False
    assert draft.facts[0].source == "model_inference"


def test_extractor_downgrades_user_fact_without_verbatim_evidence() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid.nu",
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
    assert draft.status == "ready_for_confirmation"


def test_extractor_confirms_user_fact_from_verbatim_evidence() -> None:
    gateway = RecordingExtractionGateway(_payload(confirmed=False))

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "confirmed"
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

    assert draft.status == "ready_for_confirmation"
    assert draft.facts[0].source == "model_inference"
    assert draft.facts[0].confirmed is False


def test_extractor_rejects_protected_path_before_model_call() -> None:
    gateway = RecordingExtractionGateway(_payload())

    try:
        extract_task_draft(
            "Read /private/target and solve it.",
            [],
            gateway,
            budget=_budget(),
            trace=InMemoryModelTraceSink(),
            protected_paths=("/private/target",),
        )
    except ValueError as error:
        assert "protected path" in str(error)
    else:
        raise AssertionError("protected request must fail")
    assert gateway.requests == []


def test_extractor_rejects_protected_path_in_model_output() -> None:
    payload = _payload()
    payload["facts"][0]["evidence"] = "/private/target"
    gateway = RecordingExtractionGateway(payload)

    try:
        extract_task_draft(
            "Solve a public flow.",
            [],
            gateway,
            budget=_budget(),
            trace=InMemoryModelTraceSink(),
            protected_paths=("/private/target",),
        )
    except ValueError as error:
        assert "protected path" in str(error)
    else:
        raise AssertionError("protected output must fail")
