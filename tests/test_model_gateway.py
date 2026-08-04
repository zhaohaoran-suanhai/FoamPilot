from __future__ import annotations

from pydantic import BaseModel
import pytest

from foampilot.models import (
    BackendFailureKind,
    BackendMode,
    BackendRegistry,
    GatewayRequestError,
    InMemoryModelTraceSink,
    LineageBudgetExhausted,
    ModelBudgetLedger,
    ModelGateway,
    ModelRequest,
    ModelStage,
)

from tests.support.model_gateway import (
    FakeClock,
    ScriptedBackend,
    backend_error,
    valid_response,
)


class ExampleOutput(BaseModel):
    value: str


REQUEST = ModelRequest(
    purpose="generation",
    system_prompt="Return structured output.",
    user_prompt="Return one value.",
)


def _window(
    clock: FakeClock,
    *,
    stage_seconds: float = 360,
    total_seconds: float = 600,
    attempts_used: int = 0,
):
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=total_seconds,
        lineage_transport_attempt_limit=7,
        transport_attempts_used=attempts_used,
        now=clock.monotonic,
    )
    return ledger.open_stage(
        ModelStage.GENERATION,
        request_timeout_seconds=300,
        stage_deadline_seconds=stage_seconds,
        max_transport_attempts=3,
        now=clock.monotonic,
    )


def _gateway(
    backend: ScriptedBackend,
    clock: FakeClock,
) -> ModelGateway:
    registry = BackendRegistry()
    registry.register(backend, priority=10)
    return ModelGateway(
        registry=registry,
        mode=BackendMode.NORMAL,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )


def test_gateway_uses_minimum_remaining_deadline() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [valid_response('{"value":"ok"}')]
    )

    result = _gateway(backend, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=_window(clock, stage_seconds=9),
        trace=InMemoryModelTraceSink(),
    )

    assert result.value == ExampleOutput(value="ok")
    assert result.transport_attempts == 1
    assert backend.timeouts == [pytest.approx(9)]


def test_gateway_retries_overload_with_fixed_backoff() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.OVERLOADED,
                retryable=True,
            ),
            backend_error(
                BackendFailureKind.OVERLOADED,
                retryable=True,
            ),
            valid_response('{"value":"ok"}'),
        ]
    )
    trace = InMemoryModelTraceSink()

    result = _gateway(backend, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=_window(clock),
        trace=trace,
    )

    assert result.transport_attempts == 3
    assert clock.sleeps == [5, 15]
    assert len(trace.attempts) == 3


def test_gateway_stops_after_three_persistent_overloads() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.OVERLOADED,
                retryable=True,
            )
            for _ in range(3)
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 3
    assert len(backend.timeouts) == 3


def test_gateway_does_not_retry_auth_failure() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.AUTH_FAILED,
                retryable=False,
            )
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.failure.kind == BackendFailureKind.AUTH_FAILED
    assert captured.value.transport_attempts == 1
    assert len(backend.timeouts) == 1


def test_gateway_does_not_hide_policy_rejection() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.POLICY_REJECTED,
                retryable=False,
            )
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert (
        captured.value.failure.kind
        == BackendFailureKind.POLICY_REJECTED
    )
    assert captured.value.transport_attempts == 1


def test_gateway_retries_process_interruption_with_bounded_backoff() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.PROCESS_INTERRUPTED,
                retryable=True,
            ),
            backend_error(
                BackendFailureKind.PROCESS_INTERRUPTED,
                retryable=True,
            ),
            valid_response('{"value":"must-not-run"}'),
        ]
    )

    result = _gateway(backend, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=_window(clock),
        trace=InMemoryModelTraceSink(),
    )

    assert result.transport_attempts == 3
    assert clock.sleeps == [5, 15]
    assert len(backend.timeouts) == 3


def test_gateway_traces_request_timeout_separately() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.NETWORK_UNAVAILABLE,
                retryable=True,
                request_timed_out=True,
            )
            for _ in range(3)
        ]
    )
    trace = InMemoryModelTraceSink()

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=trace,
        )

    assert captured.value.deadline_reason == "REQUEST_TIMEOUT"
    assert {
        item.deadline_reason for item in trace.attempts
    } == {"REQUEST_TIMEOUT"}


def test_gateway_corrects_schema_once_then_stops() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            valid_response("not-json"),
            valid_response("still-not-json"),
            valid_response('{"value":"must-not-be-used"}'),
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert (
        captured.value.failure.kind
        == BackendFailureKind.SCHEMA_INVALID
    )
    assert captured.value.transport_attempts == 2
    assert len(backend.timeouts) == 2
    assert "json_invalid" in captured.value.failure.detail
    assert "not-json" not in captured.value.failure.detail


def test_gateway_does_not_correct_backend_request_schema_error() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.SCHEMA_INVALID,
                retryable=False,
            ),
            valid_response('{"value":"must-not-run"}'),
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 1
    assert len(backend.timeouts) == 1


def test_retry_after_cannot_cross_stage_deadline() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.RATE_LIMITED,
                retryable=True,
                retry_after_seconds=30,
            )
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock, stage_seconds=10),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 1
    assert captured.value.deadline_reason == "STAGE_DEADLINE"
    assert clock.sleeps == []


def test_gateway_does_not_start_retry_with_short_remaining_window() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.TIMEOUT,
                retryable=True,
                request_timed_out=True,
            ),
            valid_response('{"value":"must-not-run"}'),
        ],
        on_exchange=lambda: setattr(clock, "value", clock.value + 250),
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock, stage_seconds=360),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 1
    assert len(backend.timeouts) == 1


def test_total_deadline_is_shared_across_generation_and_repair() -> None:
    clock = FakeClock()
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        now=clock.monotonic,
    )
    generation_backend = ScriptedBackend(
        [valid_response('{"value":"ok"}')],
        on_exchange=lambda: setattr(clock, "value", clock.value + 600),
    )
    _gateway(generation_backend, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=ledger.open_stage(
            ModelStage.GENERATION,
            stage_deadline_seconds=360,
            now=clock.monotonic,
        ),
        trace=InMemoryModelTraceSink(),
    )
    repair_backend = ScriptedBackend(
        [valid_response('{"value":"must-not-run"}')]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(repair_backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=ledger.open_stage(
                ModelStage.REPAIR,
                stage_deadline_seconds=240,
                now=clock.monotonic,
            ),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 0
    assert captured.value.deadline_reason == "TOTAL_MODEL_DEADLINE"
    assert repair_backend.timeouts == []


def test_lineage_transport_limit_is_reserved_before_exchange() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [valid_response('{"value":"must-not-run"}')]
    )

    with pytest.raises(LineageBudgetExhausted):
        _gateway(backend, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock, attempts_used=7),
            trace=InMemoryModelTraceSink(),
        )

    assert backend.timeouts == []


def test_trace_records_hash_and_bytes_without_prompt_text() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        [valid_response('{"value":"ok"}')]
    )
    trace = InMemoryModelTraceSink()

    _gateway(backend, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=_window(clock),
        trace=trace,
    )

    payload = trace.attempts[0].model_dump_json()
    assert len(trace.attempts[0].request_hash) == 64
    assert trace.attempts[0].request_bytes > 0
    assert "Return one value" not in payload
    assert '{"value":"ok"}' not in payload
