"""Immutable Foundation OpenFOAM polyMesh directory adapter."""

from __future__ import annotations

import errno
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from foampilot.extensions import CapabilityDescriptor, SupportedTarget
from foampilot.tasks import PublicAsset

from .models import (
    AssetBundle,
    BundleMember,
    StagedAsset,
    compute_bundle_manifest_sha256,
)


REQUIRED = ("points", "faces", "owner", "neighbour", "boundary")
OPTIONAL = ("cellZones", "faceZones", "pointZones")
MAX_BUNDLE_BYTES = 256 * 1024 * 1024


class AssetBundleError(ValueError):
    """Stable failure for asset inspection or staging."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _digest(path: Path) -> tuple[str, int]:
    value = sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
            size += len(chunk)
    return value.hexdigest(), size


def _region_from_install_path(install_path: str) -> str | None:
    parts = PurePosixPath(install_path).parts
    if parts == ("constant", "polyMesh"):
        return None
    if (
        len(parts) == 3
        and parts[0] == "constant"
        and parts[2] == "polyMesh"
    ):
        return parts[1]
    raise AssetBundleError(
        "ASSET_BUNDLE_UNSAFE",
        "polyMesh install path must be constant/polyMesh or "
        "constant/<region>/polyMesh",
    )


def _regular_member(source: Path, relative_path: str) -> Path | None:
    candidate = source / relative_path
    if not candidate.exists() and not candidate.is_symlink():
        return None
    if candidate.is_symlink() or not candidate.is_file():
        raise AssetBundleError(
            "ASSET_BUNDLE_UNSAFE",
            f"bundle member is not a regular file: {relative_path}",
        )
    return candidate


def _logical_member(source: Path, logical_name: str) -> tuple[str, Path] | None:
    plain = _regular_member(source, logical_name)
    compressed = _regular_member(source, f"{logical_name}.gz")
    if plain is not None and compressed is not None:
        raise AssetBundleError(
            "ASSET_BUNDLE_AMBIGUOUS",
            f"both {logical_name} and {logical_name}.gz exist",
        )
    if plain is not None:
        return logical_name, plain
    if compressed is not None:
        return f"{logical_name}.gz", compressed
    return None


class OpenFOAMPolyMeshAdapter:
    """Inspect and atomically stage one native OpenFOAM polyMesh."""

    descriptor = CapabilityDescriptor(
        extension_id="foampilot.asset.openfoam-poly-mesh",
        extension_version="1.0.0",
        protocol_version=1,
        capability_kinds=("asset:openfoam_poly_mesh",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        input_contracts=("foampilot.tasks.PublicAsset:2",),
        output_contracts=("foampilot.assets.AssetBundle:1",),
    )

    def inspect(
        self,
        source_root: Path,
        declaration: PublicAsset,
    ) -> AssetBundle:
        root = source_root.resolve()
        if declaration.kind != "directory":
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                "polyMesh adapter requires a directory asset declaration",
            )
        if declaration.install_path is None:
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                "polyMesh declaration has no install path",
            )
        source = root / declaration.path
        if (
            source.is_symlink()
            or not source.is_dir()
            or not source.resolve().is_relative_to(root)
        ):
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                f"polyMesh source is missing or unsafe: {declaration.path}",
            )
        region = _region_from_install_path(declaration.install_path)
        members: list[BundleMember] = []
        total_bytes = 0
        for logical_name in (*REQUIRED, *OPTIONAL):
            observed = _logical_member(source, logical_name)
            if observed is None:
                if logical_name in REQUIRED:
                    raise AssetBundleError(
                        "ASSET_BUNDLE_INCOMPLETE",
                        f"required polyMesh member is missing: {logical_name}",
                    )
                continue
            relative_path, path = observed
            digest, size = _digest(path)
            total_bytes += size
            members.append(
                BundleMember(
                    relative_path=relative_path,
                    logical_name=logical_name,
                    sha256=digest,
                    bytes=size,
                )
            )

        sets = source / "sets"
        if sets.exists() or sets.is_symlink():
            if sets.is_symlink() or not sets.is_dir():
                raise AssetBundleError(
                    "ASSET_BUNDLE_UNSAFE",
                    "polyMesh sets member must be a real directory",
                )
            for path in sorted(sets.rglob("*")):
                relative_path = path.relative_to(source).as_posix()
                if path.is_symlink():
                    raise AssetBundleError(
                        "ASSET_BUNDLE_UNSAFE",
                        f"bundle member is a symlink: {relative_path}",
                    )
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise AssetBundleError(
                        "ASSET_BUNDLE_UNSAFE",
                        f"bundle member is not regular: {relative_path}",
                    )
                digest, size = _digest(path)
                total_bytes += size
                members.append(
                    BundleMember(
                        relative_path=relative_path,
                        logical_name=relative_path,
                        sha256=digest,
                        bytes=size,
                    )
                )

        allowed_paths = {item.relative_path for item in members}
        for path in source.rglob("*"):
            relative_path = path.relative_to(source).as_posix()
            if path.is_symlink():
                raise AssetBundleError(
                    "ASSET_BUNDLE_UNSAFE",
                    f"bundle member is a symlink: {relative_path}",
                )
            if path.is_file() and relative_path not in allowed_paths:
                raise AssetBundleError(
                    "ASSET_BUNDLE_UNSAFE",
                    f"unexpected polyMesh member: {relative_path}",
                )
        if total_bytes > MAX_BUNDLE_BYTES:
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                "polyMesh bundle exceeds the 256 MiB size limit",
            )

        manifest_sha256 = compute_bundle_manifest_sha256(
            adapter_id=self.descriptor.extension_id,
            kind="openfoam_poly_mesh",
            source_path=declaration.path,
            install_path=declaration.install_path,
            region=region,
            members=members,
        )
        expected = declaration.bundle_manifest_sha256
        if declaration.sha256 != manifest_sha256 or expected != manifest_sha256:
            raise AssetBundleError(
                "ASSET_BUNDLE_HASH_MISMATCH",
                "declared polyMesh manifest does not match inspected members",
            )
        return AssetBundle(
            adapter_id=self.descriptor.extension_id,
            kind="openfoam_poly_mesh",
            source_path=declaration.path,
            install_path=declaration.install_path,
            region=region,
            members=tuple(members),
            manifest_sha256=manifest_sha256,
        )

    def stage(
        self,
        bundle: AssetBundle,
        source_root: Path,
        case_root: Path,
    ) -> StagedAsset:
        root = source_root.resolve()
        case = case_root.resolve()
        source = root / bundle.source_path
        destination = case / bundle.install_path
        if not source.resolve().is_relative_to(root):
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                "bundle source escapes the declared root",
            )
        if not destination.resolve().is_relative_to(case):
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                "bundle destination escapes the case",
            )
        if destination.exists() or destination.is_symlink():
            raise AssetBundleError(
                "ASSET_BUNDLE_TARGET_EXISTS",
                f"bundle destination already exists: {bundle.install_path}",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
        )
        try:
            for member in bundle.members:
                source_member = source / member.relative_path
                if source_member.is_symlink() or not source_member.is_file():
                    raise AssetBundleError(
                        "ASSET_BUNDLE_HASH_MISMATCH",
                        f"bundle member changed: {member.relative_path}",
                    )
                observed_digest, observed_size = _digest(source_member)
                if (
                    observed_digest != member.sha256
                    or observed_size != member.bytes
                ):
                    raise AssetBundleError(
                        "ASSET_BUNDLE_HASH_MISMATCH",
                        f"bundle member changed: {member.relative_path}",
                    )
                target = temporary / member.relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                with source_member.open("rb") as input_stream:
                    with target.open("xb") as output_stream:
                        while chunk := input_stream.read(1024 * 1024):
                            output_stream.write(chunk)
                        output_stream.flush()
                        os.fsync(output_stream.fileno())
            os.replace(temporary, destination)
        except OSError as error:
            shutil.rmtree(temporary, ignore_errors=True)
            if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise AssetBundleError(
                    "ASSET_BUNDLE_TARGET_EXISTS",
                    f"bundle destination already exists: {bundle.install_path}",
                ) from error
            raise
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return StagedAsset(bundle=bundle, destination=destination)


__all__ = [
    "AssetBundleError",
    "OpenFOAMPolyMeshAdapter",
]
