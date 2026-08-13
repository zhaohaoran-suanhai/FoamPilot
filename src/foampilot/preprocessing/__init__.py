"""Public preprocessing contracts and probes."""

from .geometry_probe import GeometryProbeError, probe_geometry
from .mesh_quality import mesh_quality_from_run_facts
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
    PolyMeshTopologyFacts,
)
from .poly_mesh import (
    PolyMeshInspectionError,
    inspect_poly_mesh,
    inspect_poly_mesh_topology,
)

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
    "PolyMeshTopologyFacts",
    "MeshZoneFact",
    "PolyMeshInspectionError",
    "mesh_quality_from_run_facts",
    "probe_geometry",
    "probe_provided_mesh",
    "inspect_poly_mesh",
    "inspect_poly_mesh_topology",
]
