"""Qt-independent parsing of OpenFOAM residual histories for the desktop."""

from __future__ import annotations

from foampilot.evidence.telemetry import IncrementalOpenFOAMLogParser
from .viewmodels import ResidualSample


def parse_residual_series(
    text: str,
    *,
    attempt: int | None,
    source_log: str,
) -> tuple[ResidualSample, ...]:
    """Parse public residual samples and retain their nearest step marker."""

    parser = IncrementalOpenFOAMLogParser()
    metrics = [*parser.feed(text), *parser.finish()]
    return tuple(
        ResidualSample(
            attempt=attempt,
            source_log=source_log,
            sequence=sequence,
            simulation_time=metric.simulation_time,
            iteration=metric.iteration,
            field=metric.field,
            initial_residual=metric.initial_residual,
            final_residual=metric.final_residual,
            solver_iterations=metric.solver_iterations,
        )
        for sequence, metric in enumerate(metrics, start=1)
    )


__all__ = ["parse_residual_series"]
