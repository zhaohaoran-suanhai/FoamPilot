"""Strict TaskSpec loading and hash-verified public-asset staging."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import os
import tempfile
from typing import TYPE_CHECKING

import yaml

from .models import TaskSpec


if TYPE_CHECKING:
    from foampilot.assets import AssetBundle, StagedAsset
    from foampilot.extensions import CapabilityRegistry


def load_task_spec(path: str | Path) -> TaskSpec:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    return TaskSpec.model_validate(payload)


def stage_public_assets(
    task: TaskSpec,
    source_root: str | Path,
    case_root: str | Path,
    *,
    registry: CapabilityRegistry | None = None,
) -> list[StagedAsset]:
    from foampilot.assets import AssetAdapter
    from foampilot.extensions import CapabilityRegistry

    source_directory = Path(source_root).resolve()
    destination_directory = Path(case_root).resolve()
    destination_directory.mkdir(parents=True, exist_ok=True)
    staged: list[StagedAsset] = []
    active_registry = registry or CapabilityRegistry.first_party()

    for asset in task.public_assets:
        if ".foampilot" in PurePosixPath(asset.path).parts:
            raise ValueError("public asset uses the reserved .foampilot namespace")
        kind = (
            "asset:openfoam_poly_mesh"
            if asset.kind == "directory"
            else "asset:public_file"
        )
        adapter = active_registry.resolve(kind, task.openfoam_target)
        if not isinstance(adapter, AssetAdapter):
            raise TypeError(f"resolved provider is not an asset adapter: {kind}")
        bundle = adapter.inspect(source_directory, asset)
        staged.append(
            adapter.stage(bundle, source_directory, destination_directory)
        )

    return staged


def inspect_public_assets(
    task: TaskSpec,
    source_root: str | Path,
    *,
    registry: CapabilityRegistry | None = None,
) -> list[AssetBundle]:
    """Validate every declaration and return its immutable bundle manifest."""

    from foampilot.assets import AssetAdapter
    from foampilot.extensions import CapabilityRegistry

    source_directory = Path(source_root).resolve()
    active_registry = registry or CapabilityRegistry.first_party()
    bundles = []
    for asset in task.public_assets:
        kind = (
            "asset:openfoam_poly_mesh"
            if asset.kind == "directory"
            else "asset:public_file"
        )
        adapter = active_registry.resolve(kind, task.openfoam_target)
        if not isinstance(adapter, AssetAdapter):
            raise TypeError(f"resolved provider is not an asset adapter: {kind}")
        bundles.append(adapter.inspect(source_directory, asset))
    return bundles


def snapshot_public_assets(
    task: TaskSpec,
    source_root: str | Path,
    snapshot_root: str | Path,
    *,
    registry: CapabilityRegistry | None = None,
) -> list[AssetBundle]:
    """Copy validated sources to an immutable source-layout run snapshot."""

    from foampilot.assets import AssetBundleError

    source_directory = Path(source_root).resolve()
    destination_root = Path(snapshot_root).resolve()
    if destination_root.exists():
        raise ValueError("public asset snapshot target already exists")
    bundles = inspect_public_assets(
        task,
        source_directory,
        registry=registry,
    )
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_root.name}.",
            dir=destination_root.parent,
        )
    )
    try:
        for bundle in bundles:
            source_base = source_directory / bundle.source_path
            destination_base = temporary / bundle.source_path
            if bundle.kind == "public_file":
                destination_base.parent.mkdir(parents=True, exist_ok=True)
                destination_base.write_bytes(source_base.read_bytes())
                member = bundle.members[0]
                if _digest_file(destination_base) != member.sha256:
                    raise AssetBundleError(
                        "ASSET_BUNDLE_HASH_MISMATCH",
                        f"public file changed during snapshot: {bundle.source_path}",
                    )
                continue
            destination_base.mkdir(parents=True, exist_ok=True)
            for member in bundle.members:
                source = source_base / member.relative_path
                target = destination_base / member.relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                if (
                    target.stat().st_size != member.bytes
                    or _digest_file(target) != member.sha256
                ):
                    raise AssetBundleError(
                        "ASSET_BUNDLE_HASH_MISMATCH",
                        "directory asset changed during snapshot: "
                        f"{bundle.source_path}/{member.relative_path}",
                    )
        os.replace(temporary, destination_root)
    except Exception:
        import shutil

        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return bundles


def _digest_file(path: Path) -> str:
    from hashlib import sha256

    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
