"""Canonical hashing and exclusive artifact persistence."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from pydantic import BaseModel
import yaml


def _payload(value: BaseModel) -> object:
    return value.model_dump(mode="json")


def canonical_sha256(value: BaseModel) -> str:
    canonical = json.dumps(
        _payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def write_json_exclusive(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            _payload(value),
            stream,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        stream.write("\n")


def write_yaml_exclusive(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(
            _payload(value),
            stream,
            allow_unicode=True,
            sort_keys=True,
        )


__all__ = [
    "canonical_sha256",
    "write_json_exclusive",
    "write_yaml_exclusive",
]
