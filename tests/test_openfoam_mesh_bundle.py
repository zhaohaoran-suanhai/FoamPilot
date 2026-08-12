from __future__ import annotations

import gzip
from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from foampilot.assets import (
    AssetBundleError,
    BundleMember,
    OpenFOAMPolyMeshAdapter,
    compute_bundle_manifest_sha256,
)
from foampilot.extensions import CapabilityRegistry
from foampilot.cli.main import _declared_task_assets
from foampilot.tasks import OpenFOAMTarget, PublicAsset


FIXTURE = Path(__file__).parent / "fixtures/poly_mesh/minimal"


def _copy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "public"
    source = root / "mesh/native"
    shutil.copytree(FIXTURE, source)
    return root, source


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _declaration(
    root: Path,
    source: Path,
    *,
    install_path: str = "constant/polyMesh",
) -> PublicAsset:
    relative_source = source.relative_to(root).as_posix()
    members = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(source).as_posix()
            logical = relative[:-3] if relative.endswith(".gz") else relative
            members.append(
                BundleMember(
                    relative_path=relative,
                    logical_name=logical,
                    sha256=_digest(path),
                    bytes=path.stat().st_size,
                )
            )
    manifest = compute_bundle_manifest_sha256(
        adapter_id="foampilot.asset.openfoam-poly-mesh",
        kind="openfoam_poly_mesh",
        source_path=relative_source,
        install_path=install_path,
        region=(
            install_path.split("/")[1]
            if install_path.count("/") == 2
            else None
        ),
        members=members,
    )
    return PublicAsset(
        path=relative_source,
        sha256=manifest,
        purpose="provided native OpenFOAM mesh",
        kind="directory",
        install_path=install_path,
        bundle_manifest_sha256=manifest,
    )


def test_poly_mesh_bundle_preserves_optional_zones(tmp_path: Path) -> None:
    root, source = _copy_fixture(tmp_path)
    adapter = OpenFOAMPolyMeshAdapter()
    bundle = adapter.inspect(root, _declaration(root, source))

    staged = adapter.stage(bundle, root, tmp_path / "case")

    assert (staged.destination / "cellZones").is_file()
    assert (staged.destination / "faceZones").is_file()
    assert {item.logical_name for item in bundle.members} >= {
        "points",
        "faces",
        "owner",
        "neighbour",
        "boundary",
        "cellZones",
        "faceZones",
    }


def test_poly_mesh_bundle_preserves_nested_sets(tmp_path: Path) -> None:
    root, source = _copy_fixture(tmp_path)
    cell_set = source / "sets/selectedCells"
    cell_set.parent.mkdir()
    cell_set.write_text("1\n(0)\n", encoding="utf-8")
    adapter = OpenFOAMPolyMeshAdapter()
    bundle = adapter.inspect(root, _declaration(root, source))

    staged = adapter.stage(bundle, root, tmp_path / "case")

    assert (staged.destination / "sets/selectedCells").read_text(
        encoding="utf-8"
    ) == "1\n(0)\n"


def test_poly_mesh_bundle_rejects_plain_and_gzip_duplicate(
    tmp_path: Path,
) -> None:
    root, source = _copy_fixture(tmp_path)
    with gzip.open(source / "points.gz", "wb") as stream:
        stream.write((source / "points").read_bytes())

    with pytest.raises(AssetBundleError, match="ASSET_BUNDLE_AMBIGUOUS"):
        OpenFOAMPolyMeshAdapter().inspect(root, _declaration(root, source))


def test_poly_mesh_bundle_accepts_gzip_required_member(tmp_path: Path) -> None:
    root, source = _copy_fixture(tmp_path)
    points = source / "points"
    with gzip.open(source / "points.gz", "wb") as stream:
        stream.write(points.read_bytes())
    points.unlink()
    adapter = OpenFOAMPolyMeshAdapter()

    bundle = adapter.inspect(root, _declaration(root, source))

    assert "points" in {item.logical_name for item in bundle.members}
    assert "points.gz" in {item.relative_path for item in bundle.members}


def test_poly_mesh_bundle_rejects_missing_required_member(
    tmp_path: Path,
) -> None:
    root, source = _copy_fixture(tmp_path)
    (source / "neighbour").unlink()

    with pytest.raises(AssetBundleError, match="ASSET_BUNDLE_INCOMPLETE"):
        OpenFOAMPolyMeshAdapter().inspect(root, _declaration(root, source))


def test_poly_mesh_bundle_rejects_symlink_member(tmp_path: Path) -> None:
    root, source = _copy_fixture(tmp_path)
    (source / "unsafe").symlink_to(source / "points")

    with pytest.raises(AssetBundleError, match="ASSET_BUNDLE_UNSAFE"):
        OpenFOAMPolyMeshAdapter().inspect(
            root,
            PublicAsset(
                path="mesh/native",
                sha256="0" * 64,
                purpose="provided mesh",
                kind="directory",
                install_path="constant/polyMesh",
                bundle_manifest_sha256="0" * 64,
            ),
        )


def test_poly_mesh_bundle_detects_mutation_before_atomic_stage(
    tmp_path: Path,
) -> None:
    root, source = _copy_fixture(tmp_path)
    adapter = OpenFOAMPolyMeshAdapter()
    bundle = adapter.inspect(root, _declaration(root, source))
    (source / "points").write_text("changed\n", encoding="utf-8")
    case = tmp_path / "case"

    with pytest.raises(AssetBundleError, match="ASSET_BUNDLE_HASH_MISMATCH"):
        adapter.stage(bundle, root, case)

    assert not (case / "constant/polyMesh").exists()


def test_named_region_has_a_distinct_install_identity(tmp_path: Path) -> None:
    root, source = _copy_fixture(tmp_path)
    adapter = OpenFOAMPolyMeshAdapter()
    declaration = _declaration(
        root,
        source,
        install_path="constant/fluid/polyMesh",
    )

    bundle = adapter.inspect(root, declaration)
    staged = adapter.stage(bundle, root, tmp_path / "case")

    assert bundle.region == "fluid"
    assert staged.destination == tmp_path / "case/constant/fluid/polyMesh"


def test_existing_install_target_is_never_merged(tmp_path: Path) -> None:
    root, source = _copy_fixture(tmp_path)
    adapter = OpenFOAMPolyMeshAdapter()
    bundle = adapter.inspect(root, _declaration(root, source))
    destination = tmp_path / "case/constant/polyMesh"
    destination.mkdir(parents=True)
    (destination / "user-owned").write_text("keep\n", encoding="utf-8")

    with pytest.raises(AssetBundleError, match="ASSET_BUNDLE_TARGET_EXISTS"):
        adapter.stage(bundle, root, tmp_path / "case")

    assert (destination / "user-owned").read_text(encoding="utf-8") == "keep\n"


def test_first_party_registry_exposes_openfoam_mesh_adapter() -> None:
    registry = CapabilityRegistry.first_party()

    provider = registry.resolve(
        "asset:openfoam_poly_mesh",
        OpenFOAMTarget(distribution="foundation", version="10"),
    )

    assert isinstance(provider, OpenFOAMPolyMeshAdapter)
    assert registry.entry_points_enabled is False


def test_cli_declaration_and_adapter_share_gzip_manifest_logic(
    tmp_path: Path,
) -> None:
    root, source = _copy_fixture(tmp_path)
    points = source / "points"
    with gzip.open(source / "points.gz", "wb") as stream:
        stream.write(points.read_bytes())
    points.unlink()
    request = root / "request.md"
    request.write_text("use mesh", encoding="utf-8")

    declaration = _declared_task_assets(
        request,
        [],
        root,
        directory_paths=[Path("mesh/native")],
        install_paths=[Path("constant/polyMesh")],
    )[0]
    bundle = OpenFOAMPolyMeshAdapter().inspect(root, declaration)

    assert declaration.sha256 == bundle.manifest_sha256
