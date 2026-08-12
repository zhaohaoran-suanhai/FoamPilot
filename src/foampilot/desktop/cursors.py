"""Qt-independent byte cursor for active workflow events."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, slots=True)
class LineChunk:
    lines: tuple[tuple[int, str], ...]
    bytes_read: int
    reset: bool
    truncated_initial_read: bool = False


class IncrementalLineCursor:
    """Return new complete UTF-8 lines and retain an incomplete byte tail."""

    def __init__(
        self,
        path: str | Path,
        *,
        initial_bytes_limit: int | None = None,
    ) -> None:
        if initial_bytes_limit is not None and initial_bytes_limit < 1:
            raise ValueError("initial bytes limit must be positive")
        self.path = Path(path)
        self.initial_bytes_limit = initial_bytes_limit
        self.offset = 0
        self._identity: tuple[int, int] | None = None
        self._tail = b""
        self._line_number = 0

    def read(self) -> LineChunk:
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        reset = (
            self._identity is not None
            and (identity != self._identity or stat.st_size < self.offset)
        )
        if reset:
            self.offset = 0
            self._tail = b""
            self._line_number = 0
        self._identity = identity
        truncated_initial_read = False
        if (
            self.offset == 0
            and self._line_number == 0
            and not self._tail
            and self.initial_bytes_limit is not None
            and stat.st_size > self.initial_bytes_limit
        ):
            self.offset = stat.st_size - self.initial_bytes_limit
            truncated_initial_read = True
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            if truncated_initial_read:
                stream.readline()
                self.offset = stream.tell()
            payload = stream.read()
        self.offset += len(payload)
        parts = (self._tail + payload).split(b"\n")
        self._tail = parts.pop()
        lines: list[tuple[int, str]] = []
        for raw in parts:
            self._line_number += 1
            lines.append(
                (
                    self._line_number,
                    raw.rstrip(b"\r").decode("utf-8", errors="replace"),
                )
            )
        return LineChunk(
            lines=tuple(lines),
            bytes_read=len(payload),
            reset=reset,
            truncated_initial_read=truncated_initial_read,
        )


__all__ = ["IncrementalLineCursor", "LineChunk"]
