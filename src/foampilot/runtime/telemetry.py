"""Incremental parsing of public Foundation OpenFOAM solver metrics."""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
_STEP = re.compile(
    rf"^\s*(Time|Iteration)\s*(?:=|:)\s*({_NUMBER})\s*s?\s*$",
    re.IGNORECASE,
)
_RESIDUAL = re.compile(
    rf"Solving for\s+([^,\s]+),\s*"
    rf"Initial residual\s*=\s*({_NUMBER}),\s*"
    rf"Final residual\s*=\s*({_NUMBER}),\s*"
    rf"No Iterations\s+(\d+)",
    re.IGNORECASE,
)


class ResidualMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_time: float | None = None
    iteration: int | None = Field(default=None, ge=0)
    field: str = Field(min_length=1, max_length=128)
    initial_residual: float = Field(gt=0)
    final_residual: float = Field(gt=0)
    solver_iterations: int = Field(ge=0)

    def series_values(self) -> dict[str, float]:
        return {
            f"residual:{self.field}": self.initial_residual,
            f"residual-final:{self.field}": self.final_residual,
            f"solver-iterations:{self.field}": float(self.solver_iterations),
        }


class IncrementalOpenFOAMLogParser:
    """Retain step context and parse only complete newly supplied lines."""

    def __init__(self) -> None:
        self._buffer = ""
        self._simulation_time: float | None = None
        self._iteration: int | None = None

    def _line(self, line: str) -> ResidualMetric | None:
        step = _STEP.match(line.rstrip("\r"))
        if step is not None:
            value = float(step.group(2))
            if step.group(1).casefold() == "time":
                self._simulation_time = value
                self._iteration = None
            else:
                self._simulation_time = None
                self._iteration = int(value)
            return None
        match = _RESIDUAL.search(line)
        if match is None:
            return None
        initial = float(match.group(2))
        final = float(match.group(3))
        if (
            not math.isfinite(initial)
            or not math.isfinite(final)
            or initial <= 0
            or final <= 0
        ):
            return None
        return ResidualMetric(
            simulation_time=self._simulation_time,
            iteration=self._iteration,
            field=match.group(1),
            initial_residual=initial,
            final_residual=final,
            solver_iterations=int(match.group(4)),
        )

    def feed(self, text: str) -> tuple[ResidualMetric, ...]:
        combined = self._buffer + text
        lines = combined.split("\n")
        self._buffer = lines.pop()
        metrics = [metric for line in lines if (metric := self._line(line))]
        return tuple(metrics)

    def finish(self) -> tuple[ResidualMetric, ...]:
        if not self._buffer:
            return ()
        line = self._buffer
        self._buffer = ""
        metric = self._line(line)
        return () if metric is None else (metric,)


__all__ = ["IncrementalOpenFOAMLogParser", "ResidualMetric"]
