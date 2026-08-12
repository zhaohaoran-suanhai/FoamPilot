"""Atomic materialization of an already verified compiled plan."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tempfile

from foampilot.plans import ExecutionPlan
from foampilot.tasks import TaskSpec


def _safe_relative(relative: str) -> bool:
    parsed = PurePosixPath(relative)
    return (
        bool(relative)
        and not parsed.is_absolute()
        and ".." not in parsed.parts
        and ".foampilot" not in parsed.parts
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def materialize_case(
    plan: ExecutionPlan,
    task: TaskSpec,
    case_root: str | Path,
) -> list[Path]:
    """Write one prevalidated model bundle without overwriting user assets."""

    root = Path(case_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    public_paths = {
        asset.install_path if asset.kind == "directory" else asset.path
        for asset in task.public_assets
    }
    existing = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    expected_existing = {
        path
        for path in existing
        if any(
            path == public_path or path.startswith(f"{public_path}/")
            for public_path in public_paths
            if public_path is not None
        )
    }
    unexpected = existing - expected_existing
    if unexpected:
        raise ValueError(
            "case directory is non-empty: " + ", ".join(sorted(unexpected))
        )

    seen: set[str] = set()
    for generated in plan.files:
        if not _safe_relative(generated.path):
            raise ValueError(
                f"generated file path must be safe relative: {generated.path}"
            )
        if generated.path in seen:
            raise ValueError(f"duplicate generated file: {generated.path}")
        if any(
            generated.path == public_path
            or generated.path.startswith(f"{public_path}/")
            for public_path in public_paths
            if public_path is not None
        ):
            raise ValueError(
                f"generated file overlaps public asset: {generated.path}"
            )
        if any(
            protected in generated.content
            for protected in task.protected_paths
        ):
            raise ValueError("generated file contains a protected path")
        seen.add(generated.path)

    written: list[Path] = []
    for generated in plan.files:
        target = (root / generated.path).resolve()
        if not target.is_relative_to(root):
            raise ValueError("generated target escapes case")
        _write_atomic(target, generated.content)
        written.append(target)
    return written
