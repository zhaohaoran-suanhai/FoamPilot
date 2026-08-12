from __future__ import annotations

import pytest

from foampilot.observations import (
    ObservationExtensionRegistry,
    ObservationRegistryError,
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


def test_unknown_observation_kind_is_rejected() -> None:
    with pytest.raises(UnsupportedObservationError, match="OBSERVATION_UNSUPPORTED"):
        first_party_observation_registry().resolve("arbitrary_model_script")


def test_entry_point_discovery_is_disabled() -> None:
    with pytest.raises(ObservationRegistryError, match="ENTRY_POINTS_DISABLED"):
        ObservationExtensionRegistry(entry_points_enabled=True)

