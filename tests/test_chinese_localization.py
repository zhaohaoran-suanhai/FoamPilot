from __future__ import annotations

import re
from pathlib import Path

import yaml

from foampilot.knowledge import load_knowledge_corpus


PROJECT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ASCII_TOKEN = re.compile(r"^[\x00-\x7f]+$")


def _has_chinese(text: str) -> bool:
    return CJK.search(text) is not None


def test_knowledge_uses_chinese_prose_and_english_retrieval_metadata() -> None:
    entries = load_knowledge_corpus(
        PROJECT / "src/foampilot/knowledge/openfoam10"
    )
    assert len(entries) >= 30

    for entry in entries:
        assert _has_chinese(entry.title), entry.id
        assert _has_chinese(entry.content.summary), entry.id
        for value in (
            *entry.applicability.conditions,
            *entry.applicability.not_applicable,
            *entry.content.rules,
            *entry.content.validation,
        ):
            assert _has_chinese(value), f"{entry.id}: {value}"

        for value in (
            entry.id,
            *entry.tags,
            *entry.activation_terms,
            *entry.solvers,
            *entry.models,
        ):
            assert ASCII_TOKEN.fullmatch(value), f"{entry.id}: {value}"


def test_skills_use_chinese_body_and_keep_discovery_contracts_english() -> None:
    root = PROJECT / "src/foampilot/skills"
    skill_dirs = sorted(path for path in root.iterdir() if (path / "SKILL.md").is_file())
    assert skill_dirs

    for skill_dir in skill_dirs:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        frontmatter, body = text.split("---", 2)[1:]
        metadata = yaml.safe_load(frontmatter)
        assert metadata["name"] == skill_dir.name
        assert metadata["description"].startswith("Use when ")
        assert ASCII_TOKEN.fullmatch(metadata["description"])
        assert _has_chinese(body), skill_dir.name

        interface = yaml.safe_load(
            (skill_dir / "agents/openai.yaml").read_text(encoding="utf-8")
        )["interface"]
        assert _has_chinese(interface["display_name"])
        assert _has_chinese(interface["short_description"])
        assert _has_chinese(interface["default_prompt"])
        assert f"${skill_dir.name}" in interface["default_prompt"]


def test_scenario_prose_is_chinese_but_skill_names_stay_ascii() -> None:
    paths = [
        PROJECT / "src/foampilot/skills/scenarios.yaml",
        PROJECT / "src/foampilot/skills/openfoam-author-native-case/scenarios.yaml",
    ]
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        for scenario in payload["skills"]:
            assert ASCII_TOKEN.fullmatch(scenario["skill_name"])
            prose = [
                *scenario["triggers"],
                *scenario["non_triggers"],
                *scenario["boundaries"],
                scenario["pressure_prompt"],
                *scenario["success_criteria"],
                *scenario["forbidden_actions"],
            ]
            assert all(_has_chinese(value) for value in prose)


def test_current_introduction_documents_are_primarily_chinese() -> None:
    paths = [
        PROJECT / "README.md",
        PROJECT / "docs/architecture.md",
        PROJECT / "docs/agent-integration.md",
        PROJECT / "docs/knowledge-governance.md",
        PROJECT / "docs/solver-family-self-checks.md",
        PROJECT / "docs/system-overview.md",
        PROJECT / "docs/independent-agent-quickstart.md",
        PROJECT / "docs/qualification.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert len(CJK.findall(text)) >= 30, path.name


def test_primary_agent_instruction_prompt_is_chinese() -> None:
    prompt = (PROJECT / "src/foampilot/authoring/case_author.py").read_text(
        encoding="utf-8"
    )
    for contract in ("CaseBundle", "CaseManifest", "execution steps", "argv"):
        assert contract in prompt
