"""Bounded public context assembled after capability routing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_text: str
    skills_text: str
    knowledge_slots: dict[str, str | None]
    missing_slots: tuple[str, ...]
    selected_knowledge_ids: tuple[str, ...]
    selected_source_hashes: dict[str, str]
    skill_names: tuple[str, ...]
