"""Qt-independent byte cursors for active run events and solver logs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from foampilot.runtime.telemetry import IncrementalOpenFOAMLogParser

from .viewmodels import ResidualSample


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


class ResidualLogCursor:
    """Incrementally parse one OpenFOAM log into a bounded sample history."""

    def __init__(
        self,
        path: str | Path,
        *,
        attempt: int | None,
        source_log: str,
        sample_limit: int = 5_000,
        initial_bytes_limit: int | None = None,
    ) -> None:
        if sample_limit < 1:
            raise ValueError("sample limit must be positive")
        self.lines = IncrementalLineCursor(
            path,
            initial_bytes_limit=initial_bytes_limit,
        )
        self.attempt = attempt
        self.source_log = source_log
        self.sample_limit = sample_limit
        self._parser = IncrementalOpenFOAMLogParser()
        self._sequence = 0
        self._samples: list[ResidualSample] = []
        self.truncated_initial_read = False

    @property
    def offset(self) -> int:
        return self.lines.offset

    @property
    def samples(self) -> tuple[ResidualSample, ...]:
        return tuple(self._samples)

    def read(self) -> tuple[ResidualSample, ...]:
        chunk = self.lines.read()
        self.truncated_initial_read = (
            self.truncated_initial_read or chunk.truncated_initial_read
        )
        if chunk.reset:
            self._parser = IncrementalOpenFOAMLogParser()
            self._sequence = 0
            self._samples.clear()
        added: list[ResidualSample] = []
        for _, line in chunk.lines:
            for metric in self._parser.feed(line + "\n"):
                self._sequence += 1
                added.append(
                    ResidualSample(
                        attempt=self.attempt,
                        source_log=self.source_log,
                        sequence=self._sequence,
                        simulation_time=metric.simulation_time,
                        iteration=metric.iteration,
                        field=metric.field,
                        initial_residual=metric.initial_residual,
                        final_residual=metric.final_residual,
                        solver_iterations=metric.solver_iterations,
                    )
                )
        self._samples.extend(added)
        if len(self._samples) > self.sample_limit:
            self._samples[:] = self._samples[-self.sample_limit :]
        return tuple(added)


__all__ = ["IncrementalLineCursor", "LineChunk", "ResidualLogCursor"]
