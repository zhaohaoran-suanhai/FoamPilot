"""Intent-only model stage with deterministic source-authority reconciliation."""

from __future__ import annotations

import json
import math
import re
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
from foampilot.observations.models import ObservationRequest
from foampilot.acceptance.models import AcceptanceRequest
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
    observation_requests: tuple[ObservationRequest, ...] = ()
    acceptance_requests: tuple[AcceptanceRequest, ...] = ()
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
            raise ValueError("duplicate simulation intent fact paths are not allowed")
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


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")


_OPERATOR_MARKERS = {
    "less_equal": (
        "<=",
        "≤",
        "less than or equal",
        "no more than",
        "not exceed",
        "at most",
        "不大于",
        "小于等于",
        "不超过",
        "至多",
    ),
    "greater_equal": (
        ">=",
        "≥",
        "greater than or equal",
        "no less than",
        "at least",
        "不小于",
        "大于等于",
        "至少",
    ),
    "between": ("between", "within the range", "介于", "范围内"),
    "relative_error": ("relative error", "相对误差"),
    "absolute_balance": (
        "absolute balance",
        "absolute imbalance",
        "绝对平衡",
        "绝对不平衡",
    ),
    "finite": ("finite", "no nan", "without nan", "有限", "无 nan", "没有 nan"),
    "exists": ("exists", "present", "available", "produced", "存在", "可用", "生成"),
}

_OBSERVATION_MARKERS = {
    "residual": ("residual", "残差"),
    "continuity": ("continuity", "mass balance", "连续性", "质量守恒"),
    "flow_rate": ("flow rate", "flowrate", "flux", "流量"),
    "pressure_difference": (
        "pressure difference",
        "pressure drop",
        "delta p",
        "压差",
    ),
    "region_average": ("average", "mean", "平均"),
    "force": ("force", "drag", "lift", "力", "阻力", "升力"),
    "heat_flux": ("heat flux", "heat transfer", "热流", "换热"),
}

_QUANTITY_MARKERS = {
    "velocity": ("velocity", "speed", "速度"),
    "velocity_magnitude": ("velocity", "speed", "速度"),
    "kinematic_pressure": ("pressure", "压力"),
    "dynamic_pressure": ("pressure", "压力"),
    "temperature": ("temperature", "温度"),
    "density": ("density", "密度"),
}

_ALL_TIME_MARKERS = (
    "throughout",
    "at all times",
    "every time",
    "entire simulation",
    "全程",
    "所有时刻",
    "整个仿真",
)
_FINAL_TIME_MARKERS = (
    "at the end",
    "final time",
    "final value",
    "最终时刻",
    "最终值",
)
_RANGE_TIME_MARKERS = (
    "time range",
    "during the interval",
    "时间范围",
    "时间区间",
)


def _contains_marker(statement: str, markers: tuple[str, ...]) -> bool:
    folded = statement.casefold()
    return any(marker.casefold() in folded for marker in markers)


def _first_number_after_marker(
    statement: str,
    markers: tuple[str, ...],
) -> float | None:
    folded = statement.casefold()
    positions = tuple(
        (position, len(marker))
        for marker in markers
        if (position := folded.find(marker.casefold())) >= 0
    )
    if not positions:
        return None
    position, width = min(positions)
    match = _NUMBER.search(statement[position + width :])
    return float(match.group(0)) if match is not None else None


def _acceptance_semantics_match(
    request: AcceptanceRequest,
    statement: str,
) -> bool:
    if not _contains_marker(statement, _OPERATOR_MARKERS[request.operator]):
        return False
    if not _contains_marker(
        statement,
        _OBSERVATION_MARKERS[request.observation.kind],
    ):
        return False
    if request.observation.kind == "region_average":
        quantity_markers = _QUANTITY_MARKERS.get(request.observation.quantity)
        if quantity_markers is None or not _contains_marker(
            statement,
            quantity_markers,
        ):
            return False
    if request.unit != "1":
        compact_statement = re.sub(r"\s+", "", statement).casefold()
        compact_unit = re.sub(r"\s+", "", request.unit).casefold()
        if compact_unit not in compact_statement:
            return False
    declared_scope = None
    if _contains_marker(statement, _ALL_TIME_MARKERS):
        declared_scope = "all"
    elif _contains_marker(statement, _RANGE_TIME_MARKERS):
        declared_scope = "range"
    elif _contains_marker(statement, _FINAL_TIME_MARKERS):
        declared_scope = "final"
    if declared_scope == "all" and request.scope.time != "all":
        return False
    if declared_scope == "range" and request.scope.time != "range":
        return False
    if declared_scope == "final" and request.scope.time not in {"final", "latest"}:
        return False
    if declared_scope is None and request.scope.time not in {"final", "latest"}:
        return False
    return True


def _acceptance_has_task_authority(
    request: AcceptanceRequest,
    statements: tuple[str, ...],
) -> bool:
    authority = {item.strip().casefold(): item for item in statements}
    matched = next(
        (
            authority[item.detail.strip().casefold()]
            for item in request.provenance
            if item.kind in {"user_quote", "explicit_task_fact"}
            and item.detail.strip().casefold() in authority
        ),
        None,
    )
    if matched is None:
        return False
    if not _acceptance_semantics_match(request, matched):
        return False
    numeric_statement = matched
    if request.unit != "1":
        numeric_statement = re.sub(
            re.escape(request.unit),
            "",
            numeric_statement,
            flags=re.IGNORECASE,
        )
    declared_numbers = tuple(
        float(item) for item in _NUMBER.findall(numeric_statement)
    )
    if request.limit is not None:
        declared_limit = _first_number_after_marker(
            numeric_statement,
            _OPERATOR_MARKERS[request.operator],
        )
        if declared_limit is None or not math.isclose(
            request.limit,
            declared_limit,
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        ):
            return False
    request_numbers = tuple(
        value
        for value in (
            request.lower,
            request.upper,
            request.reference,
            request.tolerance,
            request.scope.start,
            request.scope.end,
        )
        if value is not None
    )
    return all(
        any(
            math.isclose(value, declared, rel_tol=1.0e-12, abs_tol=1.0e-15)
            for declared in declared_numbers
        )
        for value in request_numbers
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
    acceptance_requests: list[AcceptanceRequest] = []
    for request in response.acceptance_requests:
        if (
            request.source == "user_text"
            and request.confirmed
            and _acceptance_has_task_authority(
                request,
                tuple(task.acceptance_intent),
            )
        ):
            acceptance_requests.append(request)
            continue
        acceptance_requests.append(
            request.model_copy(
                update={
                    "source": "model_inference",
                    "confirmed": False,
                    "provenance": (
                        *request.provenance,
                        FactEvidence(
                            kind="authority_audit",
                            detail=(
                                "model acceptance threshold has no matching "
                                "TaskSpec acceptance statement or confirmation"
                            ),
                        ),
                    ),
                }
            )
        )
        warnings.append(
            "ACCEPTANCE_AUTHORITY_DOWNGRADED:" + request.condition_id
        )
    return response.model_copy(
        update={
            "facts": ordered,
            "acceptance_requests": tuple(acceptance_requests),
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
