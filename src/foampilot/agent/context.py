"""Dynamically retrieve bounded public context for a native task."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from foampilot.knowledge import (
    KnowledgeQuery,
    load_knowledge_corpus,
    select_knowledge,
)
from foampilot.tasks import TaskSpec


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_text: str
    skills_text: str
    selected_knowledge_ids: tuple[str, ...]
    selected_source_hashes: dict[str, str]


def _default_package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_agent_context(
    task: TaskSpec,
    *,
    package_root: str | Path | None = None,
    limit: int = 8,
) -> AgentContext:
    """Retrieve public formal-safe knowledge without solver preselection."""

    root = (
        Path(package_root).resolve()
        if package_root is not None
        else _default_package_root()
    )
    knowledge_root = root / "knowledge/openfoam10"
    skill_path = root / "skills/openfoam-author-native-case/SKILL.md"
    if not knowledge_root.is_dir():
        raise FileNotFoundError(f"knowledge root is missing: {knowledge_root}")
    if not skill_path.is_file():
        raise FileNotFoundError(f"native Agent Skill is missing: {skill_path}")

    corpus = load_knowledge_corpus(knowledge_root)
    by_id = {entry.id: entry for entry in corpus}
    matches = select_knowledge(
        corpus,
        KnowledgeQuery(
            text=" ".join((task.title, task.prompt, *task.required_outputs)),
            formal=True,
            limit=limit,
        ),
    )
    selected = [by_id[match.entry_id] for match in matches]
    knowledge_text = json.dumps(
        [
            {
                "id": entry.id,
                "title": entry.title,
                "applicability": entry.applicability.model_dump(mode="json"),
                "content": entry.content.model_dump(mode="json"),
                "source_sha256": entry.source.sha256,
            }
            for entry in selected
        ],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    skills_text = skill_path.read_text(encoding="utf-8")
    combined = knowledge_text + skills_text
    for protected in task.protected_paths:
        if protected in combined:
            raise ValueError("Agent context contains a protected path")
    return AgentContext(
        knowledge_text=knowledge_text,
        skills_text=skills_text,
        selected_knowledge_ids=tuple(entry.id for entry in selected),
        selected_source_hashes={
            entry.id: entry.source.sha256 for entry in selected
        },
    )
