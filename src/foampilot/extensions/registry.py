"""Deterministic registry for trusted first-party capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import CapabilityDescriptor


class TargetLike(Protocol):
    distribution: str
    version: str


class CapabilityRegistrationError(ValueError):
    pass


class CapabilityResolutionError(LookupError):
    pass


@dataclass(frozen=True)
class _Registration:
    descriptor: CapabilityDescriptor
    provider: object


class CapabilityRegistry:
    """Closed registry; dynamic Python entry points are intentionally disabled."""

    def __init__(self, *, entry_points_enabled: bool = False) -> None:
        if entry_points_enabled:
            raise CapabilityRegistrationError(
                "ENTRY_POINTS_DISABLED: third-party entry points are not trusted"
            )
        self._registrations: dict[str, _Registration] = {}
        self.entry_points_enabled = False

    @classmethod
    def first_party(cls) -> "CapabilityRegistry":
        registry = cls(entry_points_enabled=False)
        from foampilot.assets.openfoam_mesh import OpenFOAMPolyMeshAdapter

        adapter = OpenFOAMPolyMeshAdapter()
        registry.register(adapter.descriptor, adapter)
        return registry

    def register(
        self,
        descriptor: CapabilityDescriptor,
        provider: object,
    ) -> None:
        if descriptor.extension_id in self._registrations:
            raise CapabilityRegistrationError(
                f"DUPLICATE_EXTENSION_ID: {descriptor.extension_id}"
            )
        self._registrations[descriptor.extension_id] = _Registration(
            descriptor=descriptor,
            provider=provider,
        )

    def extension_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def descriptor(self, extension_id: str) -> CapabilityDescriptor:
        try:
            return self._registrations[extension_id].descriptor
        except KeyError as error:
            raise CapabilityResolutionError(
                f"CAPABILITY_EXTENSION_UNKNOWN: {extension_id}"
            ) from error

    def resolve(self, kind: str, target: TargetLike) -> object:
        kind_matches = [
            item
            for item in self._registrations.values()
            if kind in item.descriptor.capability_kinds
        ]
        matches = [
            item
            for item in kind_matches
            if item.descriptor.supports_target(
                target.distribution,
                target.version,
            )
        ]
        if len(matches) > 1:
            identities = ", ".join(
                sorted(item.descriptor.extension_id for item in matches)
            )
            raise CapabilityResolutionError(
                f"CAPABILITY_AMBIGUOUS: {kind}: {identities}"
            )
        if len(matches) == 1:
            return matches[0].provider
        if kind_matches:
            raise CapabilityResolutionError(
                "CAPABILITY_TARGET_UNSUPPORTED: "
                f"{kind} for {target.distribution} {target.version}"
            )
        raise CapabilityResolutionError(f"CAPABILITY_UNAVAILABLE: {kind}")


__all__ = [
    "CapabilityRegistrationError",
    "CapabilityRegistry",
    "CapabilityResolutionError",
]
