"""Explicit test-only helpers for constructing canonical TaskSpec v3 data."""

from __future__ import annotations

from copy import deepcopy


def resolved_fact(
    field_path: str,
    value: object,
    *,
    source: str = "user_text",
    impact: str = "high",
    confirmed: bool = True,
) -> dict[str, object]:
    return {
        "field_path": field_path,
        "value": deepcopy(value),
        "source": source,
        "impact": impact,
        "evidence": [
            {
                "kind": "test_fixture",
                "detail": f"Explicit test fixture for {field_path}.",
            }
        ],
        "confirmed": confirmed,
    }


def canonical_task_payload(payload: dict[str, object]) -> dict[str, object]:
    """Upgrade a local v2-shaped test dictionary without touching loaders."""

    migrated = deepcopy(payload)
    if migrated.get("schema_version") == 3:
        return migrated
    if migrated.get("schema_version") != 2:
        return migrated
    migrated["schema_version"] = 3
    migrated["request_text"] = migrated.pop("prompt")
    migrated["acceptance_intent"] = migrated.pop(
        "acceptance_requirements"
    )
    facts = list(migrated.pop("explicit_facts", []))
    geometry = migrated.pop("geometry", None)
    if geometry is not None:
        facts.append(resolved_fact("geometry.input", geometry))
    mesh = migrated.pop("mesh", None)
    if mesh is not None:
        facts.append(resolved_fact("mesh.intent", mesh))
    for check in migrated.pop("public_checks", []):
        facts.append(
            resolved_fact(
                f"acceptance.legacy_checks.{check['name']}",
                check,
                source="deterministic_rule",
            )
        )
    migrated["explicit_facts"] = facts
    migrated.setdefault(
        "repair_policy",
        {
            "automatic_numerical_repair": True,
            "model_diagnostic": True,
        },
    )
    return migrated


def replace_explicit_fact(
    payload: dict[str, object],
    field_path: str,
    value: object,
) -> None:
    facts = payload.setdefault("explicit_facts", [])
    if not isinstance(facts, list):
        raise TypeError("explicit_facts must be a list")
    facts[:] = [
        item
        for item in facts
        if not isinstance(item, dict) or item.get("field_path") != field_path
    ]
    facts.append(resolved_fact(field_path, value))

