"""Strict public geometry and mesh-intent task contracts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


LengthUnit = Literal["m", "cm", "mm", "um", "in"]
GeometryMode = Literal["parametric", "surface", "gmsh", "openfoam_mesh"]
GeometryFormat = Literal["stl", "obj", "geo", "msh", "openfoam_mesh"]
MeshStrategy = Literal["auto", "blockMesh", "snappyHexMesh", "gmsh", "provided"]


def _safe_relative_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise ValueError(f"geometry asset path must be a safe relative path: {value!r}")
    return parsed.as_posix()


class GeometryAssetRef(StrictModel):
    path: str = Field(min_length=1)
    format: GeometryFormat
    role: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value)


class GeometryParameter(StrictModel):
    value: float
    unit: LengthUnit


class PatchRole(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    role: Literal[
        "inlet",
        "outlet",
        "wall",
        "opening",
        "symmetry",
        "empty",
        "interface",
        "other",
    ]


class RegionRole(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    role: Literal["fluid", "solid", "porous", "other"]


class GeometryInput(StrictModel):
    mode: GeometryMode
    dimensionality: Literal["two_d", "axisymmetric", "three_d"]
    description: str = Field(min_length=1)
    length_unit: LengthUnit | None = None
    assets: list[GeometryAssetRef] = Field(default_factory=list)
    parameters: dict[str, GeometryParameter] = Field(default_factory=dict)
    patch_roles: list[PatchRole] = Field(default_factory=list)
    region_roles: list[RegionRole] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("geometry description must not be blank")
        return normalized

    @field_validator("parameters")
    @classmethod
    def validate_parameter_names(
        cls,
        value: dict[str, GeometryParameter],
    ) -> dict[str, GeometryParameter]:
        for name in value:
            if not name or not name.replace("_", "a").replace("-", "a").isalnum():
                raise ValueError(f"invalid geometry parameter name: {name!r}")
        return value

    @model_validator(mode="after")
    def validate_mode_and_roles(self) -> Self:
        if self.length_unit is None:
            raise ValueError("geometry length_unit must be declared")
        asset_paths = [asset.path for asset in self.assets]
        if len(asset_paths) != len(set(asset_paths)):
            raise ValueError("duplicate geometry asset paths are not allowed")
        patch_names = [item.name for item in self.patch_roles]
        if len(patch_names) != len(set(patch_names)):
            raise ValueError("duplicate patch role names are not allowed")
        region_names = [item.name for item in self.region_roles]
        if len(region_names) != len(set(region_names)):
            raise ValueError("duplicate region role names are not allowed")

        formats = {asset.format for asset in self.assets}
        required_formats = {
            "surface": {"stl", "obj"},
            "gmsh": {"geo", "msh"},
            "openfoam_mesh": {"openfoam_mesh"},
        }
        expected = required_formats.get(self.mode)
        if expected is not None and not formats.intersection(expected):
            raise ValueError(
                f"geometry mode {self.mode!r} requires an asset format in "
                f"{sorted(expected)}"
            )
        if self.mode == "parametric" and not self.parameters:
            raise ValueError("parametric geometry requires at least one parameter")
        return self


class CellCountIntent(StrictModel):
    min: int = Field(ge=1)
    max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.min > self.max:
            raise ValueError("target cell-count min must not exceed max")
        return self


class RefinementRegionIntent(StrictModel):
    role: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    level: int = Field(ge=0, le=12)


class BoundaryLayerIntent(StrictModel):
    enabled: bool = False
    patches: list[str] = Field(default_factory=list)
    layer_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_enabled_contract(self) -> Self:
        if len(self.patches) != len(set(self.patches)):
            raise ValueError("duplicate boundary-layer patches are not allowed")
        if self.enabled and (not self.patches or self.layer_count is None):
            raise ValueError(
                "enabled boundary layers require patches and layer_count"
            )
        return self


class MeshQualityIntent(StrictModel):
    require_check_mesh_pass: bool = True
    max_non_orthogonality: float | None = Field(default=None, ge=0, le=180)
    max_skewness: float | None = Field(default=None, ge=0)


class MeshIntent(StrictModel):
    strategy: MeshStrategy = "auto"
    target_cell_size: float | None = Field(default=None, gt=0)
    target_cell_count: CellCountIntent | None = None
    refinement_regions: list[RefinementRegionIntent] = Field(default_factory=list)
    boundary_layers: BoundaryLayerIntent = Field(
        default_factory=BoundaryLayerIntent
    )
    quality: MeshQualityIntent = Field(default_factory=MeshQualityIntent)

    @model_validator(mode="after")
    def validate_unique_refinement_roles(self) -> Self:
        roles = [item.role for item in self.refinement_regions]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate refinement-region roles are not allowed")
        return self
