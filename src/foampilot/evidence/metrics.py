"""Bounded, non-authoritative live solver metric storage."""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class MetricPoint(StrictFrozenModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    occurred_at: datetime
    attempt: int | None = Field(default=None, ge=1)
    step_id: str = Field(min_length=1)
    simulation_time: float | None = Field(default=None, ge=0)
    series: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    value: float


class MetricsProjection(StrictFrozenModel):
    schema_version: Literal[1] = 1
    points: tuple[MetricPoint, ...] = ()
    warnings: tuple[str, ...] = ()
    # Deliberately absent as an input and always None as a compatibility guard.
    workflow_state: None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "MetricsProjection":
        candidate = Path(path)
        if not candidate.is_file():
            return cls(warnings=("METRICS_FILE_MISSING",))
        points: list[MetricPoint] = []
        warnings: list[str] = []
        try:
            lines = candidate.read_text(
                encoding="utf-8", errors="strict"
            ).splitlines()
        except (OSError, UnicodeError):
            return cls(warnings=("METRICS_FILE_UNREADABLE",))
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                points.append(MetricPoint.model_validate_json(line))
            except ValueError:
                warnings.append(f"METRICS_LINE_INVALID:{line_number}")
        if any(
            right.sequence <= left.sequence
            for left, right in zip(points, points[1:])
        ):
            warnings.append("METRICS_SEQUENCE_INVALID")
            points = []
        return cls(
            points=tuple(points),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def recent(self, series: str, limit: int) -> tuple[MetricPoint, ...]:
        if limit < 1:
            return ()
        selected = tuple(point for point in self.points if point.series == series)
        return selected[-limit:]

    @property
    def series_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(point.series for point in self.points))


class MetricsWriter:
    """Keep bounded sampled series while preserving complete raw logs elsewhere."""

    def __init__(
        self,
        path: str | Path,
        *,
        sample_interval_seconds: float = 0.2,
        max_points_per_series: int = 500,
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError("metric sample interval must be positive")
        if max_points_per_series < 1:
            raise ValueError("metric point limit must be positive")
        candidate = Path(path)
        if candidate.is_symlink():
            raise ValueError("metrics path cannot be a symbolic link")
        self.path = candidate
        self.sample_interval_seconds = sample_interval_seconds
        self.max_points_per_series = max_points_per_series
        self._sequence = 0
        self._points: dict[str, list[MetricPoint]] = {}
        self._last_bucket: dict[str, int] = {}
        self._lock = RLock()

    @staticmethod
    def _bucket(value: datetime, interval: float) -> int:
        return math.floor(value.timestamp() / interval)

    def write(
        self,
        *,
        occurred_at: datetime,
        attempt: int | None,
        step_id: str,
        simulation_time: float | None,
        series: str,
        value: float,
    ) -> MetricPoint | None:
        if not math.isfinite(value):
            return None
        with self._lock:
            bucket = self._bucket(
                occurred_at,
                self.sample_interval_seconds,
            )
            if self._last_bucket.get(series) == bucket:
                return None
            self._last_bucket[series] = bucket
            self._sequence += 1
            point = MetricPoint(
                sequence=self._sequence,
                occurred_at=occurred_at,
                attempt=attempt,
                step_id=step_id,
                simulation_time=simulation_time,
                series=series,
                value=value,
            )
            values = self._points.setdefault(series, [])
            values.append(point)
            if len(values) > self.max_points_per_series:
                # Uniformly retain old history while always preserving newest.
                retained = values[::2]
                if retained[-1] is not values[-1]:
                    retained.append(values[-1])
                self._points[series] = retained[-self.max_points_per_series :]
            self._rewrite()
            return point

    def _rewrite(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("metrics path cannot be a symbolic link")
        points = sorted(
            (point for values in self._points.values() for point in values),
            key=lambda item: item.sequence,
        )
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            for point in points:
                stream.write(point.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


__all__ = ["MetricPoint", "MetricsProjection", "MetricsWriter"]
