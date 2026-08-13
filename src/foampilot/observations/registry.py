"""Closed registry of trusted first-party observation capabilities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceStrategyKind, ObservationKind


class ObservationRegistryError(ValueError):
    pass


class UnsupportedObservationError(LookupError):
    pass


class QuantityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quantity: str
    dimension: str
    field: str
    unit: str
    value_shape: Literal["scalar", "vector"] = "scalar"
    reduction: Literal["identity", "magnitude"] = "identity"
    solver_compressibility: Literal[
        "any", "incompressible", "compressible"
    ] = "any"
    evidence_field_dimensions: tuple[tuple[str, str], ...]


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
    quantity_contracts: tuple[QuantityContract, ...] = ()

    def resolve_quantity_contract(
        self,
        quantity: str,
        dimension: str,
    ) -> QuantityContract | None:
        return next(
            (
                contract
                for contract in self.quantity_contracts
                if contract.quantity == quantity
                and contract.dimension == dimension
            ),
            None,
        )


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
        ("residual", ("global",), ("run_facts",), ()),
        ("continuity", ("global",), ("run_facts",), ()),
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
            ("unavailable",),
            ("U", "p"),
        ),
        (
            "heat_flux",
            ("patch",),
            ("unavailable",),
            ("T",),
        ),
    )
    for kind, scopes, strategies, fields in definitions:
        quantity_contracts = {
            "flow_rate": (
                QuantityContract(
                    quantity="volumetric_flow_rate",
                    dimension="0 3 -1 0 0 0 0",
                    field="phi",
                    unit="m3/s",
                    reduction="magnitude",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("phi", "0 3 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="flow_rate",
                    dimension="0 3 -1 0 0 0 0",
                    field="phi",
                    unit="m3/s",
                    reduction="magnitude",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("phi", "0 3 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="signed_volumetric_flow_rate",
                    dimension="0 3 -1 0 0 0 0",
                    field="phi",
                    solver_compressibility="incompressible",
                    unit="m3/s",
                    evidence_field_dimensions=(("phi", "0 3 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="mass_flow_rate",
                    dimension="1 0 -1 0 0 0 0",
                    field="phi",
                    unit="kg/s",
                    reduction="magnitude",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("phi", "1 0 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="signed_mass_flow_rate",
                    dimension="1 0 -1 0 0 0 0",
                    field="phi",
                    solver_compressibility="compressible",
                    unit="kg/s",
                    evidence_field_dimensions=(("phi", "1 0 -1 0 0 0 0"),),
                ),
            ),
            "pressure_difference": (
                QuantityContract(
                    quantity="pressure_difference",
                    dimension="0 2 -2 0 0 0 0",
                    field="p",
                    unit="m2/s2",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("p", "0 2 -2 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="pressure_difference",
                    dimension="1 -1 -2 0 0 0 0",
                    field="p",
                    unit="Pa",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("p", "1 -1 -2 0 0 0 0"),),
                ),
            ),
            "region_average": (
                QuantityContract(
                    quantity="velocity",
                    dimension="0 1 -1 0 0 0 0",
                    field="U",
                    unit="m/s",
                    value_shape="vector",
                    evidence_field_dimensions=(("U", "0 1 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="region_average",
                    dimension="0 1 -1 0 0 0 0",
                    field="U",
                    unit="m/s",
                    value_shape="vector",
                    evidence_field_dimensions=(("U", "0 1 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="velocity_magnitude",
                    dimension="0 1 -1 0 0 0 0",
                    field="U",
                    unit="m/s",
                    value_shape="vector",
                    reduction="magnitude",
                    evidence_field_dimensions=(("U", "0 1 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="temperature",
                    dimension="0 0 0 1 0 0 0",
                    field="T",
                    unit="K",
                    evidence_field_dimensions=(("T", "0 0 0 1 0 0 0"),),
                ),
                QuantityContract(
                    quantity="kinematic_pressure",
                    dimension="0 2 -2 0 0 0 0",
                    field="p",
                    unit="m2/s2",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("p", "0 2 -2 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="pressure",
                    dimension="1 -1 -2 0 0 0 0",
                    field="p",
                    unit="Pa",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("p", "1 -1 -2 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="density",
                    dimension="1 -3 0 0 0 0 0",
                    field="rho",
                    unit="kg/m3",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("rho", "1 -3 0 0 0 0 0"),),
                ),
            ),
        }.get(kind, ())
        registry.register(
            ObservationExtensionDescriptor(
                kind=kind,
                supported_scope_kinds=scopes,
                strategies=strategies,
                required_fields=fields,
                runtime_configuration_supported=(
                    "runtime_configuration" in strategies
                ),
                quantity_contracts=quantity_contracts,
            )
        )
    return registry


__all__ = [
    "ObservationExtensionDescriptor",
    "ObservationExtensionRegistry",
    "ObservationRegistryError",
    "QuantityContract",
    "UnsupportedObservationError",
    "first_party_observation_registry",
]
