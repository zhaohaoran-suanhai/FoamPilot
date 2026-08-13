"""Authority-aware projections shared by validation and compilation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import FactSource, TaskDraft, TaskFact


_COMPILABLE_SOURCES = {
    FactSource.USER_TEXT,
    FactSource.USER_CONFIRMATION,
    FactSource.PUBLIC_ASSET,
}


def compilable_fact_map_from_facts(
    facts: Iterable[TaskFact],
) -> dict[str, TaskFact]:
    """Return facts backed by an authority outside the model."""

    return {
        item.path: item
        for item in facts
        if item.confirmed and item.source in _COMPILABLE_SOURCES
    }


def compilable_fact_map(draft: TaskDraft) -> dict[str, TaskFact]:
    """Return only compilable facts from a complete draft."""

    return compilable_fact_map_from_facts(draft.facts)


def effective_geometry_value(
    facts: Mapping[str, TaskFact],
) -> dict[str, object]:
    """Compose the atomic mesh fact with separately confirmed user metadata."""

    geometry_fact = facts.get("geometry")
    if geometry_fact is None or not isinstance(geometry_fact.value, dict):
        return {}
    geometry = dict(geometry_fact.value)
    dimensionality_fact = facts.get("geometry.dimensionality")
    if dimensionality_fact is not None:
        geometry["dimensionality"] = dimensionality_fact.value
    unit_fact = facts.get("geometry.length_unit")
    if unit_fact is not None:
        geometry["length_unit"] = unit_fact.value
    patch_roles = facts.get("geometry.patch_roles")
    if patch_roles is not None:
        geometry["patch_roles"] = patch_roles.value
    region_roles = facts.get("geometry.region_roles")
    if region_roles is not None:
        geometry["region_roles"] = region_roles.value
    return geometry


__all__ = [
    "compilable_fact_map",
    "compilable_fact_map_from_facts",
    "effective_geometry_value",
]
