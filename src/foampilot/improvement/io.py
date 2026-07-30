"""YAML input and exclusive output for learning candidates."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import LearningCandidate


def load_learning_candidate(path: str | Path) -> LearningCandidate:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"candidate root must be a mapping: {source}")
    return LearningCandidate.model_validate(payload)


def write_learning_candidate(
    path: str | Path,
    candidate: LearningCandidate,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(
            candidate.model_dump(mode="json"),
            stream,
            sort_keys=False,
        )
    return destination
