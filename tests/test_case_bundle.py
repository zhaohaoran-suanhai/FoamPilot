from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from foampilot.authoring import CaseBundle, load_case_bundle_output
from tests.test_execution_plan import valid_plan


def _bundle_payload() -> dict[str, object]:
    plan = valid_plan()
    return {
        "schema_version": 1,
        "manifest": plan.manifest.model_dump(mode="json"),
        "files": [item.model_dump(mode="json") for item in plan.files],
    }


def test_case_bundle_rejects_commands() -> None:
    with pytest.raises(ValidationError, match="commands"):
        CaseBundle.model_validate({**_bundle_payload(), "commands": []})


@pytest.mark.parametrize("schema_version", [3, 4])
def test_authoring_loader_rejects_execution_plan_responses(
    schema_version: int,
) -> None:
    plan = valid_plan().model_dump(mode="json")
    plan["schema_version"] = schema_version

    with pytest.raises(ValidationError, match="schema_version|commands"):
        load_case_bundle_output(json.dumps(plan))


def test_case_bundle_accepts_manifest_and_all_related_files() -> None:
    bundle = load_case_bundle_output(json.dumps(_bundle_payload()))

    assert bundle.schema_version == 1
    assert bundle.manifest.solver_executable == "icoFoam"
    assert [item.path for item in bundle.files] == [
        "system/controlDict",
        "0/U",
    ]
