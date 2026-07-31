"""Durable workflow events and checkpoints within one mutable run."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from pydantic import BaseModel

from .events import WorkflowEvent


def _json_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _json_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _json_payload(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_atomic_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WorkflowStore:
    def __init__(
        self,
        *,
        run_dir: str | Path,
        event_listener: Callable[[WorkflowEvent], None] | None = None,
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.events_path = self.run_dir / "workflow-events.jsonl"
        self.event_listener = event_listener
        self._last_sequence = self._read_last_sequence()

    @property
    def next_sequence(self) -> int:
        return self._last_sequence + 1

    def _read_last_sequence(self) -> int:
        if not self.events_path.is_file():
            return 0
        lines = self.events_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if not lines:
            return 0
        return WorkflowEvent.model_validate_json(lines[-1]).sequence

    def record(self, event: WorkflowEvent) -> None:
        if event.sequence != self._last_sequence + 1:
            raise ValueError("workflow event sequence must be contiguous")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._last_sequence = event.sequence
        if self.event_listener is not None:
            self.event_listener(event)

    def checkpoint(
        self,
        name: str,
        payload: BaseModel | dict[str, Any],
    ) -> Path:
        if (
            not name
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
        ):
            raise ValueError("checkpoint name must be one safe path segment")
        raw_payload = _json_payload(payload)
        payload_bytes = _canonical_bytes(raw_payload)
        envelope = {
            "schema_version": 1,
            "sha256": sha256(payload_bytes).hexdigest(),
            "payload": raw_payload,
        }
        path = self.run_dir / "checkpoints" / f"{name}.json"
        _write_atomic_exclusive(path, _canonical_bytes(envelope))
        return path

    def finish(self, summary: BaseModel | dict[str, Any]) -> Path:
        path = self.run_dir / "summary.json"
        _write_atomic_exclusive(path, _canonical_bytes(summary))
        return path
