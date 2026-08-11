from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from foampilot.desktop.workspace import (
    DesktopWorkspace,
    DesktopWorkspaceError,
    confirm_task_draft,
)
from foampilot.taskbuilder import TaskDraft, validate_task_draft


def test_workspace_versions_inputs_and_creates_unique_job_roots(
    tmp_path: Path,
) -> None:
    workspace = DesktopWorkspace.open(tmp_path / "project")

    assert workspace.save_request("求解方腔流").name == "request-001.md"
    assert workspace.save_request("再次求解").name == "request-002.md"
    assert workspace.save_draft("schema_version: 1\n").name == (
        "task-draft-001.yaml"
    )
    assert workspace.save_task("schema_version: 2\n").name == "task-001.yaml"
    first_job = workspace.create_job_root()
    second_job = workspace.create_job_root()
    assert first_job.parent == workspace.runs_dir
    assert second_job.parent == workspace.runs_dir
    assert first_job != second_job
    assert first_job.is_dir()
    assert second_job.is_dir()


def test_workspace_rejects_blank_request_and_symlink_root(
    tmp_path: Path,
) -> None:
    workspace = DesktopWorkspace.open(tmp_path / "project")
    with pytest.raises(DesktopWorkspaceError, match="request is blank"):
        workspace.save_request("  \n")

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(DesktopWorkspaceError, match="symbolic link"):
        DesktopWorkspace.open(linked)


def _draft_payload() -> dict[str, object]:
    def fact(
        path: str,
        value: object,
        *,
        source: str = "user_text",
        confirmed: bool = True,
        impact: str = "high",
    ) -> dict[str, object]:
        return {
            "path": path,
            "value": value,
            "source": source,
            "evidence": f"evidence for {path}",
            "impact": impact,
            "confirmed": confirmed,
        }

    return {
        "schema_version": 1,
        "draft_id": "desktop-draft",
        "request_text": "求解一个不可压缩层流方腔。",
        "facts": [
            fact(
                "geometry",
                {
                    "mode": "parametric",
                    "dimensionality": "two_d",
                    "description": "1 m square cavity",
                    "length_unit": "m",
                    "parameters": {
                        "width": {"value": 1.0, "unit": "m"},
                        "height": {"value": 1.0, "unit": "m"},
                    },
                },
            ),
            fact("physics.family", "fluid"),
            fact(
                "physics.compressibility",
                "incompressible",
                source="model_inference",
                confirmed=False,
                impact="medium",
            ),
            fact("physics.phase_family", "single_phase"),
            fact("materials.fluid", {"nu": 1e-5}),
            fact("boundaries", [{"role": "wall", "value": "noSlip"}]),
        ],
        "assumptions": [],
        "unresolved_questions": [
            {
                "question_id": "regime",
                "path": "physics.regime",
                "kind": "confirmable",
                "prompt_zh": "确认采用稳态还是瞬态？",
                "reason_zh": "影响求解流程。",
                "candidate": "steady",
                "evidence": "用户描述未明确。",
            }
        ],
        "assets": [],
        "protected_paths": [],
        "status": "ready_for_confirmation",
    }


def test_confirm_task_draft_applies_answers_and_confirms_inference() -> None:
    text = yaml.safe_dump(
        _draft_payload(),
        sort_keys=False,
        allow_unicode=True,
    )

    confirmed_text = confirm_task_draft(text, {"regime": "steady"})
    confirmed = TaskDraft.model_validate(yaml.safe_load(confirmed_text))

    assert confirmed.status == "confirmed"
    assert confirmed.unresolved_questions == []
    assert confirmed.fact_map()["physics.regime"].value == "steady"
    assert confirmed.fact_map()["physics.regime"].source == (
        "user_confirmation"
    )
    compressibility = confirmed.fact_map()["physics.compressibility"]
    assert compressibility.confirmed is True
    assert compressibility.source == "user_confirmation"
    assert validate_task_draft(confirmed).can_compile is True


def test_confirm_task_draft_requires_answer_without_candidate() -> None:
    payload = _draft_payload()
    question = payload["unresolved_questions"][0]
    assert isinstance(question, dict)
    question["candidate"] = None
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    with pytest.raises(DesktopWorkspaceError, match="answer is required"):
        confirm_task_draft(text, {})
