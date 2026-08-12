"""Closed registry of trusted first-party observation capabilities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceStrategyKind, ObservationKind


class ObservationRegistryError(ValueError):
    pass


class UnsupportedObservationError(LookupError):
    pass


class ObservationExtensionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ObservationKind
    supported_scope_kinds: tuple[
        Literal["global", "patch", "patch_pair", "cell_zone", "region"], ...
    ] = Field(min_length=1)
    strategies: tuple[EvidenceStrategyKind, ...] = Field(min_length=1)
    supported_targets: tuple[tuple[str, str], ...] = (("foundation", "10"),)
    required_fields: tuple[str, ...] = ()
    runtime_configuration_supported: bool = False


class ObservationExtensionRegistry:
    def __init__(self, *, entry_points_enabled: bool = False) -> None:
        if entry_points_enabled:
            raise ObservationRegistryError(
                "ENTRY_POINTS_DISABLED: third-party observation extensions are disabled"
            )
        self.entry_points_enabled = False
        self._descriptors: dict[str, ObservationExtensionDescriptor] = {}

    def register(self, descriptor: ObservationExtensionDescriptor) -> None:
        if descriptor.kind in self._descriptors:
            raise ObservationRegistryError(
                f"OBSERVATION_DUPLICATE: {descriptor.kind}"
            )
        self._descriptors[descriptor.kind] = descriptor

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._descriptors))

    def resolve(self, kind: str) -> ObservationExtensionDescriptor:
        try:
            return self._descriptors[kind]
        except KeyError as error:
            raise UnsupportedObservationError(
                f"OBSERVATION_UNSUPPORTED: {kind}"
            ) from error


def first_party_observation_registry() -> ObservationExtensionRegistry:
    registry = ObservationExtensionRegistry()
    definitions = (
        ("residual", ("global", "region"), ("run_facts",), ()),
        ("continuity", ("global", "region"), ("run_facts",), ()),
        (
            "flow_rate",
            ("patch",),
            ("written_field", "postprocess_command", "runtime_configuration"),
            ("phi",),
        ),
        (
            "pressure_difference",
            ("patch_pair",),
            ("written_field", "postprocess_command", "runtime_configuration"),
            ("p",),
        ),
        (
            "region_average",
            ("cell_zone", "region"),
            ("written_field", "postprocess_command", "runtime_configuration"),
            (),
        ),
        (
            "force",
            ("patch",),
            ("postprocess_command", "runtime_configuration"),
            ("U", "p"),
        ),
        (
            "heat_flux",
            ("patch",),
            ("postprocess_command", "runtime_configuration"),
            ("T",),
        ),
    )
    for kind, scopes, strategies, fields in definitions:
        registry.register(
            ObservationExtensionDescriptor(
                kind=kind,
                supported_scope_kinds=scopes,
                strategies=strategies,
                required_fields=fields,
                runtime_configuration_supported=(
                    "runtime_configuration" in strategies
                ),
            )
        )
    return registry


__all__ = [
    "ObservationExtensionDescriptor",
    "ObservationExtensionRegistry",
    "ObservationRegistryError",
    "UnsupportedObservationError",
    "first_party_observation_registry",
]
