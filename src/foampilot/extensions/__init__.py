"""Trusted extension contracts and deterministic capability registry."""

from .models import CapabilityDescriptor, SupportedTarget
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
    "SupportedTarget",
]
