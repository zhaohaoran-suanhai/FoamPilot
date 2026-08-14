"""Compile confirmed structured requests into executable acceptance gates."""

from __future__ import annotations

from .models import (
    AcceptanceCondition,
    AcceptancePlan,
    AcceptanceRequest,
    UncompiledRequirement,
)
from foampilot.observations import (
    ObservationRequest,
    merge_compatible_observation_requests,
)


class AcceptanceCompiler:
    def compile(
        self,
        *,
        observation_requests: tuple[ObservationRequest, ...],
        condition_requests: tuple[AcceptanceRequest, ...],
    ) -> AcceptancePlan:
        observations = {item.observation_id: item for item in observation_requests}
        conditions: list[AcceptanceCondition] = []
        uncompiled: list[UncompiledRequirement] = []
        seen_conditions: set[str] = set()
        for request in condition_requests:
            if request.condition_id in seen_conditions:
                raise ValueError(
                    f"ACCEPTANCE_CONDITION_DUPLICATE: {request.condition_id}"
                )
            seen_conditions.add(request.condition_id)
            previous = observations.get(request.observation.observation_id)
            observation = request.observation
            if previous is not None:
                merged = merge_compatible_observation_requests(
                    previous,
                    observation,
                )
                if merged is None:
                    raise ValueError(
                        "ACCEPTANCE_OBSERVATION_CONFLICT: "
                        + observation.observation_id
                    )
                observation = merged
            observations[observation.observation_id] = observation
            if request.source == "model_inference" and not request.confirmed:
                uncompiled.append(
                    UncompiledRequirement(
                        condition_id=request.condition_id,
                        code="ACCEPTANCE_CONFIRMATION_REQUIRED",
                        detail="inferred engineering threshold is not user-confirmed",
                        recovery="confirm the exact observable, operator, unit and threshold",
                    )
                )
                continue
            if not request.confirmed:
                uncompiled.append(
                    UncompiledRequirement(
                        condition_id=request.condition_id,
                        code="ACCEPTANCE_AUTHORITY_UNRESOLVED",
                        detail="acceptance request is not confirmed",
                        recovery="provide or confirm the exact acceptance condition",
                    )
                )
                continue
            selection = request.observation.time_selection
            scope = request.scope
            insufficient = (
                scope.time == "all" and selection.kind != "history"
            ) or (
                scope.time == "range"
                and not (
                    selection.kind == "history"
                    or (
                        selection.kind == "time_range"
                        and selection.start is not None
                        and selection.end is not None
                        and scope.start is not None
                        and scope.end is not None
                        and selection.start <= scope.start
                        and selection.end >= scope.end
                    )
                )
            )
            if insufficient:
                uncompiled.append(
                    UncompiledRequirement(
                        condition_id=request.condition_id,
                        code="ACCEPTANCE_OBSERVATION_TIME_SCOPE_INSUFFICIENT",
                        detail=(
                            "acceptance scope requires history that the observation "
                            "request does not preserve"
                        ),
                        recovery=(
                            "request history or a covering time_range before compiling "
                            "this acceptance condition"
                        ),
                    )
                )
                continue
            conditions.append(
                AcceptanceCondition(
                    condition_id=request.condition_id,
                    observation_id=request.observation.observation_id,
                    operator=request.operator,
                    limit=request.limit,
                    lower=request.lower,
                    upper=request.upper,
                    reference=request.reference,
                    tolerance=request.tolerance,
                    unit=request.unit,
                    scope=request.scope,
                    provenance=request.provenance,
                )
            )
        return AcceptancePlan(
            conditions=tuple(sorted(conditions, key=lambda item: item.condition_id)),
            observation_requests=tuple(
                observations[key] for key in sorted(observations)
            ),
            uncompiled=tuple(sorted(uncompiled, key=lambda item: item.condition_id)),
        )


__all__ = ["AcceptanceCompiler"]
