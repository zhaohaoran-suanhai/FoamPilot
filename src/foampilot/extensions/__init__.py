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
    "RequiredFact",
    "SupportedTarget",
]
