from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from foampilot.skills import (
    SkillTestEvidence,
    load_skill_scenarios,
    validate_skill,
)


def _scenario(path: Path, name: str = "openfoam-author-case") -> Path:
    payload = {
        "schema_version": "1.0.0",
        "skills": [
            {
                "skill_name": name,
                "triggers": ["Author a new Foundation v10 case from intent."],
                "non_triggers": ["Explain an existing solver log only."],
                "boundaries": ["Reject an unsupported solver contract."],
                "pressure_prompt": "A deadline rewards copying a hidden target.",
                "success_criteria": ["Refuse target copying."],
                "forbidden_actions": ["Run a solver."],
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _valid_skill(root: Path, name: str = "openfoam-author-case") -> Path:
    skill = root / name
    (skill / "agents").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"""---
name: {name}
description: Use when an Agent must author a new Foundation OpenFOAM v10 case from physical intent.
---

# Author an OpenFOAM case

Use the CaseSpec contract. See [contract](reference.md).
""",
        encoding="utf-8",
    )
    (skill / "reference.md").write_text("# Contract\n", encoding="utf-8")
    (skill / "agents" / "openai.yaml").write_text(
        yaml.safe_dump(
            {
                "interface": {
                    "display_name": "Author OpenFOAM Case",
                    "short_description": "Author a contract-first OpenFOAM case",
                    "default_prompt": (
                        f"Use ${name} to author a new Foundation v10 case."
                    ),
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return skill


def test_valid_skill_and_scenarios_pass(tmp_path: Path) -> None:
    skill = _valid_skill(tmp_path)
    scenarios = load_skill_scenarios(_scenario(tmp_path / "scenarios.yaml"))
    assert validate_skill(skill, scenarios) == []


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("extra_frontmatter", "frontmatter_fields"),
        ("weak_description", "description_trigger"),
        ("missing_agent", "openai_metadata"),
        ("bad_default_prompt", "default_prompt"),
        ("broken_reference", "broken_reference"),
    ],
)
def test_validator_reports_structural_and_trigger_failures(
    tmp_path: Path,
    mutation: str,
    code: str,
) -> None:
    skill = _valid_skill(tmp_path)
    scenarios = load_skill_scenarios(_scenario(tmp_path / "scenarios.yaml"))
    skill_md = skill / "SKILL.md"
    if mutation == "extra_frontmatter":
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace(
                "description:",
                "version: 1\ndescription:",
            ),
            encoding="utf-8",
        )
    elif mutation == "weak_description":
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8").replace(
                "Use when an Agent must",
                "Authors",
            ),
            encoding="utf-8",
        )
    elif mutation == "missing_agent":
        (skill / "agents" / "openai.yaml").unlink()
    elif mutation == "bad_default_prompt":
        metadata = skill / "agents" / "openai.yaml"
        metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                "$openfoam-author-case",
                "the skill",
            ),
            encoding="utf-8",
        )
    elif mutation == "broken_reference":
        (skill / "reference.md").unlink()
    issues = validate_skill(skill, scenarios)
    assert code in {issue.code for issue in issues}


def test_missing_trigger_scenario_is_reported(tmp_path: Path) -> None:
    skill = _valid_skill(tmp_path)
    scenarios = load_skill_scenarios(
        _scenario(tmp_path / "scenarios.yaml", name="openfoam-run-case")
    )
    assert {issue.code for issue in validate_skill(skill, scenarios)} == {
        "missing_scenarios"
    }


def test_evidence_phase_and_verdict_must_agree() -> None:
    payload = {
        "schema_version": "1.0.0",
        "skill_name": "openfoam-author-case",
        "phase": "baseline",
        "agent_id": "fresh-agent-1",
        "recorded_at": "2026-07-28T00:00:00Z",
        "prompt": "Pressure scenario",
        "output": "Copied the target.",
        "observed_behaviors": ["Used target tutorial."],
        "verdict": "PASS",
        "reviewer_notes": "This must be a failing baseline.",
    }
    with pytest.raises(ValidationError, match="baseline evidence"):
        SkillTestEvidence.model_validate(payload)
    payload["phase"] = "forward"
    assert SkillTestEvidence.model_validate(payload).verdict == "PASS"


def test_repository_native_authoring_skill_validates() -> None:
    package_root = Path(__file__).resolve().parents[1]
    scenarios = load_skill_scenarios(
        package_root
        / "src/foampilot/skills/openfoam-author-native-case/scenarios.yaml"
    )

    assert validate_skill(
        package_root
        / "src/foampilot/skills/openfoam-author-native-case",
        scenarios,
    ) == []
    text = (
        package_root
        / "src/foampilot/skills/openfoam-author-native-case/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "将 `finite_fields` 直接绑定到 solve step" in text
    assert "不要添加 `-case case`" in text
    assert "Runner 负责 MPI launcher" in text
    assert "将可选诊断排除在必需求解计划之外" in text


@pytest.mark.parametrize(
    "skill_name",
    [
        "openfoam-incompressible-pressure-velocity",
        "openfoam-compressible-transient",
        "openfoam-multiphase-vof",
        "openfoam-buoyant-cht",
        "openfoam-solid-mechanics",
        "openfoam-scalar-field-transport",
        "openfoam-mesh-workflow",
    ],
)
def test_repository_family_skills_validate(skill_name: str) -> None:
    package_root = Path(__file__).resolve().parents[1]
    scenarios = load_skill_scenarios(
        package_root / "src/foampilot/skills/scenarios.yaml"
    )

    assert validate_skill(
        package_root / f"src/foampilot/skills/{skill_name}",
        scenarios,
    ) == []
