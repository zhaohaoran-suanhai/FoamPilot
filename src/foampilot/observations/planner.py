"""Deterministic pre-authoring observation evidence planning."""

from __future__ import annotations

from foampilot.preprocessing import InputMeshFacts
from foampilot.simulation import SimulationIntent
from foampilot.routing.registry import capability_for_solver

from .models import (
    EvidenceStrategy,
    ObservationItem,
    ObservationPlan,
    ObservationRequest,
    merge_compatible_observation_requests,
)
from .registry import ObservationExtensionRegistry

if False:  # pragma: no cover - import-only type boundary
    from foampilot.acceptance import AcceptancePlan


class ObservationPlanningError(ValueError):
    pass


def _merge_request(
    unique: dict[str, ObservationRequest],
    request: ObservationRequest,
) -> None:
    previous = unique.get(request.observation_id)
    if previous is not None:
        merged = merge_compatible_observation_requests(previous, request)
        if merged is None:
            raise ObservationPlanningError(
                f"OBSERVATION_ID_CONFLICT: {request.observation_id}"
            )
        request = merged
    unique[request.observation_id] = request


def _design_scope_names(design: object, prefix: str) -> set[str]:
    proposal = getattr(design, "proposal", None)
    values = getattr(proposal, "iter_values", lambda: ())()
    names = set()
    for fact in values:
        path = str(getattr(fact, "field_path", ""))
        parts = path.split(".")
        if len(parts) >= 3 and parts[0] == prefix:
            names.add(parts[1])
    return names


def _validate_scope(
    request: ObservationRequest,
    facts: tuple[InputMeshFacts, ...],
    design: object,
) -> None:
    local_scope = request.scope.kind in {
        "patch", "patch_pair", "cell_zone", "region"
    }
    if local_scope and request.scope.region is None and len(facts) > 1:
        raise ObservationPlanningError(
            "MESH_REGION_REQUIRED: local scope is ambiguous across mesh regions"
        )
    relevant = facts
    if request.scope.region is not None:
        relevant = tuple(
            mesh for mesh in facts if mesh.region == request.scope.region
        )
        if not relevant:
            raise ObservationPlanningError(
                "MESH_REGION_UNKNOWN: " + request.scope.region
            )
    if local_scope and not facts:
        known = {
            "patch": _design_scope_names(design, "boundaries"),
            "patch_pair": _design_scope_names(design, "boundaries"),
            "cell_zone": _design_scope_names(design, "cell_zones"),
            "region": _design_scope_names(design, "regions"),
        }[request.scope.kind]
        missing = tuple(name for name in request.scope.names if name not in known)
        if missing:
            raise ObservationPlanningError(
                "MESH_SCOPE_UNKNOWN: " + ", ".join(missing)
            )
        if request.scope.region is not None:
            regions = _design_scope_names(design, "regions")
            if request.scope.region not in regions:
                raise ObservationPlanningError(
                    "MESH_REGION_UNKNOWN: " + request.scope.region
                )
        return
    if request.scope.kind in {"patch", "patch_pair"}:
        known = {patch.name for mesh in relevant for patch in mesh.patches}
    elif request.scope.kind == "cell_zone":
        known = {zone.name for mesh in relevant for zone in mesh.cell_zones}
    elif request.scope.kind == "region":
        known = {mesh.region for mesh in relevant if mesh.region is not None}
    else:
        return
    missing = tuple(name for name in request.scope.names if name not in known)
    if missing:
        raise ObservationPlanningError(
            "MESH_SCOPE_UNKNOWN: " + ", ".join(missing)
        )


def _solver_compressibility(design: object) -> str | None:
    if design is None:
        return None
    proposal = getattr(design, "proposal", None)
    solver_fact = getattr(proposal, "solver_family", None)
    solver = getattr(solver_fact, "value", None)
    capability = capability_for_solver(str(solver)) if solver is not None else None
    return capability.compressibility if capability is not None else "unknown"


def _strategy(
    request: ObservationRequest,
    registry: ObservationExtensionRegistry,
    design: object,
) -> EvidenceStrategy:
    descriptor = registry.resolve(request.kind)
    if request.scope.kind not in descriptor.supported_scope_kinds:
        raise ObservationPlanningError(
            f"OBSERVATION_SCOPE_UNSUPPORTED: {request.kind}:{request.scope.kind}"
        )
    request_contracts = descriptor.available_request_contracts()
    request_contract = descriptor.resolve_request_contract(
        request.quantity,
        request.dimension,
    )
    if request_contracts and (
        request_contract is None
        or request.quantity != request_contract.quantity
        or request.dimension != request_contract.dimension
    ):
        return EvidenceStrategy(
            kind="unavailable",
            reason=(
                "no registered Foundation OpenFOAM 10 canonical "
                "quantity/dimension contract for "
                f"{request.quantity}:{request.dimension}"
            ),
        )
    if descriptor.strategies == ("unavailable",):
        return EvidenceStrategy(
            kind="unavailable",
            reason=(
                f"Foundation OpenFOAM 10 collector for {request.kind} "
                "is not implemented"
            ),
        )
    contract = descriptor.resolve_quantity_contract(
        request.quantity,
        request.dimension,
    )
    if descriptor.quantity_contracts and contract is None:
        return EvidenceStrategy(
            kind="unavailable",
            reason=(
                "no registered Foundation OpenFOAM 10 quantity/dimension "
                f"contract for {request.quantity}:{request.dimension}"
            ),
        )
    compressibility = _solver_compressibility(design)
    if (
        contract is not None
        and compressibility is not None
        and contract.solver_compressibility != "any"
        and contract.solver_compressibility != compressibility
    ):
        return EvidenceStrategy(
            kind="unavailable",
            reason=(
                "quantity/dimension contract is incompatible with the frozen "
                f"solver/field semantics: {request.quantity}:{request.dimension} "
                f"requires {contract.solver_compressibility}, got {compressibility}"
            ),
        )
    if "run_facts" in descriptor.strategies:
        return EvidenceStrategy(kind="run_facts")
    if request.time_selection.kind == "history":
        if "runtime_configuration" not in descriptor.strategies:
            return EvidenceStrategy(
                kind="unavailable",
                reason="requested history cannot be recovered or collected",
            )
        return EvidenceStrategy(
            kind="runtime_configuration",
            collector_id=f"foundation10.{request.kind}",
        )
    if "written_field" in descriptor.strategies:
        # Final field-backed metrics still require an allowlisted calculator;
        # choosing it here does not inject a runtime function object.
        if "postprocess_command" in descriptor.strategies:
            return EvidenceStrategy(
                kind="postprocess_command",
                collector_id=f"foundation10.{request.kind}",
            )
        return EvidenceStrategy(kind="written_field")
    if "postprocess_command" in descriptor.strategies:
        return EvidenceStrategy(
            kind="postprocess_command",
            collector_id=f"foundation10.{request.kind}",
        )
    return EvidenceStrategy(
        kind="unavailable",
        reason="no supported evidence strategy",
    )


class ObservationPlanner:
    def compile(
        self,
        *,
        intent: SimulationIntent,
        design: object,
        mesh_facts: tuple[InputMeshFacts, ...],
        registry: ObservationExtensionRegistry,
        acceptance_plan: object | None = None,
    ) -> ObservationPlan:
        unique: dict[str, ObservationRequest] = {}
        for request in intent.observation_requests:
            _merge_request(unique, request)
        condition_ids: dict[str, list[str]] = {}
        if acceptance_plan is not None:
            for request in acceptance_plan.observation_requests:
                _merge_request(unique, request)
            for condition in acceptance_plan.conditions:
                condition_ids.setdefault(condition.observation_id, []).append(
                    condition.condition_id
                )
        items = []
        for observation_id in sorted(unique):
            request = unique[observation_id]
            _validate_scope(request, mesh_facts, design)
            items.append(
                ObservationItem(
                    **request.model_dump(mode="python"),
                    evidence_strategy=_strategy(request, registry, design),
                    required_for_condition_ids=tuple(
                        sorted(condition_ids.get(observation_id, ()))
                    ),
                )
            )
        return ObservationPlan(items=tuple(items))


__all__ = ["ObservationPlanner", "ObservationPlanningError"]
