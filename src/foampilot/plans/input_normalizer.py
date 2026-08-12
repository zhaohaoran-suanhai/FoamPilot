"""Temporary Phase 2 plan-response normalization bridge.

Canonical Phase 3 authoring parses :class:`CaseBundle` and never calls this
module. It remains private to the old bridge until that caller is removed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from foampilot.models.traces import StructuredOutputNormalization

from .models import ExecutionPlan


_INVALID_STEP_CHARACTERS = re.compile(r"[^a-z0-9_-]+")


def _canonical_step_id(value: str, index: int) -> str:
    normalized = _INVALID_STEP_CHARACTERS.sub(
        "-",
        value.lower(),
    ).strip("-_")
    return normalized or f"step-{index + 1}"


def _unique_step_id(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    ordinal = 2
    while f"{base}-{ordinal}" in used:
        ordinal += 1
    return f"{base}-{ordinal}"


def _normalize_step_ids(
    payload: dict[str, Any],
) -> list[StructuredOutputNormalization]:
    commands = payload.get("commands")
    if not isinstance(commands, list):
        return []
    records: list[StructuredOutputNormalization] = []
    used: set[str] = set()
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            continue
        original = command.get("step_id")
        if not isinstance(original, str):
            continue
        canonical = _canonical_step_id(original, index)
        normalized = _unique_step_id(canonical, used)
        used.add(normalized)
        if normalized == original:
            continue
        command["step_id"] = normalized
        records.append(
            StructuredOutputNormalization(
                code="STEP_ID_CANONICALIZED",
                location=f"commands.{index}.step_id",
                original=original,
                normalized=normalized,
            )
        )
    return records


def _field_identity(field: dict[str, Any]) -> str:
    return ":".join(
        str(field.get(key, "")) for key in ("region", "name", "path")
    )


def _remove_exact_duplicate_fields(
    payload: dict[str, Any],
) -> list[StructuredOutputNormalization]:
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        return []
    fields = manifest.get("fields")
    if not isinstance(fields, list):
        return []
    records: list[StructuredOutputNormalization] = []
    kept: list[Any] = []
    exact_seen: set[str] = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            kept.append(field)
            continue
        fingerprint = json.dumps(
            field,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if fingerprint not in exact_seen:
            exact_seen.add(fingerprint)
            kept.append(field)
            continue
        identity = _field_identity(field)
        records.append(
            StructuredOutputNormalization(
                code="EXACT_DUPLICATE_MANIFEST_FIELD_REMOVED",
                location=f"manifest.fields.{index}",
                original=identity,
                normalized="removed",
            )
        )
    manifest["fields"] = kept
    return records


def normalize_execution_plan_input(
    output_text: str,
) -> tuple[ExecutionPlan, tuple[StructuredOutputNormalization, ...]]:
    """规范化无语义歧义的标签/精确重复项，再做 canonical 校验。"""

    payload = json.loads(output_text)
    if not isinstance(payload, dict):
        return ExecutionPlan.model_validate(payload), ()
    records = [
        *_normalize_step_ids(payload),
        *_remove_exact_duplicate_fields(payload),
    ]
    return ExecutionPlan.model_validate(payload), tuple(records)
