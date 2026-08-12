"""Trusted extension contracts and deterministic capability registry."""

from .models import CapabilityDescriptor, RequiredFact, SupportedTarget
from .registry import (
    CapabilityRegistrationError,
    CapabilityRegistry,
    CapabilityResolutionError,
)
__all__ = [
    "CapabilityDescriptor",
    "CapabilityRegistrationError",
    "CapabilityRegistry",
    "CapabilityResolutionError",
    "ComposedPlanFragments",
    "PlanContext",
    "PlanContributionError",
    "PlanFragment",
    "RequiredFact",
    "SupportedTarget",
]


def __getattr__(name: str):
    """Load planning contracts only for planning callers."""

    if name in {
        "ComposedPlanFragments",
        "PlanContext",
        "PlanContributionError",
        "PlanFragment",
    }:
        from .planning import (
            ComposedPlanFragments,
            PlanContext,
            PlanContributionError,
            PlanFragment,
        )

        return {
            "ComposedPlanFragments": ComposedPlanFragments,
            "PlanContext": PlanContext,
            "PlanContributionError": PlanContributionError,
            "PlanFragment": PlanFragment,
        }[name]
    raise AttributeError(name)
