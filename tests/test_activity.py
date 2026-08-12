from __future__ import annotations

from datetime import datetime, timezone
import io
from threading import Event, Thread
import time

import pytest
from pydantic import ValidationError

from foampilot.activity import (
    ActivityEvent,
    ActivityReporter,
    JsonlActivitySink,
    JsonlStreamActivitySink,
    PlainActivitySink,
)
from foampilot.evidence import MetricsProjection


def test_activity_event_is_strict() -> None:
    with pytest.raises(ValidationError):
        ActivityEvent(
            sequence=1,
            operation_id="op-1",
            kind="stage",
            state="started",
            source="model",
            occurred_at=datetime.now(timezone.utc),
            unexpected=True,
        )


def test_activity_reporter_assigns_contiguous_sequence() -> None:
    seen: list[ActivityEvent] = []
    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[seen.append],
    )

    first = reporter.emit(
        kind="stage",
        state="started",
        source="model",
        stage="generation",
    )
    second = reporter.emit(
        kind="heartbeat",
        state="alive",
        source="model",
        elapsed_seconds=5.0,
    )

    assert [first.sequence, second.sequence] == [1, 2]
    assert [event.sequence for event in seen] == [1, 2]


def test_bind_run_persists_jsonl_without_resetting_sequence(tmp_path) -> None:
    reporter = ActivityReporter(operation_id="op-1")
    reporter.emit(
        kind="stage",
        state="started",
        source="workflow",
    )

    path = tmp_path / "activity-events.jsonl"
    reporter.bind_run("run-1", path)
    event = reporter.emit(
        kind="heartbeat",
        state="alive",
        source="runner",
    )

    stored = ActivityEvent.model_validate_json(
        path.read_text(encoding="utf-8").strip()
    )
    assert event.sequence == 2
    assert event.run_id == "run-1"
    assert stored == event


def test_jsonl_sink_appends_complete_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    sink = JsonlActivitySink(path)
    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[sink],
    )

    reporter.emit(kind="stage", state="started", source="workflow")
    reporter.emit(kind="stage", state="completed", source="workflow")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [ActivityEvent.model_validate_json(line).sequence for line in lines] == [
        1,
        2,
    ]


def test_jsonl_stream_sink_writes_one_event_per_line() -> None:
    stream = io.StringIO()
    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[JsonlStreamActivitySink(stream)],
    )

    event = reporter.emit(
        kind="heartbeat",
        state="alive",
        source="model",
    )

    assert ActivityEvent.model_validate_json(stream.getvalue().strip()) == event


def test_plain_sink_does_not_render_metrics_as_model_content() -> None:
    stream = io.StringIO()
    sink = PlainActivitySink(stream)
    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[sink],
    )

    reporter.emit(
        kind="command",
        state="started",
        source="model",
        stage="generation",
        step_id="logical-1",
        message="model request started",
        metrics={"backend_id": "codex-cli", "output_text": "secret body"},
    )

    rendered = stream.getvalue()
    assert "model request started" in rendered
    assert "secret body" not in rendered
    assert "output_text" not in rendered


def test_reporter_drops_content_metrics_and_redacts_metric_strings() -> None:
    seen: list[ActivityEvent] = []
    reporter = ActivityReporter(operation_id="op-1", listeners=[seen.append])

    reporter.emit(
        kind="command",
        state="started",
        source="model",
        metrics={
            "output_text": "secret body",
            "secret body as a key": "also secret",
            "backend_id": "token=sk-123456789-secret",
            "transport_attempt": 1,
        },
    )

    assert seen[0].metrics == {
        "backend_id": "token=[REDACTED]",
        "transport_attempt": 1,
    }


def test_reporter_records_degraded_listener_without_raising() -> None:
    seen: list[ActivityEvent] = []

    def broken_listener(event: ActivityEvent) -> None:
        raise OSError("disk full")

    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[broken_listener, seen.append],
    )

    reporter.emit(kind="heartbeat", state="alive", source="runner")

    assert reporter.degraded
    assert reporter.degradation_messages == ("OSError: disk full",)
    assert len(seen) == 1


def test_reporter_propagates_critical_listener_failure() -> None:
    seen: list[ActivityEvent] = []

    def broken_control_listener(event: ActivityEvent) -> None:
        del event
        raise OSError("status disk full")

    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[seen.append],
        critical_listeners=[broken_control_listener],
    )

    with pytest.raises(OSError, match="status disk full"):
        reporter.emit(kind="command", state="started", source="runner")

    assert seen == []
    assert reporter.degraded is False


def test_reporter_delivers_concurrent_events_in_sequence_order() -> None:
    first_entered = Event()
    release_first = Event()
    seen: list[int] = []

    def listener(event: ActivityEvent) -> None:
        if event.sequence == 1:
            first_entered.set()
            assert release_first.wait(timeout=1)
        seen.append(event.sequence)

    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[listener],
    )
    first = Thread(
        target=lambda: reporter.emit(
            kind="heartbeat", state="alive", source="runner"
        )
    )
    second = Thread(
        target=lambda: reporter.emit(
            kind="heartbeat", state="alive", source="runner"
        )
    )

    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    time.sleep(0.02)
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert seen == [1, 2]


def test_solver_metrics_use_separate_sink_and_bounded_heartbeat(
    tmp_path,
) -> None:
    seen: list[ActivityEvent] = []
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    reporter = ActivityReporter(
        operation_id="op-1",
        listeners=[seen.append],
        utc_now=lambda: now,
        metric_heartbeat_seconds=5,
    )
    reporter.bind_run(
        "run-1",
        tmp_path / "activity.jsonl",
        metrics_path=tmp_path / "metrics.jsonl",
    )
    from foampilot.runtime.telemetry import ResidualMetric

    metric = ResidualMetric(
        simulation_time=0.1,
        field="p",
        initial_residual=0.2,
        final_residual=0.01,
        solver_iterations=2,
    )
    reporter.emit_solver_metric(
        metric=metric,
        elapsed_seconds=1,
        attempt=1,
        stage="solve",
        step_id="solve",
        pid=10,
    )
    reporter.emit_solver_metric(
        metric=metric,
        elapsed_seconds=6,
        attempt=1,
        stage="solve",
        step_id="solve",
        pid=10,
    )

    assert [item.kind.value for item in seen] == ["heartbeat", "heartbeat"]
    assert all(not item.metrics for item in seen)
    metrics = MetricsProjection.from_file(tmp_path / "metrics.jsonl")
    assert metrics.recent("residual:p", 10)
