"""Public observation planning contracts."""

from .models import (
    EvidenceStrategy,
    EvidenceStrategyKind,
    ObservationItem,
    ObservationKind,
    ObservationPlan,
    ObservationRequest,
    ObservationScope,
    ObservationWarning,
    TimeSelection,
)
from .registry import (
    ObservationExtensionDescriptor,
    ObservationExtensionRegistry,
    ObservationRegistryError,
    UnsupportedObservationError,
    first_party_observation_registry,
)

__all__ = [
    "EvidenceStrategy",
    "EvidenceStrategyKind",
    "ObservationExtensionDescriptor",
    "ObservationExtensionRegistry",
    "ObservationItem",
    "ObservationKind",
    "ObservationPlan",
    "ObservationRequest",
    "ObservationRegistryError",
    "ObservationScope",
    "ObservationWarning",
    "TimeSelection",
    "UnsupportedObservationError",
    "first_party_observation_registry",
]


def __getattr__(name: str):
    if name in {"ObservationPlanner", "ObservationPlanningError"}:
        from .planner import ObservationPlanner, ObservationPlanningError

        return {
            "ObservationPlanner": ObservationPlanner,
            "ObservationPlanningError": ObservationPlanningError,
        }[name]
    raise AttributeError(name)
