from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from foampilot.plans import normalize_execution_plan_input

from .test_execution_plan import valid_plan


def test_input_normalizer_repairs_step_labels_and_exact_field_duplicates():
    payload = valid_plan().model_dump(mode="json")
    payload["commands"][0]["step_id"] = "Mesh Step"
    payload["commands"][1]["step_id"] = "mesh.step"
    payload["manifest"]["fields"].append(
        dict(payload["manifest"]["fields"][0])
    )

    plan, records = normalize_execution_plan_input(json.dumps(payload))

    assert [command.step_id for command in plan.commands[:2]] == [
        "mesh-step",
        "mesh-step-2",
    ]
    assert len(plan.manifest.fields) == 1
    assert [record.code for record in records] == [
        "STEP_ID_CANONICALIZED",
        "STEP_ID_CANONICALIZED",
        "EXACT_DUPLICATE_MANIFEST_FIELD_REMOVED",
    ]
    assert records[0].location == "commands.0.step_id"
    assert records[0].original == "Mesh Step"
    assert records[0].normalized == "mesh-step"
    assert records[2].location == "manifest.fields.1"


def test_input_normalizer_leaves_semantic_field_conflicts_invalid():
    payload = valid_plan().model_dump(mode="json")
    conflicting = dict(payload["manifest"]["fields"][0])
    conflicting["role"] = "momentum"
    payload["manifest"]["fields"].append(conflicting)

    with pytest.raises(
        ValidationError,
        match="manifest field identities must be unique",
    ):
        normalize_execution_plan_input(json.dumps(payload))


def test_input_normalizer_rejects_non_object_json():
    with pytest.raises(ValidationError):
        normalize_execution_plan_input("[]")
