from __future__ import annotations

from foampilot.models import InMemoryModelTraceSink
from foampilot.taskbuilder import extract_task_draft
from foampilot.tasks import PublicAsset
from tests.support.taskbuilder import (
    RecordingExtractionGateway,
    extraction_payload as _payload,
    file_ingress_context as _file_ingress_context,
    task_extraction_budget as _budget,
)


def test_extractor_discards_design_owned_model_questions() -> None:
    payload = _payload()
    payload["unresolved_questions"] = [
        {
            "question_id": "q_solver",
            "path": "physics.solver",
            "kind": "blocking",
            "prompt_zh": "请选择 solver。",
            "reason_zh": "模型错误地提前追问工程设计。",
        }
    ]
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert [item.path for item in draft.unresolved_questions] == ["geometry"]


def test_public_file_route_rebuilds_model_question_identifiers() -> None:
    payload = {
        "schema_version": 1,
        "facts": [],
        "assumptions": [],
        "unresolved_questions": [
            {
                "question_id": "q_geometry_dimensionality",
                "path": "geometry.length_unit",
                "kind": "blocking",
                "prompt_zh": "单位是什么？",
                "reason_zh": "模型未找到单位。",
            }
        ],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.obj", sha256="b" * 64, purpose="body"
    )

    draft = extract_task_draft(
        "Use body.obj as geometry.",
        [surface],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface),
    )

    assert [(item.question_id, item.path) for item in draft.unresolved_questions] == [
        ("q_geometry_length_unit", "geometry.length_unit"),
        ("q_geometry_dimensionality", "geometry.dimensionality"),
    ]


def test_resolved_question_cannot_forge_conflict_with_model_identifier() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"surface","dimensionality":"three_d",'
                    '"description":"body","length_unit":"m","assets":[]}'
                ),
                "source": "user_text",
                "evidence": "three_d geometry in m",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [
            {
                "question_id": "q_fake_conflict",
                "path": "geometry.length_unit",
                "kind": "blocking",
                "prompt_zh": "单位是什么？",
                "reason_zh": "模型伪造冲突标识。",
            }
        ],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.stl", sha256="b" * 64, purpose="body"
    )

    draft = extract_task_draft(
        "Use body.stl as three_d geometry in m.",
        [surface],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface),
    )

    assert draft.status == "confirmed"
    assert draft.unresolved_questions == []
