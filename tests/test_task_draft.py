from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.taskbuilder import (
    DraftIssue,
    DraftReview,
    TaskDraft,
    TaskFact,
    taskbuilder_message_zh,
)


def _fact(
    path: str,
    value,
    *,
    source: str = "user_text",
    impact: str = "high",
    confirmed: bool = True,
) -> TaskFact:
    return TaskFact(
        path=path,
        value=value,
        source=source,
        evidence="用户明确说明该条件",
        impact=impact,
        confirmed=confirmed,
    )


def _draft(**overrides) -> TaskDraft:
    payload = {
        "draft_id": "draft-laminar-channel",
        "request_text": "求解二维不可压缩层流通道，长度 1 m。",
        "facts": [
            _fact("physics.regime", "steady"),
            _fact("physics.compressibility", "incompressible"),
            _fact("physics.phase_family", "single_phase"),
            _fact("geometry.length_unit", "m"),
        ],
        "assumptions": [],
        "unresolved_questions": [],
        "assets": [],
        "protected_paths": ["/private/taskbuilder-target"],
        "status": "confirmed",
    }
    payload.update(overrides)
    return TaskDraft.model_validate(payload)


def test_confirmed_task_draft_has_unique_provenance_bearing_facts() -> None:
    draft = _draft()

    assert draft.status == "confirmed"
    assert all(item.evidence for item in draft.facts)
    assert all(item.confirmed for item in draft.facts)


def test_duplicate_fact_paths_are_rejected() -> None:
    fact = _fact("physics.regime", "steady")

    with pytest.raises(ValidationError, match="duplicate fact paths"):
        _draft(facts=[fact, fact])


def test_high_impact_model_inference_cannot_be_confirmed() -> None:
    with pytest.raises(ValidationError, match="model inference"):
        _fact(
            "materials.fluid.nu",
            {"value": 1e-6, "unit": "m2/s"},
            source="model_inference",
            confirmed=True,
        )


def test_blocking_question_requires_incomplete_status() -> None:
    with pytest.raises(ValidationError, match="blocking questions"):
        _draft(
            unresolved_questions=[
                {
                    "question_id": "geometry-unit",
                    "path": "geometry.length_unit",
                    "kind": "blocking",
                    "prompt_zh": "几何长度单位是什么？",
                    "reason_zh": "单位会改变物理尺度。",
                }
            ]
        )


def test_draft_review_requires_consistent_can_compile() -> None:
    issue = DraftIssue(
        code="TASK_UNIT_AMBIGUOUS",
        severity="blocking",
        field_path="geometry.length_unit",
        message_zh="几何长度单位缺失。",
        recovery_zh="请明确提供长度单位。",
    )

    with pytest.raises(ValidationError, match="can_compile"):
        DraftReview(draft=_draft(status="incomplete"), issues=[issue], can_compile=True)


def test_stable_taskbuilder_errors_have_chinese_message_and_recovery() -> None:
    payload = taskbuilder_message_zh("TASK_UNIT_AMBIGUOUS")

    assert payload.code == "TASK_UNIT_AMBIGUOUS"
    assert "单位" in payload.message
    assert "确认" in payload.recovery
