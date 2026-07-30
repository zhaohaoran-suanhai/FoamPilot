from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from foampilot.improvement import (
    LearningCandidate,
    load_learning_candidate,
    write_learning_candidate,
)


def _source_run() -> dict[str, object]:
    return {
        "path": "/tmp/foampilot-runs/multiphase-dam-break",
        "manifest_sha256": "a" * 64,
    }


def _evidence() -> dict[str, object]:
    return {
        "failure_fingerprints": ["FOAM FATAL IO ERROR"],
        "failed_steps": ["solve"],
        "observations": ["The generated time-step contract was incomplete."],
    }


def _candidate_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "candidate_id": "of10-interfoam-time-cap",
        "source_runs": [_source_run()],
        "root_cause": "numerics",
        "secondary_root_causes": [],
        "public_evidence": _evidence(),
        "official_example": {
            "used": True,
            "source_sha256": "b" * 64,
            "extracted_principles": [
                "Transient VOF time steps must obey a Courant cap."
            ],
        },
        "generalized_lesson": (
            "Cap transient VOF time steps by the applicable Courant numbers."
        ),
        "proposed_target": "knowledge",
        "leakage_families": ["multiphase/interFoam"],
        "development_cases": ["multiphase-dam-break"],
        "regression_cases": ["laminar-cavity"],
        "holdout_cases": ["multiphase-capillary-rise"],
        "promotion_criteria": ["source_improves", "holdout_non_decreasing"],
        "max_total_model_calls_delta": 0,
        "max_total_duration_ratio": 1.25,
    }


def test_official_example_requires_hash_principles_and_leakage() -> None:
    payload = _candidate_payload()
    payload["official_example"] = {
        "used": True,
        "source_sha256": None,
        "extracted_principles": [],
    }
    payload["leakage_families"] = []

    with pytest.raises(ValidationError, match="official example use"):
        LearningCandidate.model_validate(payload)


def test_unused_official_example_cannot_carry_derived_evidence() -> None:
    payload = _candidate_payload()
    payload["official_example"] = {
        "used": False,
        "source_sha256": "b" * 64,
        "extracted_principles": ["A hidden answer-derived principle."],
    }

    with pytest.raises(ValidationError, match="unused official example"):
        LearningCandidate.model_validate(payload)


def test_case_roles_must_be_disjoint() -> None:
    payload = _candidate_payload()
    payload["holdout_cases"] = ["multiphase-dam-break"]

    with pytest.raises(ValidationError, match="case roles"):
        LearningCandidate.model_validate(payload)


def test_environment_candidate_can_only_target_runner() -> None:
    payload = _candidate_payload()
    payload["root_cause"] = "environment"
    payload["proposed_target"] = "knowledge"

    with pytest.raises(ValidationError, match="runner"):
        LearningCandidate.model_validate(payload)


def test_candidate_rejects_unknown_fields() -> None:
    payload = _candidate_payload()
    payload["unreviewed_patch"] = "apply automatically"

    with pytest.raises(ValidationError, match="Extra inputs"):
        LearningCandidate.model_validate(payload)


def test_learning_candidate_yaml_round_trip_is_exclusive(
    tmp_path: Path,
) -> None:
    candidate = LearningCandidate.model_validate(_candidate_payload())
    destination = tmp_path / "improvements" / "candidate.yaml"

    written = write_learning_candidate(destination, candidate)

    assert written == destination
    assert load_learning_candidate(destination) == candidate
    with pytest.raises(FileExistsError):
        write_learning_candidate(destination, candidate)


def test_candidate_yaml_root_must_be_a_mapping(tmp_path: Path) -> None:
    source = tmp_path / "candidate.yaml"
    source.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="root must be a mapping"):
        load_learning_candidate(source)
