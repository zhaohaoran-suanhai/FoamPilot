"""Assemble one bounded entry per public knowledge slot."""

from __future__ import annotations

import json
from pathlib import Path
import re

from foampilot.knowledge import (
    KnowledgeEntry,
    KnowledgeQuery,
    load_knowledge_corpus,
    select_knowledge,
)
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec
from foampilot.preprocessing import GeometryFacts

from .models import AgentContext
from .skill_registry import read_skills, select_skill_names
from .slots import BASE_SLOTS, ERROR_SLOT, PARALLEL_SLOT, PRUNE_ORDER, ContextSlot


_REPAIR_EVIDENCE_LIMIT_BYTES = 4096
_DESIGN_CONTEXT_LIMIT_BYTES = 48 * 1024


def public_design_context(
    context: AgentContext,
    *,
    payload_limit_bytes: int = _DESIGN_CONTEXT_LIMIT_BYTES,
) -> dict[str, object]:
    """Return only bounded public knowledge and Skill context for design."""

    if payload_limit_bytes < 1:
        raise ValueError("design context payload limit must be positive")
    payload: dict[str, object] = {
        "knowledge_text": context.knowledge_text,
        "skills_text": context.skills_text,
        "selected_knowledge_ids": context.selected_knowledge_ids,
        "selected_source_hashes": context.selected_source_hashes,
        "skill_names": context.skill_names,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > payload_limit_bytes:
        raise ValueError("design context exceeds the context payload budget")
    return payload


def _default_package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _query_text(
    task: TaskSpec,
    capability: CapabilityProfile,
    slot: ContextSlot,
    repair_evidence: str = "",
    geometry_facts: GeometryFacts | None = None,
) -> str:
    parts = (
        task.title,
        task.prompt,
        *task.required_outputs,
        *task.acceptance_requirements,
        capability.physics_family,
        capability.solver_family or "",
        capability.solver_executable or "",
        capability.mesh_family,
        slot.query_terms,
    )
    if slot.name == ERROR_SLOT.name and repair_evidence:
        parts = (*parts, repair_evidence)
    if slot.name == "mesh_pattern" and geometry_facts is not None:
        parts = (
            *parts,
            json.dumps(
                geometry_facts.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    return " ".join(parts)


def _bounded_repair_evidence(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    encoded = normalized.encode("utf-8")
    if len(encoded) <= _REPAIR_EVIDENCE_LIMIT_BYTES:
        return normalized
    return encoded[-_REPAIR_EVIDENCE_LIMIT_BYTES:].decode(
        "utf-8",
        errors="ignore",
    )


def _public_task_text(task: TaskSpec) -> str:
    return " ".join(
        (
            task.title,
            task.prompt,
            *task.required_outputs,
            *task.acceptance_requirements,
        )
    )


def _normalized_tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def _is_activated(entry: KnowledgeEntry, task_text: str) -> bool:
    """Require explicit task evidence for opt-in cross-solver guidance."""

    if not entry.activation_terms:
        return True
    task_tokens = _normalized_tokens(task_text)
    for term in entry.activation_terms:
        term_tokens = _normalized_tokens(term)
        width = len(term_tokens)
        if width and any(
            task_tokens[index : index + width] == term_tokens
            for index in range(len(task_tokens) - width + 1)
        ):
            return True
    return False


def _entry_payload(
    *,
    slot: str,
    score: int,
    entry: KnowledgeEntry,
) -> dict[str, object]:
    return {
        "slot": slot,
        "score": score,
        "entry": {
            "id": entry.id,
            "title": entry.title,
            "knowledge_type": entry.knowledge_type,
            "solvers": entry.solvers,
            "models": entry.models,
            "tags": entry.tags,
            "activation_terms": entry.activation_terms,
            "applicability": entry.applicability.model_dump(mode="json"),
            "content": entry.content.model_dump(mode="json"),
            "source": entry.source.model_dump(mode="json"),
        },
    }


def _render(entries: list[dict[str, object]]) -> str:
    return json.dumps(
        entries,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def assemble_agent_context(
    task: TaskSpec,
    capability: CapabilityProfile,
    *,
    package_root: str | Path | None = None,
    repair: bool = False,
    repair_evidence: str = "",
    geometry_facts: GeometryFacts | None = None,
    payload_limit_bytes: int = 32 * 1024,
) -> AgentContext:
    """Select context by semantic slot without irrelevant top-N fill."""

    if payload_limit_bytes < 1:
        raise ValueError("payload_limit_bytes must be positive")
    root = (
        Path(package_root).resolve()
        if package_root is not None
        else _default_package_root()
    )
    knowledge_root = root / "knowledge/openfoam10"
    skills_root = root / "skills"
    if not knowledge_root.is_dir():
        raise FileNotFoundError(f"knowledge root is missing: {knowledge_root}")

    corpus = load_knowledge_corpus(knowledge_root)
    by_id = {entry.id: entry for entry in corpus}
    slots = list(BASE_SLOTS)
    if capability.parallel_expected:
        slots.append(PARALLEL_SLOT)
    if repair:
        slots.append(ERROR_SLOT)

    knowledge_slots: dict[str, str | None] = {}
    payloads: list[dict[str, object]] = []
    public_task_text = _public_task_text(task)
    bounded_repair_evidence = _bounded_repair_evidence(repair_evidence)
    for slot in slots:
        matches = select_knowledge(
            corpus,
            KnowledgeQuery(
                text=_query_text(
                    task,
                    capability,
                    slot,
                    bounded_repair_evidence,
                    geometry_facts,
                ),
                solver=(
                    capability.solver_executable
                    if slot.solver_filtered
                    else None
                ),
                knowledge_types=slot.knowledge_types,
                formal=True,
                limit=20,
            ),
        )
        match = next(
            (
                candidate
                for candidate in matches
                if _is_activated(by_id[candidate.entry_id], public_task_text)
            ),
            None,
        )
        if match is None:
            knowledge_slots[slot.name] = None
            continue
        entry = by_id[match.entry_id]
        knowledge_slots[slot.name] = entry.id
        payloads.append(
            _entry_payload(
                slot=slot.name,
                score=match.score,
                entry=entry,
            )
        )

    skill_names = select_skill_names(capability, task=task)
    skills_text = read_skills(skills_root, skill_names)
    if len(skills_text.encode("utf-8")) > payload_limit_bytes:
        raise ValueError("selected Skills exceed the context payload budget")

    def total_bytes() -> int:
        return len(_render(payloads).encode("utf-8")) + len(
            skills_text.encode("utf-8")
        )

    for slot_name in PRUNE_ORDER:
        if total_bytes() <= payload_limit_bytes:
            break
        payloads = [
            item for item in payloads if item["slot"] != slot_name
        ]
        if knowledge_slots.get(slot_name) is not None:
            knowledge_slots[slot_name] = None
    if total_bytes() > payload_limit_bytes:
        raise ValueError(
            "required solver context exceeds the context payload budget"
        )

    knowledge_text = _render(payloads)
    selected_ids = tuple(
        str(item["entry"]["id"])
        for item in payloads
        if isinstance(item.get("entry"), dict)
    )
    selected_hashes = {
        entry_id: by_id[entry_id].source.sha256
        for entry_id in selected_ids
    }
    combined = knowledge_text + skills_text
    for protected in task.protected_paths:
        if protected in combined:
            raise ValueError("Agent context contains a protected path")
    return AgentContext(
        knowledge_text=knowledge_text,
        skills_text=skills_text,
        knowledge_slots=knowledge_slots,
        missing_slots=tuple(
            slot for slot, entry_id in knowledge_slots.items()
            if entry_id is None
        ),
        selected_knowledge_ids=selected_ids,
        selected_source_hashes=selected_hashes,
        skill_names=skill_names,
    )
