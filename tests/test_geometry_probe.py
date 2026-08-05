from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from foampilot.preprocessing import GeometryProbeError, probe_geometry
from foampilot.tasks import TaskSpec


FIXTURES = Path(__file__).resolve().parent / "fixtures/preprocessing"


def _task(
    asset_name: str,
    *,
    format_name: str,
    length_unit: str,
    patch_name: str = "bodySurface",
) -> TaskSpec:
    source = FIXTURES / asset_name
    return TaskSpec.model_validate(
        {
            "schema_version": 2,
            "task_id": "geometry-probe",
            "title": "Geometry probe",
            "prompt": "Probe a public geometry before meshing.",
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 1,
                "max_wall_seconds": 60,
                "max_mpi_ranks": 1,
                "memory_mib": 1024,
            },
            "required_outputs": ["geometry facts"],
            "acceptance_requirements": ["geometry is valid"],
            "public_checks": [
                {"name": "mesh", "kind": "mesh_ok", "parameters": {}}
            ],
            "public_assets": [
                {
                    "path": f"geometry/{asset_name}",
                    "sha256": sha256(source.read_bytes()).hexdigest(),
                    "purpose": "public geometry",
                }
            ],
            "protected_paths": ["/private/tutorials"],
            "geometry": {
                "mode": "surface",
                "dimensionality": "three_d",
                "description": "Synthetic public surface",
                "length_unit": length_unit,
                "assets": [
                    {
                        "path": f"geometry/{asset_name}",
                        "format": format_name,
                        "role": "closed_body_surface",
                    }
                ],
                "parameters": {},
                "patch_roles": [{"name": patch_name, "role": "wall"}],
                "region_roles": [{"name": "fluid", "role": "fluid"}],
            },
            "mesh": {"strategy": "snappyHexMesh"},
        }
    )


def _asset_root(tmp_path: Path, asset_name: str) -> Path:
    root = tmp_path / "assets"
    destination = root / "geometry" / asset_name
    destination.parent.mkdir(parents=True)
    destination.write_bytes((FIXTURES / asset_name).read_bytes())
    return root


def test_probe_converts_stl_bounds_to_metres_and_reports_topology(
    tmp_path: Path,
) -> None:
    task = _task("closed-tetra.stl", format_name="stl", length_unit="mm")

    facts = probe_geometry(task, _asset_root(tmp_path, "closed-tetra.stl"))

    assert facts is not None
    assert facts.bounding_box_m is not None
    assert facts.bounding_box_m.maximum == (1.0, 1.0, 1.0)
    assert facts.point_count == 4
    assert facts.face_count == 4
    assert facts.closed_surface is True
    assert facts.manifold_status == "closed_manifold"
    assert facts.surface_names == ("bodySurface",)
    assert facts.patch_role_matches[0].matched is True
    assert facts.source_hashes["geometry/closed-tetra.stl"] == (
        task.public_assets[0].sha256
    )


def test_probe_reads_obj_groups_and_detects_planar_open_surface(
    tmp_path: Path,
) -> None:
    task = _task("grouped-square.obj", format_name="obj", length_unit="cm")

    facts = probe_geometry(task, _asset_root(tmp_path, "grouped-square.obj"))

    assert facts is not None and facts.bounding_box_m is not None
    assert facts.bounding_box_m.maximum == (0.1, 0.2, 0.0)
    assert facts.surface_names == ("bodySurface",)
    assert facts.closed_surface is False
    assert facts.dimensionality_observation == "two_d"
    assert "surface has open edges" in facts.warnings


@pytest.mark.parametrize(
    ("asset_name", "mutate", "code"),
    [
        ("closed-tetra.stl", "missing", "GEOMETRY_ASSET_INVALID"),
        ("closed-tetra.stl", "hash", "GEOMETRY_ASSET_INVALID"),
        ("empty.stl", "none", "GEOMETRY_ASSET_INVALID"),
    ],
)
def test_probe_rejects_missing_changed_and_empty_geometry(
    tmp_path: Path,
    asset_name: str,
    mutate: str,
    code: str,
) -> None:
    task = _task(
        asset_name,
        format_name="stl",
        length_unit="m",
        patch_name=("emptySurface" if asset_name == "empty.stl" else "bodySurface"),
    )
    root = tmp_path / "assets"
    if mutate != "missing":
        root = _asset_root(tmp_path, asset_name)
    if mutate == "hash":
        (root / "geometry" / asset_name).write_text("changed", encoding="utf-8")

    with pytest.raises(GeometryProbeError) as captured:
        probe_geometry(task, root)

    assert captured.value.code == code


def test_probe_rejects_unresolved_patch_mapping(tmp_path: Path) -> None:
    task = _task(
        "closed-tetra.stl",
        format_name="stl",
        length_unit="mm",
        patch_name="missingSurface",
    )

    with pytest.raises(GeometryProbeError) as captured:
        probe_geometry(task, _asset_root(tmp_path, "closed-tetra.stl"))

    assert captured.value.code == "PATCH_MAPPING_UNRESOLVED"
