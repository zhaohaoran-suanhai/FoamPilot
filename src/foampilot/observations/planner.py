"""Deterministic pre-authoring observation evidence planning."""

from __future__ import annotations

from foampilot.preprocessing import InputMeshFacts
from foampilot.simulation import SimulationIntent

from .models import EvidenceStrategy, ObservationItem, ObservationPlan, ObservationRequest
from .registry import ObservationExtensionRegistry


class ObservationPlanningError(ValueError):
    pass


def _validate_scope(request: ObservationRequest, facts: tuple[InputMeshFacts, ...]) -> None:
    if request.scope.kind in {"patch", "patch_pair"}:
        known = {patch.name for mesh in facts for patch in mesh.patches}
    elif request.scope.kind == "cell_zone":
        known = {zone.name for mesh in facts for zone in mesh.cell_zones}
    elif request.scope.kind == "region":
        known = {mesh.region for mesh in facts if mesh.region is not None}
    else:
        return
    missing = tuple(name for name in request.scope.names if name not in known)
    if missing:
        raise ObservationPlanningError(
            "MESH_SCOPE_UNKNOWN: " + ", ".join(missing)
        )


def _strategy(request: ObservationRequest, registry: ObservationExtensionRegistry) -> EvidenceStrategy:
    descriptor = registry.resolve(request.kind)
    if request.scope.kind not in descriptor.supported_scope_kinds:
        raise ObservationPlanningError(
            f"OBSERVATION_SCOPE_UNSUPPORTED: {request.kind}:{request.scope.kind}"
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
        del design, acceptance_plan
        unique: dict[str, ObservationRequest] = {}
        for request in intent.observation_requests:
            previous = unique.get(request.observation_id)
            if previous is not None and previous != request:
                raise ObservationPlanningError(
                    f"OBSERVATION_ID_CONFLICT: {request.observation_id}"
                )
            unique[request.observation_id] = request
        items = []
        for observation_id in sorted(unique):
            request = unique[observation_id]
            _validate_scope(request, mesh_facts)
            items.append(
                ObservationItem(
                    **request.model_dump(mode="python"),
                    evidence_strategy=_strategy(request, registry),
                )
            )
        return ObservationPlan(items=tuple(items))


__all__ = ["ObservationPlanner", "ObservationPlanningError"]
