"""Bounded geometry probing before model routing and case authoring."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re

import pyvista as pv

from foampilot.tasks import TaskSpec

from .models import BoundingBox, GeometryFacts, PatchRoleMatch


_UNIT_TO_METRES = {
    "m": 1.0,
    "cm": 1.0e-2,
    "mm": 1.0e-3,
    "um": 1.0e-6,
    "in": 0.0254,
}


class GeometryProbeError(ValueError):
    """Stable task-domain failure raised before model generation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _surface_names(path: Path, format_name: str) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if format_name == "stl":
        values = re.findall(r"(?im)^\s*solid\s+([^\r\n]+?)\s*$", text)
    elif format_name == "obj":
        values = re.findall(r"(?im)^\s*(?:o|g)\s+([^\r\n]+?)\s*$", text)
    else:
        values = []
    normalized = tuple(sorted({item.strip() for item in values if item.strip()}))
    return normalized or (path.stem,)


def _read_surface(path: Path) -> pv.PolyData:
    try:
        data = pv.read(path)
    except Exception as error:
        raise GeometryProbeError(
            "GEOMETRY_ASSET_INVALID",
            f"无法读取公开几何资产 {path.name}: {error}",
        ) from error
    if isinstance(data, pv.MultiBlock):
        data = data.combine().extract_surface()
    elif not isinstance(data, pv.PolyData):
        data = data.extract_surface()
    if data.n_points < 1 or data.n_cells < 1:
        raise GeometryProbeError(
            "GEOMETRY_ASSET_INVALID",
            f"公开几何资产为空: {path.name}",
        )
    return data.clean()


def _edge_counts(surface: pv.PolyData) -> tuple[int, int]:
    boundary = surface.extract_feature_edges(
        boundary_edges=True,
        non_manifold_edges=False,
        feature_edges=False,
        manifold_edges=False,
    )
    non_manifold = surface.extract_feature_edges(
        boundary_edges=False,
        non_manifold_edges=True,
        feature_edges=False,
        manifold_edges=False,
    )
    return boundary.n_cells, non_manifold.n_cells


def _dimensionality(bounds: tuple[float, ...]) -> str:
    extents = (
        bounds[1] - bounds[0],
        bounds[3] - bounds[2],
        bounds[5] - bounds[4],
    )
    largest = max(extents)
    if largest <= 0:
        return "degenerate"
    small = sum(item <= largest * 1.0e-10 for item in extents)
    return "two_d" if small == 1 else ("degenerate" if small > 1 else "three_d")


def _resolve_asset(root: Path, relative: str) -> Path:
    source = root / relative
    if source.is_symlink() or not source.is_file():
        raise GeometryProbeError(
            "GEOMETRY_ASSET_INVALID",
            f"公开几何资产不存在或不是普通文件: {relative}",
        )
    resolved = source.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise GeometryProbeError(
            "GEOMETRY_ASSET_INVALID",
            f"公开几何资产逃逸出资产根目录: {relative}",
        ) from error
    return resolved


def probe_geometry(
    task: TaskSpec,
    asset_root: Path,
) -> GeometryFacts | None:
    """Probe declared public geometry without running model-authored commands."""

    geometry = task.geometry
    if geometry is None:
        return None
    root = Path(asset_root).resolve()
    expected_hashes = {asset.path: asset.sha256 for asset in task.public_assets}
    scale = _UNIT_TO_METRES[geometry.length_unit]

    if geometry.mode == "parametric":
        matches = tuple(
            PatchRoleMatch(
                name=item.name,
                role=item.role,
                matched=True,
                evidence="TaskSpec parametric patch declaration",
            )
            for item in geometry.patch_roles
        )
        return GeometryFacts(
            mode=geometry.mode,
            source_hashes={},
            declared_length_unit=geometry.length_unit,
            bounding_box_m=None,
            point_count=None,
            face_count=None,
            surface_names=tuple(item.name for item in geometry.patch_roles),
            region_names=tuple(item.name for item in geometry.region_roles),
            closed_surface=None,
            manifold_status="not_observed",
            dimensionality_observation="not_observed",
            patch_role_matches=matches,
            topology_observations=("parametric geometry has no file topology",),
            warnings=("parametric bounds are not inferred from parameter names",),
        )

    surfaces: list[pv.PolyData] = []
    names: set[str] = set()
    source_hashes: dict[str, str] = {}
    warnings: list[str] = []
    for asset in geometry.assets:
        source = _resolve_asset(root, asset.path)
        digest = _digest(source)
        if digest != expected_hashes.get(asset.path):
            raise GeometryProbeError(
                "GEOMETRY_ASSET_INVALID",
                f"公开几何资产 SHA256 不匹配: {asset.path}",
            )
        source_hashes[asset.path] = digest
        if asset.format not in {"stl", "obj", "msh"}:
            warnings.append(
                f"{asset.path} only received metadata probing for format {asset.format}"
            )
            continue
        surface = _read_surface(source)
        surfaces.append(surface)
        names.update(_surface_names(source, asset.format))

    if not surfaces:
        return GeometryFacts(
            mode=geometry.mode,
            source_hashes=source_hashes,
            declared_length_unit=geometry.length_unit,
            bounding_box_m=None,
            point_count=None,
            face_count=None,
            surface_names=tuple(sorted(names)),
            region_names=tuple(item.name for item in geometry.region_roles),
            closed_surface=None,
            manifold_status="not_observed",
            dimensionality_observation="not_observed",
            patch_role_matches=(),
            topology_observations=("no readable surface topology",),
            warnings=tuple(warnings),
        )

    bounds = (
        min(surface.bounds[0] for surface in surfaces),
        max(surface.bounds[1] for surface in surfaces),
        min(surface.bounds[2] for surface in surfaces),
        max(surface.bounds[3] for surface in surfaces),
        min(surface.bounds[4] for surface in surfaces),
        max(surface.bounds[5] for surface in surfaces),
    )
    boundary_edges = 0
    non_manifold_edges = 0
    for surface in surfaces:
        boundary, non_manifold = _edge_counts(surface)
        boundary_edges += boundary
        non_manifold_edges += non_manifold
    closed = boundary_edges == 0 and non_manifold_edges == 0
    manifold_status = (
        "non_manifold"
        if non_manifold_edges
        else ("closed_manifold" if closed else "open_manifold")
    )
    if boundary_edges:
        warnings.append("surface has open edges")
    if non_manifold_edges:
        warnings.append("surface has non-manifold edges")

    matches = tuple(
        PatchRoleMatch(
            name=item.name,
            role=item.role,
            matched=item.name in names,
            evidence=(
                "exact public surface name"
                if item.name in names
                else "no exact public surface name"
            ),
        )
        for item in geometry.patch_roles
    )
    unresolved = [item.name for item in matches if not item.matched]
    if unresolved:
        raise GeometryProbeError(
            "PATCH_MAPPING_UNRESOLVED",
            "无法把公开 patch role 映射到 surface: " + ", ".join(unresolved),
        )

    return GeometryFacts(
        mode=geometry.mode,
        source_hashes=source_hashes,
        declared_length_unit=geometry.length_unit,
        bounding_box_m=BoundingBox(
            minimum=(bounds[0] * scale, bounds[2] * scale, bounds[4] * scale),
            maximum=(bounds[1] * scale, bounds[3] * scale, bounds[5] * scale),
        ),
        point_count=sum(surface.n_points for surface in surfaces),
        face_count=sum(surface.n_cells for surface in surfaces),
        surface_names=tuple(sorted(names)),
        region_names=tuple(item.name for item in geometry.region_roles),
        closed_surface=closed,
        manifold_status=manifold_status,
        dimensionality_observation=_dimensionality(bounds),
        patch_role_matches=matches,
        topology_observations=(
            f"boundary_edges={boundary_edges}",
            f"non_manifold_edges={non_manifold_edges}",
        ),
        warnings=tuple(dict.fromkeys(warnings)),
    )
