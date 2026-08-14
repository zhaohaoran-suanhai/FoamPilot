from __future__ import annotations

import pytest

from foampilot.observations import (
    ObservationExtensionDescriptor,
    ObservationExtensionRegistry,
    ObservationRegistryError,
    ObservationRequestContract,
    UnsupportedObservationError,
    first_party_observation_registry,
)


def test_first_party_registry_is_closed_and_complete() -> None:
    registry = first_party_observation_registry()

    assert registry.ids() == (
        "continuity",
        "flow_rate",
        "force",
        "heat_flux",
        "pressure_difference",
        "region_average",
        "residual",
    )
    assert registry.entry_points_enabled is False
    assert registry.resolve("residual").supported_targets == (("foundation", "10"),)
    assert registry.resolve("force").strategies == ("unavailable",)
    assert registry.resolve("heat_flux").strategies == ("unavailable",)
    mass_flow = registry.resolve("flow_rate").resolve_quantity_contract(
        "mass_flow_rate",
        "1 0 -1 0 0 0 0",
    )
    assert mass_flow is not None
    assert mass_flow.field == "phi"
    assert mass_flow.unit == "kg/s"
    assert mass_flow.reduction == "magnitude"
    signed_flow = registry.resolve("flow_rate").resolve_quantity_contract(
        "signed_volumetric_flow_rate",
        "0 3 -1 0 0 0 0",
    )
    assert signed_flow is not None
    assert signed_flow.reduction == "identity"
    velocity = registry.resolve("region_average").resolve_quantity_contract(
        "velocity",
        "0 1 -1 0 0 0 0",
    )
    assert velocity is not None
    assert velocity.value_shape == "vector"
    assert velocity.reduction == "identity"


def test_unknown_observation_kind_is_rejected() -> None:
    with pytest.raises(UnsupportedObservationError, match="OBSERVATION_UNSUPPORTED"):
        first_party_observation_registry().resolve("arbitrary_model_script")


def test_entry_point_discovery_is_disabled() -> None:
    with pytest.raises(ObservationRegistryError, match="ENTRY_POINTS_DISABLED"):
        ObservationExtensionRegistry(entry_points_enabled=True)


def test_request_alias_is_bound_to_its_registered_kind_and_dimension() -> None:
    registry = first_party_observation_registry()

    assert registry.resolve("flow_rate").resolve_request_contract("U", "L/T") is None
    assert (
        registry.resolve("flow_rate").resolve_request_contract("Q", "L/T") is None
    )


def test_registry_rejects_ambiguous_request_alias_bindings() -> None:
    registry = ObservationExtensionRegistry()
    descriptor = ObservationExtensionDescriptor(
        kind="residual",
        supported_scope_kinds=("global",),
        strategies=("run_facts",),
        request_contracts=(
            ObservationRequestContract(
                quantity="solver_residual",
                dimension="1",
                quantity_aliases=("shared_alias",),
            ),
            ObservationRequestContract(
                quantity="normalized_residual",
                dimension="1",
                quantity_aliases=("shared_alias",),
            ),
        ),
    )

    with pytest.raises(
        ObservationRegistryError,
        match="OBSERVATION_REQUEST_ALIAS_AMBIGUOUS",
    ):
        registry.register(descriptor)
