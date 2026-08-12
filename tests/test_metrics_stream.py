from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from foampilot.activity import ActivityReporter
from foampilot.evidence import MetricsProjection, MetricsWriter
from foampilot.evidence.telemetry import IncrementalOpenFOAMLogParser


def test_ten_thousand_residuals_do_not_flood_activity(
    tmp_path: Path,
) -> None:
    activity_path = tmp_path / "activity-events.jsonl"
    metrics_path = tmp_path / "metrics.jsonl"
    reporter = ActivityReporter(
        operation_id="op-metrics",
        utc_now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    reporter.bind_run(
        "run-metrics",
        activity_path,
        metrics_path=metrics_path,
        metrics_max_points_per_series=500,
    )
    parser = IncrementalOpenFOAMLogParser()

    for index in range(10_000):
        lines = (
            f"Time = {index * 0.01}\n"
            "PCG: Solving for p, Initial residual = 0.1, "
            "Final residual = 0.01, No Iterations 2\n"
        )
        for metric in parser.feed(lines):
            reporter.emit_solver_metric(
                metric=metric,
                elapsed_seconds=index * 0.01,
                attempt=1,
                stage="solve",
                step_id="solve",
                pid=123,
            )

    activity_lines = (
        activity_path.read_text(encoding="utf-8").splitlines()
        if activity_path.is_file()
        else []
    )
    projection = MetricsProjection.from_file(metrics_path)
    assert len(activity_lines) < 100
    assert len(projection.recent("residual:p", limit=10_000)) <= 500
    assert projection.recent("residual:p", limit=1)[0].value == 0.1


def test_metrics_projection_is_non_authoritative_on_corruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metrics.jsonl"
    path.write_text(
        '{"schema_version":1,"sequence":1,"occurred_at":"bad"}\n'
        "not-json\n",
        encoding="utf-8",
    )

    projection = MetricsProjection.from_file(path)

    assert projection.warnings
    assert projection.workflow_state is None
    assert projection.recent("residual:p", 10) == ()


def test_metrics_writer_downsamples_by_interval_and_caps_each_series(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metrics.jsonl"
    start = datetime(2026, 8, 13, tzinfo=timezone.utc)
    writer = MetricsWriter(
        path,
        sample_interval_seconds=1.0,
        max_points_per_series=3,
    )
    for index, seconds in enumerate((0.0, 0.2, 1.1, 2.2, 3.3), start=1):
        writer.write(
            occurred_at=start + timedelta(seconds=seconds),
            attempt=1,
            step_id="solve",
            simulation_time=seconds,
            series="residual:U",
            value=float(index),
        )
    writer.write(
        occurred_at=start + timedelta(seconds=3.4),
        attempt=1,
        step_id="solve",
        simulation_time=3.4,
        series="courant:max",
        value=0.5,
    )

    projection = MetricsProjection.from_file(path)
    residuals = projection.recent("residual:U", 10)
    assert len(residuals) <= 3
    assert tuple(point.sequence for point in residuals) == tuple(
        sorted(point.sequence for point in residuals)
    )
    assert projection.recent("courant:max", 1)[0].value == 0.5
    assert all(json.loads(line) for line in path.read_text().splitlines())
