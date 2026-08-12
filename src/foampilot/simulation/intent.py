"""Intent-only model stage with deterministic source-authority reconciliation."""

from __future__ import annotations

import json
from typing import Iterable, Literal, Self

from pydantic import Field, field_validator, model_validator

from foampilot.assets import AssetBundle
from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.preprocessing import InputMeshFacts
from foampilot.tasks import TaskSpec

from .provenance import FactEvidence, ResolvedValue, StrictModel, Uncertainty


_FORBIDDEN_INFERRED_PREFIXES = (
    "command",
    "commands",
    "files",
    "numerics",
)
_EXPLICIT_DECISION_PREFIXES = (
    "solver.",
    "numerics.",
    "commands.",
    "files.",
)


class SimulationIntent(StrictModel):
    schema_version: Literal[1] = 1
    facts: tuple[ResolvedValue, ...] = ()
    constraints: tuple[str, ...] = ()
    requested_observables: tuple[str, ...] = ()
    acceptance_intent: tuple[str, ...] = ()
    uncertainties: tuple[Uncertainty, ...] = ()
    audit_warnings: tuple[str, ...] = ()

    @field_validator(
        "constraints",
        "requested_observables",
        "acceptance_intent",
        "audit_warnings",
    )
    @classmethod
    def normalize_text_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("intent text entries must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("intent text entries must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_unique_facts(self) -> Self:
        paths = [item.field_path for item in self.facts]
        if len(paths) != len(set(paths)):
            raise ValueError("simulation intent fact paths must be unique")
        question_ids = [item.question_id for item in self.uncertainties]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("simulation intent question IDs must be unique")
        return self

    def fact(self, field_path: str) -> ResolvedValue:
        for item in self.facts:
            if item.field_path == field_path:
                return item
        raise KeyError(field_path)


def _asset_summary(bundle: AssetBundle) -> dict[str, object]:
    return {
        "fact_id": f"asset:{bundle.manifest_sha256}",
        "kind": bundle.kind,
        "source_path": bundle.source_path,
        "install_path": bundle.install_path,
        "region": bundle.region,
        "manifest_sha256": bundle.manifest_sha256,
        "member_count": len(bundle.members),
        "logical_members": [item.logical_name for item in bundle.members],
    }


def _mesh_summary(facts: InputMeshFacts) -> dict[str, object]:
    return {
        "fact_id": f"mesh:{facts.bundle_manifest_sha256}",
        "region": facts.region,
        "declared_length_unit": facts.declared_length_unit,
        "counts": {
            "points": facts.points,
            "faces": facts.faces,
            "internal_faces": facts.internal_faces,
            "cells": facts.cells,
        },
        "bounding_box_m": facts.bounding_box_m.model_dump(mode="json"),
        "patches": [
            {
                "name": item.name,
                "patch_type": item.patch_type,
                "face_count": item.face_count,
            }
            for item in facts.patches
        ],
        "cell_zones": [
            {"name": item.name, "element_count": item.element_count}
            for item in facts.cell_zones
        ],
        "face_zones": [
            {"name": item.name, "element_count": item.element_count}
            for item in facts.face_zones
        ],
        "point_zones": [
            {"name": item.name, "element_count": item.element_count}
            for item in facts.point_zones
        ],
        "dimensionality_observations": facts.dimensionality_observations,
        "topology_observations": facts.topology_observations,
        "warnings": facts.warnings,
        "raw_content_included": False,
    }


def _detail_is_verified_user_text(
    evidence: Iterable[FactEvidence],
    request_text: str,
) -> bool:
    folded = request_text.casefold()
    return any(
        item.kind in {"user_quote", "explicit_task_fact"}
        and item.detail.strip().casefold() in folded
        for item in evidence
        if item.detail.strip()
    )


def _references_verified_fact(
    evidence: Iterable[FactEvidence],
    fact_ids: set[str],
) -> bool:
    return any(
        (item.reference is not None and item.reference in fact_ids)
        or item.detail in fact_ids
        for item in evidence
    )


def _downgrade(
    fact: ResolvedValue,
    *,
    reason: str,
) -> ResolvedValue:
    return fact.model_copy(
        update={
            "source": "model_inference",
            "confirmed": False,
            "evidence": (
                *fact.evidence,
                FactEvidence(kind="authority_audit", detail=reason),
            ),
        }
    )


def _reconcile_intent(
    response: SimulationIntent,
    *,
    task: TaskSpec,
    fact_ids: set[str],
) -> SimulationIntent:
    explicit = {item.field_path: item for item in task.explicit_facts}
    facts: dict[str, ResolvedValue] = {}
    warnings = list(response.audit_warnings)
    for fact in response.facts:
        if fact.field_path in explicit:
            continue
        candidate = fact
        if candidate.source in {"user_confirmation", "system_default"}:
            candidate = _downgrade(
                candidate,
                reason=(
                    f"model cannot assert {fact.source} authority for "
                    f"{fact.field_path}"
                ),
            )
            warnings.append(f"INTENT_AUTHORITY_DOWNGRADED:{fact.field_path}")
        elif candidate.source == "user_text" and not _detail_is_verified_user_text(
            candidate.evidence,
            task.request_text,
        ):
            candidate = _downgrade(
                candidate,
                reason="claimed user text evidence was not found verbatim",
            )
            warnings.append(f"INTENT_USER_TEXT_UNVERIFIED:{fact.field_path}")
        elif candidate.source == "public_asset_fact" and not _references_verified_fact(
            candidate.evidence,
            fact_ids,
        ):
            candidate = _downgrade(
                candidate,
                reason="claimed public fact reference was not supplied",
            )
            warnings.append(f"INTENT_PUBLIC_FACT_UNVERIFIED:{fact.field_path}")
        elif candidate.source == "deterministic_rule":
            candidate = _downgrade(
                candidate,
                reason="model response cannot originate deterministic authority",
            )
            warnings.append(f"INTENT_RULE_AUTHORITY_DOWNGRADED:{fact.field_path}")

        prefix = candidate.field_path.split(".", 1)[0]
        explicitly_stated = _detail_is_verified_user_text(
            candidate.evidence,
            task.request_text,
        )
        if (
            prefix in _FORBIDDEN_INFERRED_PREFIXES
            or candidate.field_path.startswith(_EXPLICIT_DECISION_PREFIXES)
        ) and not explicitly_stated:
            warnings.append(f"INTENT_FORBIDDEN_DECISION_REMOVED:{fact.field_path}")
            continue
        facts[candidate.field_path] = candidate

    for fact in task.explicit_facts:
        if fact.field_path.startswith("acceptance.legacy_checks."):
            continue
        facts[fact.field_path] = fact

    ordered = tuple(facts[path] for path in sorted(facts))
    return response.model_copy(
        update={
            "facts": ordered,
            "acceptance_intent": tuple(task.acceptance_intent),
            "audit_warnings": tuple(dict.fromkeys(warnings)),
        }
    )


def interpret_intent(
    task: TaskSpec,
    *,
    asset_facts: tuple[AssetBundle, ...],
    mesh_facts: tuple[InputMeshFacts, ...],
    capability_kinds: tuple[str, ...],
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> SimulationIntent:
    """Interpret the user request without choosing files, commands or numerics."""

    assets = tuple(_asset_summary(item) for item in asset_facts)
    meshes = tuple(_mesh_summary(item) for item in mesh_facts)
    request = ModelRequest(
        purpose="interpret-simulation-intent",
        system_prompt=(
            "You interpret simulation intent only. Do not write OpenFOAM files, "
            "choose numerical schemes, create commands, or assign confidence. "
            "Report ambiguity as structured uncertainties. A model response may "
            "not self-assert user_confirmation, public_asset_fact, "
            "system_default, or deterministic_rule authority."
        ),
        user_prompt=json.dumps(
            {
                "request_text": task.request_text,
                "explicit_facts": [
                    item.model_dump(mode="json")
                    for item in task.explicit_facts
                    if not item.field_path.startswith(
                        "acceptance.legacy_checks."
                    )
                ],
                "required_outputs": task.required_outputs,
                "acceptance_intent": task.acceptance_intent,
                "AssetFacts": assets,
                "InputMeshFacts": meshes,
                "available_capability_kinds": capability_kinds,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    response = gateway.generate_structured(
        request,
        SimulationIntent,
        budget=budget,
        trace=trace,
    ).value
    fact_ids = {
        str(item["fact_id"])
        for item in (*assets, *meshes)
    }
    return _reconcile_intent(response, task=task, fact_ids=fact_ids)


__all__ = ["SimulationIntent", "interpret_intent"]
