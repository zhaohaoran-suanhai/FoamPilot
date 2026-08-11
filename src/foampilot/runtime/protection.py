"""Merge machine-derived paths that model-authored content must not access."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from foampilot.environment.models import EnvironmentSnapshot


def runtime_protected_paths(
    declared: Sequence[str],
    environment: EnvironmentSnapshot,
    evaluator_roots: Sequence[Path] = (),
) -> tuple[Path, ...]:
    values = [Path(item).resolve() for item in declared]
    if environment.tutorial_root is not None:
        values.append(environment.tutorial_root.resolve())
    values.extend(path.resolve() for path in evaluator_roots)
    return tuple(dict.fromkeys(values))
