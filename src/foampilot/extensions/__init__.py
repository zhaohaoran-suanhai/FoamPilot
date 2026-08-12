"""Trusted extension contracts and deterministic capability registry."""

from .models import CapabilityDescriptor, RequiredFact, SupportedTarget
from .registry import (
    CapabilityRegistrationError,
    CapabilityRegistry,
    CapabilityResolutionError,
)
from .planning import (
    ComposedPlanFragments,
    PlanContext,
    PlanContributionError,
    PlanFragment,
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
