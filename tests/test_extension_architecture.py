from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.observations import (
    ObservationExtensionDescriptor,
    ObservationExtensionRegistry,
    ObservationRegistryError,
    first_party_observation_registry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src/foampilot"


def test_canonical_solve_has_no_legacy_public_validation_contracts() -> None:
    source = (SOURCE_ROOT / "agent/native_orchestrator.py").read_text(
        encoding="utf-8"
    )

    for token in (
        "PublicCheck",
        "PublicValidationReport",
        "validate_native_run",
        "public-validation.json",
        "PUBLIC_VALIDATION_PASS",
        "PUBLIC_VALIDATION_FAILED",
    ):
        assert token not in source


def test_legacy_validation_is_a_read_only_adapter_not_an_evaluator() -> None:
    validation_root = SOURCE_ROOT / "validation"

    assert {
        path.name for path in validation_root.glob("*.py")
    } == {"__init__.py", "legacy.py"}


def test_coordinator_has_no_first_party_observation_names() -> None:
    source = (SOURCE_ROOT / "workflow/coordinator.py").read_text(
        encoding="utf-8"
    )

    for token in first_party_observation_registry().ids():
        assert token not in source


def test_synthetic_first_party_descriptor_registers_without_coordinator_edit() -> None:
    before = (SOURCE_ROOT / "workflow/coordinator.py").read_bytes()
    registry = ObservationExtensionRegistry()
    registry.register(
        ObservationExtensionDescriptor(
            kind="residual",
            supported_scope_kinds=("global",),
            strategies=("run_facts",),
        )
    )

    assert registry.resolve("residual").strategies == ("run_facts",)
    assert (SOURCE_ROOT / "workflow/coordinator.py").read_bytes() == before


def test_third_party_entry_point_loading_stays_disabled() -> None:
    with pytest.raises(ObservationRegistryError, match="ENTRY_POINTS_DISABLED"):
        ObservationExtensionRegistry(entry_points_enabled=True)
