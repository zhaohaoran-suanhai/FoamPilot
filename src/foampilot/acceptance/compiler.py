"""Compile confirmed structured requests into executable acceptance gates."""

from __future__ import annotations

from .models import (
    AcceptanceCondition,
    AcceptancePlan,
    AcceptanceRequest,
    UncompiledRequirement,
)
from foampilot.observations import ObservationRequest


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
            if previous is not None and previous != request.observation:
                raise ValueError(
                    "ACCEPTANCE_OBSERVATION_CONFLICT: "
                    + request.observation.observation_id
                )
            observations[request.observation.observation_id] = request.observation
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
