"""System-owned capability confidence calculation."""

from __future__ import annotations

from dataclasses import dataclass

from .models import CapabilityConfidence


@dataclass(frozen=True)
class RouteEvidenceState:
    explicit_solver: bool
    solver_installed: bool
    has_conflict: bool
    compatible_candidate_count: int
    critical_physics_complete: bool
    used_model_route: bool


def calculate_confidence(
    state: RouteEvidenceState,
) -> CapabilityConfidence:
    if (
        state.explicit_solver
        and state.solver_installed
        and not state.has_conflict
    ):
        return CapabilityConfidence.HIGH
    if (
        not state.explicit_solver
        and state.compatible_candidate_count == 1
        and state.critical_physics_complete
        and not state.used_model_route
    ):
        return CapabilityConfidence.MEDIUM
    return CapabilityConfidence.LOW
