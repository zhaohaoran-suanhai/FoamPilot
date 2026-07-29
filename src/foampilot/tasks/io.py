"""Strict TaskSpec loading and hash-verified public-asset staging."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

import yaml

from .models import TaskSpec


def load_task_spec(path: str | Path) -> TaskSpec:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    return TaskSpec.model_validate(payload)


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def stage_public_assets(
    task: TaskSpec,
    source_root: str | Path,
    case_root: str | Path,
) -> list[Path]:
    source_directory = Path(source_root).resolve()
    destination_directory = Path(case_root).resolve()
    destination_directory.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []

    for asset in task.public_assets:
        source = (source_directory / asset.path).resolve()
        if not source.is_relative_to(source_directory) or not source.is_file():
            raise ValueError(f"public asset source is invalid: {asset.path}")
        observed = _digest(source)
        if observed != asset.sha256:
            raise ValueError(
                f"public asset SHA256 mismatch for {asset.path}: "
                f"expected {asset.sha256}, observed {observed}"
            )

        destination = (destination_directory / asset.path).resolve()
        if not destination.is_relative_to(destination_directory):
            raise ValueError(
                f"public asset destination escapes case: {asset.path}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as input_handle:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as output_handle:
                temporary = Path(output_handle.name)
                while chunk := input_handle.read(1024 * 1024):
                    output_handle.write(chunk)
                output_handle.flush()
                os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
        staged.append(destination)

    return staged
