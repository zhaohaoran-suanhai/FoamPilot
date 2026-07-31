"""Thin, region-aware declarations for an Agent-authored native case."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseRegion(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: Literal["fluid", "solid", "electromagnetic", "generic"]
    path_prefix: str

    @field_validator("path_prefix")
    @classmethod
    def validate_path_prefix(cls, value: str) -> str:
        if value == "":
            return value
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("region path_prefix must be safe and relative")
        return path.as_posix()


class CaseField(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    region: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    path: str = Field(min_length=1)
    role: str = Field(min_length=1)
    created_by: Literal[
        "author",
        "public_asset",
        "mesh",
        "initialize",
        "solver",
    ]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("field path must be safe and relative")
        return path.as_posix()


class CasePatch(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    region: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    mesh_type: str = Field(min_length=1)


class CaseModels(StrictModel):
    turbulence: str | None = None
    transport: str | None = None
    thermophysical: str | None = None


class CaseManifest(StrictModel):
    solver_executable: str = Field(pattern=r"^[A-Za-z0-9_.+-]+$")
    solver_family: str = Field(min_length=1)
    regime: Literal["steady", "transient", "unknown"]
    physics_family: str = Field(min_length=1)
    mesh_family: str = Field(min_length=1)
    dimensionality: Literal["1d", "2d", "3d", "axisymmetric", "unknown"]
    regions: list[CaseRegion] = Field(min_length=1)
    fields: list[CaseField] = Field(default_factory=list)
    patches: list[CasePatch] = Field(default_factory=list)
    models: CaseModels = Field(default_factory=CaseModels)

    @model_validator(mode="after")
    def validate_region_scoped_identities(self) -> Self:
        region_names = [region.name for region in self.regions]
        if len(region_names) != len(set(region_names)):
            raise ValueError("manifest region names must be unique")
        path_prefixes = [region.path_prefix for region in self.regions]
        if len(path_prefixes) != len(set(path_prefixes)):
            raise ValueError("manifest region path_prefix values must be unique")
        known_regions = set(region_names)

        field_names: set[tuple[str, str]] = set()
        field_paths: set[tuple[str, str]] = set()
        for field in self.fields:
            if field.region not in known_regions:
                raise ValueError(
                    f"field {field.name} references unknown region "
                    f"{field.region}"
                )
            name_identity = (field.region, field.name)
            path_identity = (field.region, field.path)
            if name_identity in field_names or path_identity in field_paths:
                raise ValueError(
                    "manifest field identities must be unique by region"
                )
            field_names.add(name_identity)
            field_paths.add(path_identity)

        patch_names: set[tuple[str, str]] = set()
        for patch in self.patches:
            if patch.region not in known_regions:
                raise ValueError(
                    f"patch {patch.name} references unknown region "
                    f"{patch.region}"
                )
            identity = (patch.region, patch.name)
            if identity in patch_names:
                raise ValueError(
                    "manifest patch identities must be unique by region"
                )
            patch_names.add(identity)
        return self
