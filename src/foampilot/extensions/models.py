"""Versioned capability descriptors for trusted FoamPilot extensions."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_EXTENSION_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY_KIND = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")
_EXECUTABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SupportedTarget(StrictFrozenModel):
    distribution: Literal["foundation"]
    versions: tuple[str, ...] = Field(min_length=1)

    @field_validator("versions")
    @classmethod
    def validate_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(value))
        if any(not item.isdigit() for item in normalized):
            raise ValueError("target versions must be numeric strings")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate target versions are not allowed")
        return normalized

    def supports(self, distribution: str, version: str) -> bool:
        return self.distribution == distribution and version in self.versions


class CapabilityDescriptor(StrictFrozenModel):
    extension_id: str
    extension_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    protocol_version: Literal[1] = 1
    capability_kinds: tuple[str, ...] = Field(min_length=1)
    supported_targets: tuple[SupportedTarget, ...] = Field(min_length=1)
    required_executables: tuple[str, ...] = ()
    input_contracts: tuple[str, ...] = ()
    output_contracts: tuple[str, ...] = ()
    compatible_extensions: tuple[str, ...] = ()
    incompatible_extensions: tuple[str, ...] = ()
    semantic_validators: tuple[str, ...] = ()
    evidence_extractors: tuple[str, ...] = ()

    @field_validator("extension_id")
    @classmethod
    def validate_extension_id(cls, value: str) -> str:
        if not _EXTENSION_ID.fullmatch(value):
            raise ValueError(f"invalid extension id: {value!r}")
        return value

    @field_validator("capability_kinds")
    @classmethod
    def validate_capability_kinds(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(sorted(value))
        if any(not _CAPABILITY_KIND.fullmatch(item) for item in normalized):
            raise ValueError("invalid capability kind")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate capability kinds are not allowed")
        return normalized

    @field_validator("required_executables")
    @classmethod
    def validate_executables(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(sorted(value))
        if any(not _EXECUTABLE.fullmatch(item) for item in normalized):
            raise ValueError("required executable name must be a basename")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate executable names are not allowed")
        return normalized

    @field_validator(
        "input_contracts",
        "output_contracts",
        "compatible_extensions",
        "incompatible_extensions",
        "semantic_validators",
        "evidence_extractors",
    )
    @classmethod
    def order_unique_strings(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(sorted(value))
        if any(not item.strip() for item in normalized):
            raise ValueError("descriptor entries must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate descriptor entries are not allowed")
        return normalized

    @model_validator(mode="after")
    def validate_compatibility(self) -> Self:
        overlap = set(self.compatible_extensions) & set(
            self.incompatible_extensions
        )
        if overlap:
            raise ValueError(
                "an extension cannot be both compatible and incompatible"
            )
        return self

    def supports_target(self, distribution: str, version: str) -> bool:
        return any(
            target.supports(distribution, version)
            for target in self.supported_targets
        )


__all__ = ["CapabilityDescriptor", "SupportedTarget"]
