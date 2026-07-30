"""Minimal structured parsing for Foundation OpenFOAM solver logs."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"


class EquationResidual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    initial: float
    final: float
    iterations: int


class ContinuitySample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local: float
    global_error: float
    cumulative: float


class OpenFOAMLogSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_time: float | None = None
    normal_end: bool = False
    fatal: bool = False
    segmentation_fault: bool = False
    non_finite: bool = False
    last_cumulative_continuity_error: float | None = None
    courant_maxima: list[float] = Field(default_factory=list)
    residuals: list[EquationResidual] = Field(default_factory=list)
    continuity: list[ContinuitySample] = Field(default_factory=list)

    @property
    def completed(self) -> bool:
        return (
            self.normal_end
            and not self.fatal
            and not self.segmentation_fault
            and not self.non_finite
        )


def parse_openfoam_log(text: str) -> OpenFOAMLogSummary:
    """Parse the public completion signals used by Stage 1."""

    times = re.findall(
        rf"(?m)^\s*(?:Time|Iteration)\s*(?:=|:)\s*"
        rf"({_NUMBER})\s*s?\s*$",
        text,
    )
    continuity_matches = re.findall(
        rf"continuity errors\s*:\s*sum local\s*=\s*({_NUMBER}),\s*"
        rf"global\s*=\s*({_NUMBER}),\s*cumulative\s*=\s*({_NUMBER})",
        text,
        re.IGNORECASE,
    )
    residual_matches = re.findall(
        rf"Solving for\s+([^,\s]+),\s*"
        rf"Initial residual\s*=\s*({_NUMBER}),\s*"
        rf"Final residual\s*=\s*({_NUMBER}),\s*"
        rf"No Iterations\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    courant_matches = re.findall(
        rf"Courant Number\s+mean\s*:\s*{_NUMBER}\s+max\s*:\s*"
        rf"({_NUMBER})",
        text,
        re.IGNORECASE,
    )
    courant_matches.extend(
        re.findall(
            rf"Mean and max Courant Numbers\s*=\s*{_NUMBER}\s+"
            rf"({_NUMBER})",
            text,
            re.IGNORECASE,
        )
    )
    return OpenFOAMLogSummary(
        latest_time=float(times[-1]) if times else None,
        normal_end=bool(re.search(r"(?m)^\s*End\s*$", text)),
        fatal=bool(re.search(r"FOAM\s+FATAL", text, re.IGNORECASE)),
        segmentation_fault=bool(
            re.search(r"segmentation fault|sigsegv", text, re.IGNORECASE)
        ),
        non_finite=bool(
            re.search(
                r"(?<![A-Za-z])(?:nan|[-+]?inf)(?![A-Za-z])",
                text,
                re.IGNORECASE,
            )
        ),
        last_cumulative_continuity_error=(
            float(continuity_matches[-1][2])
            if continuity_matches
            else None
        ),
        courant_maxima=[float(value) for value in courant_matches],
        residuals=[
            EquationResidual(
                field=field,
                initial=float(initial),
                final=float(final),
                iterations=int(iterations),
            )
            for field, initial, final, iterations in residual_matches
        ],
        continuity=[
            ContinuitySample(
                local=float(local),
                global_error=float(global_error),
                cumulative=float(cumulative),
            )
            for local, global_error, cumulative in continuity_matches
        ],
    )
