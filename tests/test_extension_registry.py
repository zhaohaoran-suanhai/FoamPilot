from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from foampilot.extensions import (
    CapabilityDescriptor,
    CapabilityRegistrationError,
    CapabilityRegistry,
    CapabilityResolutionError,
    SupportedTarget,
)


def _descriptor(
    extension_id: str,
    *,
    kinds: tuple[str, ...] = ("asset:file",),
    versions: tuple[str, ...] = ("10",),
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        extension_id=extension_id,
        extension_version="1.0.0",
        protocol_version=1,
        capability_kinds=kinds,
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=versions),
        ),
    )


def _target(version: str = "10") -> SimpleNamespace:
    return SimpleNamespace(distribution="foundation", version=version)


def test_registry_rejects_duplicate_extension_id() -> None:
    registry = CapabilityRegistry()
    descriptor = _descriptor("foampilot.asset.file")
    registry.register(descriptor, object())

    with pytest.raises(
        CapabilityRegistrationError,
        match="DUPLICATE_EXTENSION_ID",
    ):
        registry.register(descriptor, object())


def test_registry_resolves_kind_and_target_deterministically() -> None:
    registry = CapabilityRegistry()
    provider = object()
    registry.register(_descriptor("foampilot.asset.file"), provider)

    assert registry.resolve("asset:file", _target()) is provider
    assert registry.descriptor("foampilot.asset.file").protocol_version == 1
    assert registry.extension_ids() == ("foampilot.asset.file",)


def test_registry_rejects_unsupported_target_and_ambiguous_kind() -> None:
    registry = CapabilityRegistry()
    registry.register(_descriptor("foampilot.asset.file-a"), object())

    with pytest.raises(
        CapabilityResolutionError,
        match="CAPABILITY_TARGET_UNSUPPORTED",
    ):
        registry.resolve("asset:file", _target("13"))

    registry.register(_descriptor("foampilot.asset.file-b"), object())
    with pytest.raises(
        CapabilityResolutionError,
        match="CAPABILITY_AMBIGUOUS",
    ):
        registry.resolve("asset:file", _target("10"))


def test_first_party_registry_does_not_load_entry_points() -> None:
    registry = CapabilityRegistry.first_party()

    assert registry.extension_ids() == (
        "foampilot.asset.openfoam-poly-mesh",
        "foampilot.asset.public-file",
    )
    assert registry.entry_points_enabled is False


def test_descriptor_rejects_conflicting_and_unsafe_declarations() -> None:
    with pytest.raises(ValidationError, match="both compatible and incompatible"):
        CapabilityDescriptor(
            extension_id="foampilot.asset.bad",
            extension_version="1.0.0",
            protocol_version=1,
            capability_kinds=("asset:file",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
            compatible_extensions=("foampilot.mesh.a",),
            incompatible_extensions=("foampilot.mesh.a",),
        )

    with pytest.raises(ValidationError, match="executable name"):
        CapabilityDescriptor(
            extension_id="foampilot.asset.bad-executable",
            extension_version="1.0.0",
            protocol_version=1,
            capability_kinds=("asset:file",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
            required_executables=("/tmp/checkMesh",),
        )

    with pytest.raises(ValidationError, match="safe and relative"):
        CapabilityDescriptor(
            extension_id="foampilot.asset.bad-author-path",
            extension_version="1.0.0",
            protocol_version=1,
            capability_kinds=("asset:file",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
            required_authored_paths=("../constant/secret",),
        )

    with pytest.raises(ValidationError, match="authoring rules"):
        CapabilityDescriptor(
            extension_id="foampilot.asset.bad-authoring-rule",
            extension_version="1.0.0",
            protocol_version=1,
            capability_kinds=("asset:file",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
            authoring_rules=("   ",),
        )
