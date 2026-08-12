"""Public preprocessing contracts and probes."""

from .geometry_probe import GeometryProbeError, probe_geometry
from .mesh_quality import build_mesh_quality_report
from .models import (
    BoundingBox,
    GeometryFacts,
    InputMeshFacts,
    MeshPatchFact,
    MeshQualityReport,
    MeshZoneFact,
    PatchRoleMatch,
)
from .poly_mesh import PolyMeshInspectionError, inspect_poly_mesh

__all__ = [
    "BoundingBox",
    "GeometryFacts",
    "GeometryProbeError",
    "InputMeshFacts",
    "MeshPatchFact",
    "MeshQualityReport",
    "PatchRoleMatch",
    "MeshZoneFact",
    "PolyMeshInspectionError",
    "build_mesh_quality_report",
    "probe_geometry",
    "inspect_poly_mesh",
]
