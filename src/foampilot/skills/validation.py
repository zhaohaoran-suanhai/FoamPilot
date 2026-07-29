"""Independent structural validation for portable Agent Skills."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import (
    SkillScenarioSuite,
    SkillTestEvidence,
    SkillValidationIssue,
)


_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def load_skill_scenarios(path: str | Path) -> SkillScenarioSuite:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("skill scenario root must be a mapping")
    return SkillScenarioSuite.model_validate(payload)


def validate_skill_evidence(path: str | Path) -> SkillTestEvidence:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("skill evidence root must be a mapping")
    return SkillTestEvidence.model_validate(payload)


def _issue(code: str, path: Path, message: str) -> SkillValidationIssue:
    return SkillValidationIssue(code=code, path=str(path), message=message)


def _frontmatter(skill_md: Path) -> tuple[dict[str, object] | None, str]:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None, text
    payload = yaml.safe_load(parts[1])
    return (payload if isinstance(payload, dict) else None), parts[2]


def validate_skill(
    skill_dir: str | Path,
    scenarios: SkillScenarioSuite,
) -> list[SkillValidationIssue]:
    directory = Path(skill_dir)
    issues: list[SkillValidationIssue] = []
    name = directory.name
    if not _NAME.fullmatch(name) or len(name) > 64:
        issues.append(_issue("skill_name", directory, "invalid skill directory name"))

    skill_md = directory / "SKILL.md"
    if not skill_md.is_file():
        return [_issue("skill_md", skill_md, "SKILL.md is missing")]
    metadata, body = _frontmatter(skill_md)
    if metadata is None:
        issues.append(
            _issue("frontmatter", skill_md, "valid YAML frontmatter is required")
        )
        metadata = {}
    if set(metadata) != {"name", "description"}:
        issues.append(
            _issue(
                "frontmatter_fields",
                skill_md,
                "frontmatter must contain only name and description",
            )
        )
    if metadata.get("name") != name:
        issues.append(
            _issue("frontmatter_name", skill_md, "frontmatter name must match folder")
        )
    description = metadata.get("description")
    if (
        not isinstance(description, str)
        or not description.startswith("Use when ")
        or len(description) > 500
    ):
        issues.append(
            _issue(
                "description_trigger",
                skill_md,
                "description must start with 'Use when ' and stay under 500 chars",
            )
        )
    if not body.strip():
        issues.append(_issue("skill_body", skill_md, "Skill body is empty"))

    for target in _MARKDOWN_LINK.findall(body):
        if target.startswith(("http://", "https://", "#", "/")):
            continue
        relative = target.split("#", 1)[0]
        if relative and not (directory / relative).is_file():
            issues.append(
                _issue(
                    "broken_reference",
                    skill_md,
                    f"missing relative reference: {relative}",
                )
            )

    openai_yaml = directory / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        issues.append(
            _issue("openai_metadata", openai_yaml, "agents/openai.yaml is missing")
        )
    else:
        payload = yaml.safe_load(openai_yaml.read_text(encoding="utf-8"))
        interface = payload.get("interface") if isinstance(payload, dict) else None
        if not isinstance(interface, dict):
            issues.append(
                _issue("openai_metadata", openai_yaml, "interface mapping is required")
            )
        else:
            for field in ("display_name", "short_description", "default_prompt"):
                if not isinstance(interface.get(field), str) or not interface[field]:
                    issues.append(
                        _issue(
                            "openai_metadata",
                            openai_yaml,
                            f"interface.{field} is required",
                        )
                    )
            short = interface.get("short_description", "")
            if isinstance(short, str) and not 25 <= len(short) <= 64:
                issues.append(
                    _issue(
                        "openai_metadata",
                        openai_yaml,
                        "short_description must contain 25-64 characters",
                    )
                )
            prompt = interface.get("default_prompt", "")
            if isinstance(prompt, str) and f"${name}" not in prompt:
                issues.append(
                    _issue(
                        "default_prompt",
                        openai_yaml,
                        f"default_prompt must mention ${name}",
                    )
                )

    scenario = next(
        (
            candidate
            for candidate in scenarios.skills
            if candidate.skill_name == name
        ),
        None,
    )
    if scenario is None:
        issues.append(
            _issue(
                "missing_scenarios",
                directory,
                "trigger, non-trigger, boundary, and pressure scenarios are missing",
            )
        )
    return issues
