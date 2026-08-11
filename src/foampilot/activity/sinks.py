"""Activity event sinks for durable JSONL and human-readable stderr."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TextIO

from .models import ActivityEvent


class JsonlActivitySink:
    """Append complete, fsynced activity records to one controlled file."""

    def __init__(self, path: str | Path) -> None:
        candidate = Path(path)
        if candidate.is_symlink():
            raise ValueError("activity JSONL path cannot be a symbolic link")
        self.path = candidate

    def __call__(self, event: ActivityEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("activity JSONL path cannot be a symbolic link")
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class PlainActivitySink:
    """Render bounded status without expanding event metrics or payloads."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def __call__(self, event: ActivityEvent) -> None:
        context = "/".join(
            part
            for part in (event.source.value, event.stage, event.step_id)
            if part
        )
        elapsed = f"{event.elapsed_seconds:.1f}s"
        message = f" {event.message}" if event.message else ""
        self.stream.write(
            f"[{event.occurred_at.isoformat()}] {context or 'activity'} "
            f"{event.state.value} elapsed={elapsed}{message}\n"
        )
        self.stream.flush()


__all__ = ["JsonlActivitySink", "PlainActivitySink"]
