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
    StructuredOutputNormalization,
)
from foampilot.preprocessing import ExecutedMeshFacts, InputMeshFacts
from foampilot.observations.models import ObservationRequest, ObservationScope
from foampilot.observations.registry import (
    UnsupportedObservationError,
    first_party_observation_registry,
)
from foampilot.acceptance.models import AcceptanceRequest
from foampilot.tasks import TaskSpec

from .provenance import (
    FactEvidence,
    ImpactLevel,
    JsonValue,
    ResolvedValue,
    StrictModel,
)


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
_REPORTING_LIMIT_PREFIXES = ("acceptance.", "observations.")
_FOUNDATION10_MESH_COMPATIBILITY_PATHS = {
    "mesh.foundation_openfoam_10_compatibility",
    "mesh.openfoam10_compatibility",
}


class IntentUncertainty(StrictModel):
    """Candidate-free ambiguity emitted before engineering design exists."""

    question_id: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
    )
    field_path: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*$"
    )
    impact: ImpactLevel
    kind: Literal["design_required", "information_required", "conflict"]
    prompt_zh: str = Field(min_length=1)
    reason_zh: str = Field(min_length=1)
    conflicting_evidence: tuple[FactEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "conflict" and len(self.conflicting_evidence) < 2:
            raise ValueError(
                "conflict uncertainty requires conflicting evidence"
            )
        if self.kind != "conflict" and self.conflicting_evidence:
            raise ValueError(
                "only conflict uncertainty may contain conflicting evidence"
            )
        return self


class SimulationIntent(StrictModel):
    schema_version: Literal[1] = 1
    facts: tuple[ResolvedValue[JsonValue], ...] = ()
    constraints: tuple[str, ...] = ()
    requested_observables: tuple[str, ...] = ()
    observation_requests: tuple[ObservationRequest, ...] = ()
    acceptance_requests: tuple[AcceptanceRequest, ...] = ()
    acceptance_intent: tuple[str, ...] = ()
    uncertainties: tuple[IntentUncertainty, ...] = ()
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


def _intent_observation_nodes(
    payload: dict[str, object],
) -> tuple[tuple[str, dict[str, object]], ...]:
    nodes: list[tuple[str, dict[str, object]]] = []
    observations = payload.get("observation_requests")
    if isinstance(observations, list):
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                continue
            nodes.append((f"observation_requests.{index}", observation))
    acceptance = payload.get("acceptance_requests")
    if isinstance(acceptance, list):
        for index, request in enumerate(acceptance):
            if not isinstance(request, dict):
                continue
            observation = request.get("observation")
            if not isinstance(observation, dict):
                continue
            nodes.append(
                (f"acceptance_requests.{index}.observation", observation)
            )
    return tuple(nodes)


def _intent_scope_nodes(
    payload: dict[str, object],
) -> tuple[tuple[str, dict[str, object]], ...]:
    return tuple(
        (location + ".scope", scope)
        for location, observation in _intent_observation_nodes(payload)
        if isinstance((scope := observation.get("scope")), dict)
    )


def normalize_simulation_intent_input(
    output_text: str,
) -> tuple[SimulationIntent, tuple[StructuredOutputNormalization, ...]]:
    """Apply only registry-proven aliases and unambiguous scope repairs."""

    payload = json.loads(output_text)
    if not isinstance(payload, dict):
        return SimulationIntent.model_validate(payload), ()
    records: list[StructuredOutputNormalization] = []
    registry = first_party_observation_registry()
    for location, observation in _intent_observation_nodes(payload):
        kind = observation.get("kind")
        quantity = observation.get("quantity")
        dimension = observation.get("dimension")
        if not all(isinstance(item, str) for item in (kind, quantity, dimension)):
            continue
        try:
            descriptor = registry.resolve(kind)
        except UnsupportedObservationError:
            continue
        contract = descriptor.resolve_request_contract(quantity, dimension)
        if contract is None:
            continue
        if quantity != contract.quantity:
            observation["quantity"] = contract.quantity
            records.append(
                StructuredOutputNormalization(
                    code="INTENT_QUANTITY_ALIAS_BOUND",
                    location=location + ".quantity",
                    original=quantity,
                    normalized=contract.quantity,
                )
            )
        if dimension != contract.dimension:
            observation["dimension"] = contract.dimension
            records.append(
                StructuredOutputNormalization(
                    code="INTENT_DIMENSION_ALIAS_BOUND",
                    location=location + ".dimension",
                    original=dimension,
                    normalized=contract.dimension,
                )
            )
    for location, scope in _intent_scope_nodes(payload):
        kind = scope.get("kind")
        names = scope.get("names")
        region = scope.get("region")
        if kind == "region":
            if isinstance(names, list) and len(names) == 1 and region is None:
                scope["region"] = names[0]
                records.append(
                    StructuredOutputNormalization(
                        code="INTENT_REGION_SCOPE_BOUND",
                        location=location + ".region",
                        original=None,
                        normalized=str(names[0]),
                    )
                )
            elif (
                names is None or (isinstance(names, list) and len(names) == 0)
            ) and isinstance(region, str):
                scope["names"] = [region]
                records.append(
                    StructuredOutputNormalization(
                        code="INTENT_REGION_SCOPE_BOUND",
                        location=location + ".names",
                        original=(
                            None
                            if names is None
                            else "[]"
                        ),
                        normalized=json.dumps([region], ensure_ascii=False),
                    )
                )
    return SimulationIntent.model_validate(payload), tuple(records)


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


def _reconcile_region_scope_with_mesh(
    scope: ObservationScope,
    mesh_facts: tuple[InputMeshFacts, ...],
) -> tuple[ObservationScope, bool]:
    """Reclassify an unambiguous cellZone mislabeled as a mesh region."""

    if scope.kind != "region" or scope.region is None:
        return scope, False
    if any(mesh.region == scope.region for mesh in mesh_facts):
        return scope, False
    matching_meshes = tuple(
        mesh
        for mesh in mesh_facts
        if any(zone.name == scope.region for zone in mesh.cell_zones)
    )
    if len(matching_meshes) != 1:
        return scope, False
    return (
        scope.model_copy(
            update={
                "kind": "cell_zone",
                "region": matching_meshes[0].region,
            }
        ),
        True,
    )


def _redundant_patch_pair_flow_balance(
    request: ObservationRequest,
    observations: tuple[ObservationRequest, ...],
    acceptance_observation_ids: set[str],
) -> bool:
    if (
        request.kind != "flow_rate"
        or request.scope.kind != "patch_pair"
        or request.observation_id in acceptance_observation_ids
    ):
        return False
    return all(
        any(
            candidate.kind == "flow_rate"
            and candidate.scope.kind == "patch"
            and candidate.scope.names == (patch,)
            and candidate.scope.region == request.scope.region
            and candidate.dimension == request.dimension
            and candidate.time_selection == request.time_selection
            for candidate in observations
        )
        for patch in request.scope.names
    )


def _reconcile_intent(
    response: SimulationIntent,
    *,
    task: TaskSpec,
    fact_ids: set[str],
    mesh_facts: tuple[InputMeshFacts, ...],
    executed_mesh_facts: tuple[ExecutedMeshFacts, ...],
) -> SimulationIntent:
    explicit = {item.field_path: item for item in task.explicit_facts}
    task_contract = {
        "execution.required_outputs": ResolvedValue(
            field_path="execution.required_outputs",
            value=list(task.required_outputs),
            source="deterministic_rule",
            impact="high",
            evidence=(
                FactEvidence(
                    kind="task_contract",
                    detail=(
                        "TaskSpec.required_outputs is the authoritative output "
                        "contract"
                    ),
                    reference=f"task:{task.task_id}",
                ),
            ),
            confirmed=True,
        )
    }
    facts: dict[str, ResolvedValue] = {}
    warnings = list(response.audit_warnings)
    for fact in response.facts:
        if fact.field_path in explicit or fact.field_path in task_contract:
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
        if fact.field_path in task_contract:
            continue
        if fact.field_path == "physics.solver":
            facts["solver.family"] = fact.model_copy(
                update={"field_path": "solver.family"}
            )
            continue
        if fact.field_path == "geometry.input":
            geometry = task.geometry
            if geometry is not None:
                facts["geometry.length_unit"] = fact.model_copy(
                    update={
                        "field_path": "geometry.length_unit",
                        "value": geometry.length_unit,
                    }
                )
                for role in geometry.patch_roles:
                    path = f"boundaries.{role.name}.role"
                    facts[path] = fact.model_copy(
                        update={"field_path": path, "value": role.role}
                    )
                for role in geometry.region_roles:
                    path = f"regions.{role.name}.role"
                    facts[path] = fact.model_copy(
                        update={"field_path": path, "value": role.role}
                    )
        facts[fact.field_path] = fact
    facts.update(task_contract)

    ordered = tuple(facts[path] for path in sorted(facts))
    target_mesh_probe_succeeded = (
        task.openfoam_target.distribution == "foundation"
        and task.openfoam_target.version == "10"
        and bool(executed_mesh_facts)
        and all(
            item.mesh_check.executed
            and item.mesh_check.return_code == 0
            and not item.mesh_check.timed_out
            and item.mesh_check.mesh_ok is True
            for item in executed_mesh_facts
        )
    )
    uncertainties: list[IntentUncertainty] = []
    for uncertainty in response.uncertainties:
        if (
            uncertainty.kind == "information_required"
            and uncertainty.field_path
            in _FOUNDATION10_MESH_COMPATIBILITY_PATHS
            and target_mesh_probe_succeeded
        ):
            warnings.append(
                "INTENT_UNCERTAINTY_RESOLVED_BY_MESH_PROBE:"
                + uncertainty.field_path
            )
            continue
        if (
            uncertainty.kind == "information_required"
            and uncertainty.field_path.startswith(_REPORTING_LIMIT_PREFIXES)
        ):
            warnings.append(
                "INTENT_REPORTING_LIMITATION:" + uncertainty.field_path
            )
            continue
        uncertainties.append(uncertainty)
    observation_requests: list[ObservationRequest] = []
    for request in response.observation_requests:
        scope, reconciled = _reconcile_region_scope_with_mesh(
            request.scope,
            mesh_facts,
        )
        observation_requests.append(
            request.model_copy(update={"scope": scope})
            if reconciled
            else request
        )
        if reconciled:
            warnings.append(
                "INTENT_REGION_SCOPE_RECONCILED_TO_CELL_ZONE:"
                + request.observation_id
            )

    reconciled_observations = tuple(observation_requests)
    acceptance_observation_ids = {
        request.observation.observation_id
        for request in response.acceptance_requests
    }
    observation_requests = []
    for request in reconciled_observations:
        if _redundant_patch_pair_flow_balance(
            request,
            reconciled_observations,
            acceptance_observation_ids,
        ):
            warnings.append(
                "INTENT_REDUNDANT_FLOW_BALANCE_REPRESENTED_BY_PATCH_FLOWS:"
                + request.observation_id
            )
            continue
        observation_requests.append(request)

    acceptance_requests: list[AcceptanceRequest] = []
    for original_request in response.acceptance_requests:
        scope, reconciled = _reconcile_region_scope_with_mesh(
            original_request.observation.scope,
            mesh_facts,
        )
        request = (
            original_request.model_copy(
                update={
                    "observation": original_request.observation.model_copy(
                        update={"scope": scope}
                    )
                }
            )
            if reconciled
            else original_request
        )
        if reconciled:
            warnings.append(
                "INTENT_ACCEPTANCE_REGION_SCOPE_RECONCILED_TO_CELL_ZONE:"
                + request.condition_id
            )
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
            "uncertainties": tuple(uncertainties),
            "observation_requests": tuple(observation_requests),
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
    executed_mesh_facts: tuple[ExecutedMeshFacts, ...] = (),
    capability_kinds: tuple[str, ...],
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> SimulationIntent:
    """Interpret the user request without choosing files, commands or numerics."""

    assets = tuple(_asset_summary(item) for item in asset_facts)
    meshes = tuple(_mesh_summary(item) for item in mesh_facts)
    observation_registry = first_party_observation_registry()
    available_observation_contracts = tuple(
        {
            "kind": kind,
            "quantity": contract.quantity,
            "dimension": contract.dimension,
            "supported_scope_kinds": list(
                observation_registry.resolve(kind).supported_scope_kinds
            ),
        }
        for kind, contract in observation_registry.request_contracts()
    )
    request = ModelRequest(
        purpose="interpret-simulation-intent",
        system_prompt=(
            "You interpret simulation intent only. Do not write OpenFOAM files, "
            "choose numerical schemes, create commands, or assign confidence. "
            "Report ambiguity as candidate-free structured uncertainties. Use "
            "design_required for solver, material, boundary, time, numerical, "
            "or region-model values that Case Designer can propose. Use "
            "information_required only for facts that must come from the user "
            "or an asset and without which a safe case cannot be authored. Missing "
            "Foundation OpenFOAM 10 mesh compatibility must use the field path "
            "mesh.foundation_openfoam_10_compatibility; the system may resolve it "
            "from an executed target-version mesh probe. Missing "
            "acceptance thresholds or optional observation sampling scopes are "
            "reporting limitations, not pre-design information blockers; record "
            "them as audit warnings. The Intent stage must never emit confirmable "
            "candidates. Use scope kind cell_zone for an OpenFOAM cellZone. "
            "Reserve scope kind region for a named OpenFOAM mesh region; do not "
            "use it for a cellZone. For a region scope, emit both region and a "
            "one-item names array containing the identical mesh-region name. "
            "Flow-rate observations support one patch per request. To compare "
            "inlet and outlet flow, emit one patch-scoped flow-rate request for "
            "each patch; do not emit a patch_pair flow-rate request. "
            "Observation IDs and quantities are machine identifiers; emit them "
            "in lower_snake_case. Every observation request, including an "
            "observation nested in an acceptance request, must use the exact "
            "canonical quantity and dimension listed for its kind in "
            "AvailableObservationContracts. Do not emit aliases or invent "
            "quantity/dimension combinations. "
            "A model response may "
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
                "AvailableObservationContracts": (
                    available_observation_contracts
                ),
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
        output_normalizer=normalize_simulation_intent_input,
    ).value
    fact_ids = {
        str(item["fact_id"])
        for item in (*assets, *meshes)
    }
    return _reconcile_intent(
        response,
        task=task,
        fact_ids=fact_ids,
        mesh_facts=mesh_facts,
        executed_mesh_facts=executed_mesh_facts,
    )


__all__ = [
    "IntentUncertainty",
    "SimulationIntent",
    "interpret_intent",
    "normalize_simulation_intent_input",
]
