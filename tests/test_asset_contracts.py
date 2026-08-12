from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from foampilot.assets import (
    AssetBundle,
    BundleMember,
    StagedAsset,
    compute_bundle_manifest_sha256,
)


def _member(
    relative_path: str = "points",
    *,
    logical_name: str | None = None,
    digest: str = "a" * 64,
) -> BundleMember:
    return BundleMember(
        relative_path=relative_path,
        logical_name=(
            logical_name
            if logical_name is not None
            else (relative_path or "member")
        ),
        sha256=digest,
        bytes=12,
    )


def _bundle(*members: BundleMember) -> AssetBundle:
    values = {
        "adapter_id": "foampilot.asset.openfoam-poly-mesh",
        "kind": "openfoam_poly_mesh",
        "source_path": "mesh/openfoam/constant/polyMesh",
        "install_path": "constant/polyMesh",
        "region": None,
        "members": members,
    }
    return AssetBundle(
        **values,
        manifest_sha256=compute_bundle_manifest_sha256(**values),
    )


def test_asset_bundle_rejects_duplicate_member_paths() -> None:
    member = _member()

    with pytest.raises(ValidationError, match="duplicate member path"):
        _bundle(member, member)


def test_asset_bundle_rejects_duplicate_logical_names() -> None:
    with pytest.raises(ValidationError, match="duplicate logical name"):
        _bundle(
            _member("points", logical_name="mesh-points"),
            _member("points.gz", logical_name="mesh-points"),
        )


@pytest.mark.parametrize(
    "relative_path",
    ["../points", "/tmp/points", ".foampilot/secret", ""],
)
def test_bundle_member_rejects_unsafe_relative_paths(
    relative_path: str,
) -> None:
    with pytest.raises(ValidationError, match="safe relative path"):
        _member(relative_path)


def test_bundle_manifest_hash_is_canonical_across_member_order() -> None:
    points = _member("points", digest="a" * 64)
    faces = _member("faces", digest="b" * 64)

    first = _bundle(points, faces)
    second = _bundle(faces, points)

    assert first.manifest_sha256 == second.manifest_sha256
    assert [item.relative_path for item in first.members] == ["faces", "points"]
    assert first.model_dump_json() == second.model_dump_json()


def test_asset_bundle_rejects_a_noncanonical_manifest_hash() -> None:
    member = _member()

    with pytest.raises(ValidationError, match="manifest SHA256"):
        AssetBundle(
            adapter_id="foampilot.asset.openfoam-poly-mesh",
            kind="openfoam_poly_mesh",
            source_path="mesh/openfoam/constant/polyMesh",
            install_path="constant/polyMesh",
            region=None,
            members=(member,),
            manifest_sha256="0" * 64,
        )


def test_asset_contracts_are_frozen_and_forbid_extra_fields(
    tmp_path: Path,
) -> None:
    bundle = _bundle(_member())
    staged = StagedAsset(bundle=bundle, destination=tmp_path / "constant/polyMesh")

    with pytest.raises(ValidationError, match="frozen"):
        bundle.kind = "file"
    with pytest.raises(ValidationError, match="extra"):
        BundleMember.model_validate(
            {
                **_member().model_dump(mode="json"),
                "unexpected": True,
            }
        )
    assert staged.bundle.manifest_sha256 == bundle.manifest_sha256
