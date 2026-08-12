from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from foampilot.simulation import (
    ConfirmationRecord,
    DesignCandidate,
    FactEvidence,
    ResolvedValue,
    Uncertainty,
    canonical_sha256,
    write_json_exclusive,
    write_yaml_exclusive,
)


def _evidence(detail: str = "candidate") -> tuple[FactEvidence, ...]:
    return (FactEvidence(kind="model_reason", detail=detail),)


def test_model_inference_cannot_be_marked_confirmed() -> None:
    with pytest.raises(
        ValidationError,
        match="model inference cannot self-confirm",
    ):
        ResolvedValue[str](
            field_path="solver.family",
            value="pisoFoam",
            source="model_inference",
            impact="high",
            evidence=_evidence(),
            confirmed=True,
        )


def test_confirmation_binds_one_question_field_and_value() -> None:
    record = ConfirmationRecord(
        confirmation_id="confirm-1",
        question_id="question-1",
        field_path="materials.fluid.nu",
        candidate_id="water-like-nu",
        confirmed_value={"value": 1e-6, "unit": "m2/s"},
        source="user_confirmation",
        answered_at="2026-08-12T12:00:00Z",
    )

    assert record.field_path == "materials.fluid.nu"
    assert record.answered_at == datetime(
        2026,
        8,
        12,
        12,
        tzinfo=timezone.utc,
    )


def test_system_default_is_low_impact_only() -> None:
    with pytest.raises(ValidationError, match="system defaults are low impact"):
        ResolvedValue[float](
            field_path="materials.fluid.nu",
            value=1e-6,
            source="system_default",
            impact="high",
            evidence=(
                FactEvidence(kind="default_policy", detail="water-like default"),
            ),
            confirmed=True,
        )


def test_fact_evidence_is_unique_and_field_paths_are_legal() -> None:
    evidence = FactEvidence(kind="user_quote", detail="laminar flow")
    with pytest.raises(ValidationError, match="duplicate evidence"):
        ResolvedValue[str](
            field_path="physics.regime",
            value="laminar",
            source="user_text",
            impact="high",
            evidence=(evidence, evidence),
            confirmed=True,
        )

    with pytest.raises(ValidationError, match="field path"):
        ResolvedValue[str](
            field_path="../physics.regime",
            value="laminar",
            source="user_text",
            impact="high",
            evidence=(evidence,),
            confirmed=True,
        )


def test_values_are_json_only_and_contracts_are_frozen() -> None:
    with pytest.raises(ValidationError):
        ResolvedValue[object](
            field_path="physics.regime",
            value=Path("not-json"),
            source="model_inference",
            impact="low",
            evidence=_evidence(),
            confirmed=False,
        )

    value = ResolvedValue[str](
        field_path="physics.regime",
        value="laminar",
        source="user_text",
        impact="high",
        evidence=(FactEvidence(kind="user_quote", detail="laminar"),),
        confirmed=True,
    )
    with pytest.raises(ValidationError, match="frozen"):
        value.value = "turbulent"


def test_uncertainty_candidate_shape_is_fail_closed() -> None:
    candidate = DesignCandidate(
        candidate_id="nu-water",
        value={"value": 1e-6, "unit": "m2/s"},
        rationale="Common explicit candidate for confirmation.",
        evidence=_evidence("candidate derived from request context"),
    )
    confirmable = Uncertainty(
        question_id="q-nu",
        field_path="materials.fluid.nu",
        impact="high",
        kind="confirmable",
        prompt_zh="是否采用该运动黏度？",
        reason_zh="用户尚未给出数值。",
        candidates=(candidate,),
    )
    assert confirmable.candidates == (candidate,)

    with pytest.raises(ValidationError, match="confirmable.*candidate"):
        Uncertainty(
            question_id="q-empty",
            field_path="materials.fluid.nu",
            impact="high",
            kind="confirmable",
            prompt_zh="请选择。",
            reason_zh="需要确认。",
        )
    with pytest.raises(ValidationError, match="information-required.*candidate"):
        Uncertainty(
            question_id="q-info",
            field_path="geometry.length_unit",
            impact="high",
            kind="information_required",
            prompt_zh="请提供长度单位。",
            reason_zh="无法安全推断。",
            candidates=(candidate,),
        )
    with pytest.raises(ValidationError, match="conflict.*two candidates"):
        Uncertainty(
            question_id="q-conflict",
            field_path="physics.regime",
            impact="high",
            kind="conflict",
            prompt_zh="存在冲突。",
            reason_zh="两处输入不一致。",
            candidates=(candidate,),
        )


def test_canonical_hash_is_stable_and_writers_are_exclusive(
    tmp_path: Path,
) -> None:
    first = ResolvedValue[dict](
        field_path="materials.fluid.nu",
        value={"unit": "m2/s", "value": 1e-6},
        source="user_confirmation",
        impact="high",
        evidence=(FactEvidence(kind="confirmation", detail="answer q-nu"),),
        confirmed=True,
    )
    second = ResolvedValue[dict](
        field_path="materials.fluid.nu",
        value={"value": 1e-6, "unit": "m2/s"},
        source="user_confirmation",
        impact="high",
        evidence=(FactEvidence(kind="confirmation", detail="answer q-nu"),),
        confirmed=True,
    )
    assert canonical_sha256(first) == canonical_sha256(second)

    json_path = tmp_path / "fact.json"
    yaml_path = tmp_path / "fact.yaml"
    write_json_exclusive(json_path, first)
    write_yaml_exclusive(yaml_path, first)
    assert json.loads(json_path.read_text(encoding="utf-8"))["value"]["unit"] == "m2/s"
    assert "field_path: materials.fluid.nu" in yaml_path.read_text(
        encoding="utf-8"
    )
    with pytest.raises(FileExistsError):
        write_json_exclusive(json_path, first)
    with pytest.raises(FileExistsError):
        write_yaml_exclusive(yaml_path, first)
