from __future__ import annotations

import gzip
import json
from pathlib import Path
import shutil

import pytest

from foampilot.assets import OpenFOAMPolyMeshAdapter
from foampilot.cli.main import _declared_task_assets
from foampilot.preprocessing import (
    PolyMeshInspectionError,
    inspect_poly_mesh,
    inspect_poly_mesh_topology,
)


FIXTURE = Path(__file__).parent / "fixtures/poly_mesh/minimal"


def _staged_bundle(tmp_path: Path, *, gzip_points: bool = False):
    public_root = tmp_path / "public"
    source = public_root / "mesh/native"
    shutil.copytree(FIXTURE, source)
    if gzip_points:
        points = source / "points"
        with gzip.open(source / "points.gz", "wb") as stream:
            stream.write(points.read_bytes())
        points.unlink()
    request = public_root / "request.md"
    request.write_text("use mesh", encoding="utf-8")
    declaration = _declared_task_assets(
        request,
        [],
        public_root,
        directory_paths=[Path("mesh/native")],
        install_paths=[Path("constant/polyMesh")],
    )[0]
    adapter = OpenFOAMPolyMeshAdapter()
    bundle = adapter.inspect(public_root, declaration)
    staged = adapter.stage(bundle, public_root, tmp_path / "case")
    return staged.destination, bundle


def _bundle_for_staged_root(mesh_root: Path):
    case_root = mesh_root.parents[1]
    request = case_root / "request.md"
    request.write_text("inspect mesh", encoding="utf-8")
    declaration = _declared_task_assets(
        request,
        [],
        case_root,
        directory_paths=[Path("constant/polyMesh")],
        install_paths=[Path("constant/polyMesh")],
    )[0]
    return OpenFOAMPolyMeshAdapter().inspect(case_root, declaration)


def test_inspector_reports_patch_and_zone_facts(tmp_path: Path) -> None:
    mesh_root, bundle = _staged_bundle(tmp_path)

    facts = inspect_poly_mesh(mesh_root, bundle, length_unit="m")

    assert facts.points == 12
    assert facts.faces == 11
    assert facts.internal_faces == 1
    assert facts.cells == 2
    assert facts.bounding_box_m.minimum == (0.0, 0.0, 0.0)
    assert facts.bounding_box_m.maximum == (2.0, 1.0, 0.1)
    assert [(item.name, item.patch_type, item.face_count) for item in facts.patches] == [
        ("inlet", "patch", 1),
        ("outlet", "patch", 1),
        ("top", "symmetryPlane", 2),
        ("bottom", "symmetryPlane", 2),
        ("frontAndBack", "empty", 4),
    ]
    assert [(item.name, item.element_count) for item in facts.cell_zones] == [
        ("zoneA", 1)
    ]
    assert [(item.name, item.element_count) for item in facts.face_zones] == [
        ("interfaceA", 1)
    ]
    assert facts.point_zones == ()
    assert "empty patch frontAndBack" in facts.dimensionality_observations
    assert "boundary face coverage is contiguous" in facts.topology_observations
    assert facts.raw_content_included is False
    assert len(facts.model_dump_json().encode("utf-8")) < 64 * 1024


def test_topology_inspector_does_not_claim_a_length_unit(tmp_path: Path) -> None:
    mesh_root, bundle = _staged_bundle(tmp_path)

    facts = inspect_poly_mesh_topology(mesh_root, bundle)

    assert facts.points == 12
    assert facts.faces == 11
    assert facts.cells == 2
    assert facts.unscaled_bounds.minimum == (0.0, 0.0, 0.0)
    assert facts.unscaled_bounds.maximum == (2.0, 1.0, 0.1)
    assert [(item.name, item.patch_type) for item in facts.patches] == [
        ("inlet", "patch"),
        ("outlet", "patch"),
        ("top", "symmetryPlane"),
        ("bottom", "symmetryPlane"),
        ("frontAndBack", "empty"),
    ]
    assert [(item.name, item.element_count) for item in facts.cell_zones] == [
        ("zoneA", 1)
    ]
    payload = facts.model_dump(mode="json")
    assert "declared_length_unit" not in payload
    assert "bounding_box_m" not in payload
    assert payload["raw_content_included"] is False


def test_gzip_and_plain_meshes_have_equivalent_physical_facts(
    tmp_path: Path,
) -> None:
    plain_root, plain_bundle = _staged_bundle(tmp_path / "plain")
    gzip_root, gzip_bundle = _staged_bundle(
        tmp_path / "gzip",
        gzip_points=True,
    )

    plain = inspect_poly_mesh(plain_root, plain_bundle, length_unit="m")
    compressed = inspect_poly_mesh(gzip_root, gzip_bundle, length_unit="m")

    ignored = {
        "bundle_manifest_sha256",
        "source_member_sha256",
    }
    assert plain.model_dump(exclude=ignored) == compressed.model_dump(
        exclude=ignored
    )


def test_inspector_converts_declared_units_to_metres(tmp_path: Path) -> None:
    mesh_root, bundle = _staged_bundle(tmp_path)

    facts = inspect_poly_mesh(mesh_root, bundle, length_unit="mm")

    assert facts.bounding_box_m.maximum == (0.002, 0.001, 0.0001)
    assert facts.declared_length_unit == "mm"


@pytest.mark.parametrize(
    ("member", "replacement", "code"),
    [
        ("points", "FoamFile { format binary; }\n0()\n", "POLYMESH_BINARY_UNSUPPORTED"),
        ("boundary", '#include "other"\n', "POLYMESH_DYNAMIC_INPUT_UNSUPPORTED"),
        ("owner", "$values\n", "POLYMESH_DYNAMIC_INPUT_UNSUPPORTED"),
        ("faces", "not-a-list\n", "POLYMESH_PARSE_FAILED"),
    ],
)
def test_inspector_rejects_unsupported_or_malformed_input(
    tmp_path: Path,
    member: str,
    replacement: str,
    code: str,
) -> None:
    mesh_root, bundle = _staged_bundle(tmp_path)
    (mesh_root / member).write_text(replacement, encoding="utf-8")
    bundle = _bundle_for_staged_root(mesh_root)

    with pytest.raises(PolyMeshInspectionError) as captured:
        inspect_poly_mesh(mesh_root, bundle, length_unit="m")

    assert captured.value.code == code


def test_inspector_rejects_owner_neighbour_and_boundary_inconsistency(
    tmp_path: Path,
) -> None:
    mesh_root, bundle = _staged_bundle(tmp_path)
    owner = (mesh_root / "owner").read_text(encoding="utf-8")
    (mesh_root / "owner").write_text(
        owner.replace("\n0\n1\n)\n", "\n0\n9\n)\n"),
        encoding="utf-8",
    )
    bundle = _bundle_for_staged_root(mesh_root)

    with pytest.raises(PolyMeshInspectionError) as captured:
        inspect_poly_mesh(mesh_root, bundle, length_unit="m")

    assert captured.value.code == "POLYMESH_TOPOLOGY_INVALID"


def test_serialized_facts_never_contain_raw_mesh_text(tmp_path: Path) -> None:
    mesh_root, bundle = _staged_bundle(tmp_path)

    payload = json.loads(
        inspect_poly_mesh(mesh_root, bundle, length_unit="m").model_dump_json()
    )

    serialized = json.dumps(payload)
    assert "FoamFile" not in serialized
    assert "4(1 4 10 7)" not in serialized
    assert payload["raw_content_included"] is False
