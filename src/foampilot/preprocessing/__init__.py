"""Public preprocessing contracts and probes."""

from .geometry_probe import GeometryProbeError, probe_geometry
from .mesh_quality import build_mesh_quality_report
from .models import (
    BoundingBox,
    GeometryFacts,
    MeshQualityReport,
    PatchRoleMatch,
)

__all__ = [
    "BoundingBox",
    "GeometryFacts",
    "GeometryProbeError",
    "MeshQualityReport",
    "PatchRoleMatch",
    "build_mesh_quality_report",
    "probe_geometry",
]
