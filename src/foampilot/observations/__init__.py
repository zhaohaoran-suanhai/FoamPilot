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
    merge_compatible_observation_requests,
)
from .registry import (
    ObservationExtensionDescriptor,
    ObservationExtensionRegistry,
    ObservationRegistryError,
    ObservationRequestContract,
    QuantityContract,
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
    "ObservationRequestContract",
    "QuantityContract",
    "ObservationScope",
    "ObservationWarning",
    "TimeSelection",
    "UnsupportedObservationError",
    "first_party_observation_registry",
    "merge_compatible_observation_requests",
    "audit_observation_field_dimensions",
    "collect_foundation10_observation_artifacts",
    "compile_foundation10_observations",
    "inject_observation_fragments",
    "verify_observation_field_dimensions",
]


def __getattr__(name: str):
    if name in {"ObservationPlanner", "ObservationPlanningError"}:
        from .planner import ObservationPlanner, ObservationPlanningError

        return {
            "ObservationPlanner": ObservationPlanner,
            "ObservationPlanningError": ObservationPlanningError,
        }[name]
    if name in {
        "CompiledObservationFragments",
        "ObservationConfigFragment",
        "audit_observation_field_dimensions",
        "collect_foundation10_observation_artifacts",
        "compile_foundation10_observations",
        "inject_observation_fragments",
        "verify_observation_field_dimensions",
    }:
        from .openfoam10 import (
            CompiledObservationFragments,
            ObservationConfigFragment,
            audit_observation_field_dimensions,
            collect_foundation10_observation_artifacts,
            compile_foundation10_observations,
            inject_observation_fragments,
            verify_observation_field_dimensions,
        )

        return locals()[name]
    raise AttributeError(name)
