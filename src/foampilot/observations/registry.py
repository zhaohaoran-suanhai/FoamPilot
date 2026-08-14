"""Closed registry of trusted first-party observation capabilities."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import EvidenceStrategyKind, ObservationKind


class ObservationRegistryError(ValueError):
    pass


class UnsupportedObservationError(LookupError):
    pass


class ObservationRequestContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quantity: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    dimension: str = Field(min_length=1)
    quantity_aliases: tuple[str, ...] = ()
    dimension_aliases: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_aliases(self) -> Self:
        for canonical, aliases, label in (
            (self.quantity, self.quantity_aliases, "quantity"),
            (self.dimension, self.dimension_aliases, "dimension"),
        ):
            if any(not alias.strip() for alias in aliases):
                raise ValueError(f"{label} aliases must not be blank")
            if len(aliases) != len(set(aliases)):
                raise ValueError(f"{label} aliases must be unique")
            if canonical in aliases:
                raise ValueError(
                    f"canonical {label} must not be repeated as an alias"
                )
        return self

    def accepts(self, quantity: str, dimension: str) -> bool:
        return (
            quantity == self.quantity or quantity in self.quantity_aliases
        ) and (
            dimension == self.dimension or dimension in self.dimension_aliases
        )


class QuantityContract(ObservationRequestContract):
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
    request_contracts: tuple[ObservationRequestContract, ...] = ()
    quantity_contracts: tuple[QuantityContract, ...] = ()

    def available_request_contracts(
        self,
    ) -> tuple[ObservationRequestContract, ...]:
        return (*self.request_contracts, *self.quantity_contracts)

    def resolve_request_contract(
        self,
        quantity: str,
        dimension: str,
    ) -> ObservationRequestContract | None:
        matches = tuple(
            contract
            for contract in self.available_request_contracts()
            if contract.accepts(quantity, dimension)
        )
        if len(matches) > 1:
            raise ObservationRegistryError(
                "OBSERVATION_REQUEST_ALIAS_AMBIGUOUS: "
                f"{self.kind}:{quantity}:{dimension}"
            )
        return matches[0] if matches else None

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
        bindings: dict[tuple[str, str], tuple[str, str]] = {}
        for contract in descriptor.available_request_contracts():
            canonical = (contract.quantity, contract.dimension)
            for quantity in (contract.quantity, *contract.quantity_aliases):
                for dimension in (
                    contract.dimension,
                    *contract.dimension_aliases,
                ):
                    key = (quantity, dimension)
                    previous = bindings.get(key)
                    if previous is not None and previous != canonical:
                        raise ObservationRegistryError(
                            "OBSERVATION_REQUEST_ALIAS_AMBIGUOUS: "
                            f"{descriptor.kind}:{quantity}:{dimension}"
                        )
                    bindings[key] = canonical
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

    def request_contracts(
        self,
    ) -> tuple[tuple[str, ObservationRequestContract], ...]:
        return tuple(
            (kind, contract)
            for kind in self.ids()
            for contract in sorted(
                self._descriptors[kind].available_request_contracts(),
                key=lambda item: (item.quantity, item.dimension),
            )
        )


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
    request_contracts_by_kind = {
        "residual": (
            ObservationRequestContract(
                quantity="solver_residual",
                dimension="1",
                dimension_aliases=("dimensionless",),
            ),
        ),
        "continuity": (
            ObservationRequestContract(
                quantity="continuity_error",
                dimension="1",
                dimension_aliases=("dimensionless",),
            ),
        ),
        "force": (
            ObservationRequestContract(
                quantity="force",
                dimension="1 1 -2 0 0 0 0",
            ),
        ),
        "heat_flux": (
            ObservationRequestContract(
                quantity="heat_flux",
                dimension="1 0 -3 0 0 0 0",
            ),
        ),
    }
    for kind, scopes, strategies, fields in definitions:
        quantity_contracts = {
            "flow_rate": (
                QuantityContract(
                    quantity="volumetric_flow_rate",
                    quantity_aliases=("Q",),
                    dimension="0 3 -1 0 0 0 0",
                    dimension_aliases=("L^3/T",),
                    field="phi",
                    unit="m3/s",
                    reduction="magnitude",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("phi", "0 3 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="flow_rate",
                    dimension="0 3 -1 0 0 0 0",
                    dimension_aliases=("L^3/T",),
                    field="phi",
                    unit="m3/s",
                    reduction="magnitude",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("phi", "0 3 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="signed_volumetric_flow_rate",
                    dimension="0 3 -1 0 0 0 0",
                    dimension_aliases=("L^3/T",),
                    field="phi",
                    solver_compressibility="incompressible",
                    unit="m3/s",
                    evidence_field_dimensions=(("phi", "0 3 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="mass_flow_rate",
                    dimension="1 0 -1 0 0 0 0",
                    dimension_aliases=("M/T",),
                    field="phi",
                    unit="kg/s",
                    reduction="magnitude",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("phi", "1 0 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="signed_mass_flow_rate",
                    dimension="1 0 -1 0 0 0 0",
                    dimension_aliases=("M/T",),
                    field="phi",
                    solver_compressibility="compressible",
                    unit="kg/s",
                    evidence_field_dimensions=(("phi", "1 0 -1 0 0 0 0"),),
                ),
            ),
            "pressure_difference": (
                QuantityContract(
                    quantity="pressure_difference",
                    quantity_aliases=("kinematic_pressure",),
                    dimension="0 2 -2 0 0 0 0",
                    dimension_aliases=("L^2/T^2",),
                    field="p",
                    unit="m2/s2",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("p", "0 2 -2 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="pressure_difference",
                    dimension="1 -1 -2 0 0 0 0",
                    dimension_aliases=("M/(L*T^2)",),
                    field="p",
                    unit="Pa",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("p", "1 -1 -2 0 0 0 0"),),
                ),
            ),
            "region_average": (
                QuantityContract(
                    quantity="velocity",
                    quantity_aliases=("U",),
                    dimension="0 1 -1 0 0 0 0",
                    dimension_aliases=("L/T",),
                    field="U",
                    unit="m/s",
                    value_shape="vector",
                    evidence_field_dimensions=(("U", "0 1 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="region_average",
                    dimension="0 1 -1 0 0 0 0",
                    dimension_aliases=("L/T",),
                    field="U",
                    unit="m/s",
                    value_shape="vector",
                    evidence_field_dimensions=(("U", "0 1 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="velocity_magnitude",
                    dimension="0 1 -1 0 0 0 0",
                    dimension_aliases=("L/T",),
                    field="U",
                    unit="m/s",
                    value_shape="vector",
                    reduction="magnitude",
                    evidence_field_dimensions=(("U", "0 1 -1 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="temperature",
                    quantity_aliases=("T",),
                    dimension="0 0 0 1 0 0 0",
                    field="T",
                    unit="K",
                    evidence_field_dimensions=(("T", "0 0 0 1 0 0 0"),),
                ),
                QuantityContract(
                    quantity="kinematic_pressure",
                    quantity_aliases=("p",),
                    dimension="0 2 -2 0 0 0 0",
                    dimension_aliases=("L^2/T^2",),
                    field="p",
                    unit="m2/s2",
                    solver_compressibility="incompressible",
                    evidence_field_dimensions=(("p", "0 2 -2 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="pressure",
                    quantity_aliases=("p",),
                    dimension="1 -1 -2 0 0 0 0",
                    dimension_aliases=("M/(L*T^2)",),
                    field="p",
                    unit="Pa",
                    solver_compressibility="compressible",
                    evidence_field_dimensions=(("p", "1 -1 -2 0 0 0 0"),),
                ),
                QuantityContract(
                    quantity="density",
                    quantity_aliases=("rho",),
                    dimension="1 -3 0 0 0 0 0",
                    dimension_aliases=("M/L^3",),
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
                request_contracts=request_contracts_by_kind.get(kind, ()),
                quantity_contracts=quantity_contracts,
            )
        )
    return registry


__all__ = [
    "ObservationExtensionDescriptor",
    "ObservationExtensionRegistry",
    "ObservationRegistryError",
    "ObservationRequestContract",
    "QuantityContract",
    "UnsupportedObservationError",
    "first_party_observation_registry",
]
