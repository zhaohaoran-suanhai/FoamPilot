from __future__ import annotations

import pytest

from foampilot.models import InMemoryModelTraceSink
from foampilot.taskbuilder import extract_task_draft
from foampilot.tasks import PublicAsset
from tests.support.taskbuilder import (
    RecordingExtractionGateway,
    extraction_payload as _payload,
    poly_mesh_topology_payload,
    provided_mesh_ingress_context,
    task_extraction_budget as _budget,
)


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

    assert draft.status == "incomplete"
    assert [item.path for item in draft.unresolved_questions] == ["geometry"]
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
    assert "必须完整写入 geometry 的 patch_roles/region_roles" in (
        gateway.requests[0].system_prompt
    )
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


def test_extractor_receives_compact_topology_context() -> None:
    gateway = RecordingExtractionGateway(_payload())
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "frontAndBack",
                    "patch_type": "empty",
                    "start_face": 7,
                    "face_count": 4,
                }
            ],
            cell_zones=[{"name": "zoneA", "element_count": 1}],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    extract_task_draft(
        "Use the supplied mesh for a steady flow.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    prompt = gateway.requests[0].user_prompt
    assert "PolyMeshTopologyFacts" in prompt
    assert "frontAndBack" in prompt
    assert "zoneA" in prompt
    assert "unscaled_bounds" in prompt
    assert "FoamFile" not in prompt


def test_ingress_context_has_deterministic_serialized_size_limit() -> None:
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "p" + "x" * 270_000,
                    "patch_type": "patch",
                    "start_face": 1,
                    "face_count": 1,
                }
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    with pytest.raises(ValueError, match="TASK_INGRESS_CONTEXT_TOO_LARGE"):
        context.agent_payload()


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
