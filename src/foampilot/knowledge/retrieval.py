"""Deterministic, leakage-aware selection over focused knowledge entries."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .models import KnowledgeEntry


Family = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"),
]
KnowledgeType = Literal[
    "solver_guide",
    "mesh_pattern",
    "boundary_condition",
    "physics_model",
    "numerics",
    "error_playbook",
    "parallel_execution",
    "validation_pattern",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeQuery(StrictModel):
    text: Annotated[str, StringConstraints(min_length=1)]
    fork: Literal["foundation"] = "foundation"
    version: Literal["10"] = "10"
    solver: str | None = None
    knowledge_types: tuple[KnowledgeType, ...] = ()
    evaluation_family: Family | None = None
    formal: bool = True
    allowed_development_families: tuple[Family, ...] = ()
    limit: int = Field(default=5, ge=1, le=20)


class KnowledgeMatch(StrictModel):
    entry_id: str
    title: str
    knowledge_type: KnowledgeType
    score: int
    visibility: Literal["public", "development_only"]
    summary: str
    source_sha256: str


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(token) > 1
    )


def _eligible(entry: KnowledgeEntry, query: KnowledgeQuery) -> bool:
    if entry.fork != query.fork or entry.version != query.version:
        return False
    if query.knowledge_types and entry.knowledge_type not in query.knowledge_types:
        return False
    if (
        query.solver is not None
        and entry.solvers
        and query.solver not in entry.solvers
    ):
        return False
    family = query.evaluation_family
    if family is not None and family in entry.leakage.families:
        return False
    if entry.leakage.visibility == "development_only":
        return (
            family is not None
            and family in query.allowed_development_families
        )
    return True


def _score(entry: KnowledgeEntry, query_tokens: tuple[str, ...]) -> int:
    query = set(query_tokens)
    solver_tokens = set(_tokens(" ".join(entry.solvers)))
    high = set(
        _tokens(
            " ".join(
                (
                    entry.id,
                    entry.title,
                    " ".join(entry.tags),
                    " ".join(entry.solvers),
                    " ".join(entry.models),
                )
            )
        )
    )
    # Detailed guidance is delivered after selection; its verbosity must not
    # change an entry's relevance.
    body = set(_tokens(entry.content.summary))
    exact_solver_bonus = 20 if query & solver_tokens else 0
    return exact_solver_bonus + 4 * len(query & high) + len(query & body)


def select_knowledge(
    entries: tuple[KnowledgeEntry, ...] | list[KnowledgeEntry],
    query: KnowledgeQuery,
) -> tuple[KnowledgeMatch, ...]:
    """Filter sensitive entries before deterministic relevance scoring."""

    query_tokens = _tokens(query.text)
    candidates: list[tuple[int, KnowledgeEntry]] = []
    for entry in entries:
        if not _eligible(entry, query):
            continue
        score = _score(entry, query_tokens)
        if score > 0:
            candidates.append((score, entry))
    candidates.sort(key=lambda item: (-item[0], item[1].id))
    return tuple(
        KnowledgeMatch(
            entry_id=entry.id,
            title=entry.title,
            knowledge_type=entry.knowledge_type,
            score=score,
            visibility=entry.leakage.visibility,
            summary=entry.content.summary,
            source_sha256=entry.source.sha256,
        )
        for score, entry in candidates[: query.limit]
    )
