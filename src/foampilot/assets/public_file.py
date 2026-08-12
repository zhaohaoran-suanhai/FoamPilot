"""Deterministic adapter for one hash-addressed public file."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import tempfile

from foampilot.extensions import CapabilityDescriptor, SupportedTarget
from foampilot.tasks import PublicAsset

from .models import (
    AssetBundle,
    BundleMember,
    StagedAsset,
    compute_bundle_manifest_sha256,
)
from .openfoam_mesh import AssetBundleError


def _digest(path: Path) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class PublicFileAdapter:
    descriptor = CapabilityDescriptor(
        extension_id="foampilot.asset.public-file",
        extension_version="1.0.0",
        protocol_version=1,
        capability_kinds=("asset:public_file",),
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
        if declaration.kind != "file":
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                "public-file adapter requires a file declaration",
            )
        source = root / declaration.path
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(root)
        ):
            raise AssetBundleError(
                "ASSET_BUNDLE_UNSAFE",
                f"public file is missing or unsafe: {declaration.path}",
            )
        observed, size = _digest(source)
        if observed != declaration.sha256:
            raise AssetBundleError(
                "ASSET_BUNDLE_HASH_MISMATCH",
                f"declared public file SHA256 does not match: {declaration.path}",
            )
        member = BundleMember(
            relative_path=Path(declaration.path).name,
            logical_name=Path(declaration.path).name,
            sha256=observed,
            bytes=size,
        )
        values = {
            "adapter_id": self.descriptor.extension_id,
            "kind": "public_file",
            "source_path": declaration.path,
            "install_path": declaration.path,
            "region": None,
            "members": (member,),
        }
        return AssetBundle(
            **values,
            manifest_sha256=compute_bundle_manifest_sha256(**values),
        )

    def stage(
        self,
        bundle: AssetBundle,
        source_root: Path,
        case_root: Path,
    ) -> StagedAsset:
        source = source_root.resolve() / bundle.source_path
        case = case_root.resolve()
        destination = case / bundle.install_path
        if destination.exists() or destination.is_symlink():
            raise AssetBundleError(
                "ASSET_BUNDLE_TARGET_EXISTS",
                f"asset destination already exists: {bundle.install_path}",
            )
        observed, size = _digest(source)
        member = bundle.members[0]
        if observed != member.sha256 or size != member.bytes:
            raise AssetBundleError(
                "ASSET_BUNDLE_HASH_MISMATCH",
                f"public file changed before staging: {bundle.source_path}",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as input_stream:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as output_stream:
                temporary = Path(output_stream.name)
                while chunk := input_stream.read(1024 * 1024):
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
        return StagedAsset(bundle=bundle, destination=destination)


__all__ = ["PublicFileAdapter"]
