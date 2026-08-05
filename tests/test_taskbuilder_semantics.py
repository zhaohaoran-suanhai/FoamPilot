from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from foampilot.taskbuilder import TaskDraft, compile_task_draft, validate_task_draft
from tests.test_task_draft_validation import _complete_draft, _fact


FIXTURE = (
    Path(__file__).parent
    / "fixtures/taskbuilder/semantic-cases.yaml"
)


def _cases():
    payload = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return payload["cases"]


@pytest.mark.parametrize("scenario", _cases(), ids=lambda item: item["case_id"])
def test_semantic_request_fixture_never_invents_blocking_facts(scenario) -> None:
    payload = _complete_draft().model_dump(mode="json")
    payload["draft_id"] = "draft-" + scenario["case_id"]
    payload["request_text"] = scenario["request_text"]
    facts = {item["path"]: item for item in payload["facts"]}
    for path in scenario.get("remove_facts", []):
        facts.pop(path, None)
    for path, value in scenario.get("set_facts", {}).items():
        if path in facts:
            facts[path]["value"] = value
        else:
            facts[path] = _fact(path, value)
    for path, value in scenario.get("add_facts", {}).items():
        facts[path] = _fact(path, value)
    payload["facts"] = list(facts.values())
    payload["assets"] = scenario.get("assets", [])
    draft = TaskDraft.model_validate(payload)

    review = validate_task_draft(draft)

    assert review.can_compile is scenario["expected_can_compile"]
    codes = {item.code for item in review.issues}
    assert set(scenario.get("expected_codes", [])) <= codes
    paths = {item.field_path for item in review.issues}
    assert set(scenario.get("expected_paths", [])) <= paths
    if review.can_compile:
        compilation = compile_task_draft(review)
        assert compilation.task.prompt.startswith(scenario["request_text"])
    else:
        with pytest.raises(ValueError, match="TASK_COMPILATION_FAILED"):
            compile_task_draft(review)
