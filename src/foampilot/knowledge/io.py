"""Knowledge-entry IO, corpus loading, and frozen manifests."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import yaml

from .models import KnowledgeEntry


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_knowledge_entry(path: str | Path) -> KnowledgeEntry:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"knowledge entry root must be a mapping: {source}")
    return KnowledgeEntry.model_validate(payload)


def _entry_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in root.rglob("*.yaml")
            if path.is_file()
        )
    )


def load_knowledge_corpus(root: str | Path) -> tuple[KnowledgeEntry, ...]:
    directory = Path(root)
    entries = tuple(load_knowledge_entry(path) for path in _entry_paths(directory))
    seen: dict[str, int] = {}
    for entry in entries:
        seen[entry.id] = seen.get(entry.id, 0) + 1
    duplicates = sorted(entry_id for entry_id, count in seen.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate knowledge ID: {', '.join(duplicates)}")
    return entries


def knowledge_entry_json_schema() -> dict[str, object]:
    return KnowledgeEntry.model_json_schema()


def build_knowledge_manifest(root: str | Path) -> dict[str, object]:
    directory = Path(root)
    files = {
        path.relative_to(directory).as_posix(): _hash(path)
        for path in _entry_paths(directory)
    }
    return {
        "schema_version": 1,
        "entry_count": len(files),
        "files": files,
    }


def verify_knowledge_manifest(
    root: str | Path,
    manifest: dict[str, object] | str | Path,
) -> list[str]:
    directory = Path(root)
    if isinstance(manifest, (str, Path)):
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
    else:
        payload = manifest
    expected = payload.get("files", {})
    if not isinstance(expected, dict):
        return ["manifest files must be a mapping"]
    actual = build_knowledge_manifest(directory)["files"]
    problems: list[str] = []
    for relative in sorted(set(expected) - set(actual)):
        problems.append(f"missing entry: {relative}")
    for relative in sorted(set(actual) - set(expected)):
        problems.append(f"unexpected entry: {relative}")
    for relative in sorted(set(actual) & set(expected)):
        if actual[relative] != expected[relative]:
            problems.append(f"hash mismatch: {relative}")
    return problems
