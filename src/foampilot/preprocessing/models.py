"""Structured, public preprocessing observations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BoundingBox(StrictModel):
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]


class PatchRoleMatch(StrictModel):
    name: str
    role: str
    matched: bool
    evidence: str


class GeometryFacts(StrictModel):
    schema_version: Literal[1] = 1
    mode: str
    source_hashes: dict[str, str]
    declared_length_unit: str
    bounding_box_m: BoundingBox | None
    point_count: int | None
    face_count: int | None
    surface_names: tuple[str, ...]
    region_names: tuple[str, ...]
    closed_surface: bool | None
    manifold_status: Literal[
        "closed_manifold",
        "open_manifold",
        "non_manifold",
        "not_observed",
    ]
    dimensionality_observation: Literal[
        "two_d",
        "three_d",
        "degenerate",
        "not_observed",
    ]
    patch_role_matches: tuple[PatchRoleMatch, ...]
    topology_observations: tuple[str, ...]
    warnings: tuple[str, ...]


class MeshQualityReport(StrictModel):
    schema_version: Literal[1] = 1
    strategy: str
    commands_completed: tuple[str, ...]
    mesh_created: bool | None
    check_mesh_passed: bool | None
    cells: int | None = Field(default=None, ge=0)
    faces: int | None = Field(default=None, ge=0)
    points: int | None = Field(default=None, ge=0)
    regions: int | None = Field(default=None, ge=0)
    patches: tuple[str, ...]
    max_non_orthogonality: float | None = Field(default=None, ge=0)
    max_skewness: float | None = Field(default=None, ge=0)
    negative_volume_count: int | None = Field(default=None, ge=0)
    failed_requirements: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence_files: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failed_requirements
