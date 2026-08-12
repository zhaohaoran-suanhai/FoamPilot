"""Public preprocessing contracts and probes."""

from .geometry_probe import GeometryProbeError, probe_geometry
from .mesh_quality import build_mesh_quality_report
from .mesh_probe import probe_provided_mesh
from .models import (
    BoundingBox,
    ExecutedMeshFacts,
    GeometryFacts,
    InputMeshFacts,
    MeshCheckFact,
    MeshPatchFact,
    MeshQualityReport,
    MeshZoneFact,
    PatchRoleMatch,
)
from .poly_mesh import PolyMeshInspectionError, inspect_poly_mesh

__all__ = [
    "BoundingBox",
    "ExecutedMeshFacts",
    "GeometryFacts",
    "GeometryProbeError",
    "InputMeshFacts",
    "MeshCheckFact",
    "MeshPatchFact",
    "MeshQualityReport",
    "PatchRoleMatch",
    "MeshZoneFact",
    "PolyMeshInspectionError",
    "build_mesh_quality_report",
    "probe_geometry",
    "probe_provided_mesh",
    "inspect_poly_mesh",
]
