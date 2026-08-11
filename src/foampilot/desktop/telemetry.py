"""Qt-independent parsing of OpenFOAM residual histories for the desktop."""

from __future__ import annotations

import math
import re

from .viewmodels import ResidualSample


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


def parse_residual_series(
    text: str,
    *,
    attempt: int | None,
    source_log: str,
) -> tuple[ResidualSample, ...]:
    """Parse public residual samples and retain their nearest step marker."""

    simulation_time: float | None = None
    iteration: int | None = None
    samples: list[ResidualSample] = []
    for line in text.splitlines():
        step = _STEP.match(line)
        if step is not None:
            value = float(step.group(2))
            if step.group(1).lower() == "time":
                simulation_time = value
                iteration = None
            else:
                simulation_time = None
                iteration = int(value)
            continue
        match = _RESIDUAL.search(line)
        if match is None:
            continue
        initial = float(match.group(2))
        final = float(match.group(3))
        if (
            not math.isfinite(initial)
            or not math.isfinite(final)
            or initial <= 0
            or final <= 0
        ):
            continue
        samples.append(
            ResidualSample(
                attempt=attempt,
                source_log=source_log,
                sequence=len(samples) + 1,
                simulation_time=simulation_time,
                iteration=iteration,
                field=match.group(1),
                initial_residual=initial,
                final_residual=final,
                solver_iterations=int(match.group(4)),
            )
        )
    return tuple(samples)


__all__ = ["parse_residual_series"]
