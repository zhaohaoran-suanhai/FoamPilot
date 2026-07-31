from __future__ import annotations

from pydantic import BaseModel
import pytest

from foampilot.models import (
    GatewayRequestError,
    InMemoryModelTraceSink,
    LineageBudgetExhausted,
    ModelBudgetLedger,
    ModelGateway,
    ModelRequest,
    ModelStage,
    ProviderFailureKind,
)

from tests.support.model_gateway import (
    FakeClock,
    ScriptedProvider,
    provider_error,
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
    provider: ScriptedProvider,
    clock: FakeClock,
) -> ModelGateway:
    return ModelGateway(
        provider=provider,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )


def test_gateway_uses_minimum_remaining_deadline() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [valid_response('{"value":"ok"}')]
    )

    result = _gateway(provider, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=_window(clock, stage_seconds=9),
        trace=InMemoryModelTraceSink(),
    )

    assert result.value == ExampleOutput(value="ok")
    assert result.transport_attempts == 1
    assert provider.timeouts == [pytest.approx(9)]


def test_gateway_retries_overload_with_fixed_backoff() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.OVERLOADED,
                retryable=True,
            ),
            provider_error(
                ProviderFailureKind.OVERLOADED,
                retryable=True,
            ),
            valid_response('{"value":"ok"}'),
        ]
    )
    trace = InMemoryModelTraceSink()

    result = _gateway(provider, clock).generate_structured(
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
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.OVERLOADED,
                retryable=True,
            )
            for _ in range(3)
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 3
    assert len(provider.timeouts) == 3


def test_gateway_does_not_retry_auth_failure() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.AUTH_FAILED,
                retryable=False,
            )
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.failure.kind == ProviderFailureKind.AUTH_FAILED
    assert captured.value.transport_attempts == 1
    assert len(provider.timeouts) == 1


def test_gateway_does_not_retry_permission_failure() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.PERMISSION_DENIED,
                retryable=False,
            )
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert (
        captured.value.failure.kind
        == ProviderFailureKind.PERMISSION_DENIED
    )
    assert captured.value.transport_attempts == 1


def test_gateway_retries_stream_interruption_only_once() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.STREAM_INTERRUPTED,
                retryable=True,
            ),
            provider_error(
                ProviderFailureKind.STREAM_INTERRUPTED,
                retryable=True,
            ),
            valid_response('{"value":"must-not-run"}'),
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 2
    assert clock.sleeps == [5]
    assert len(provider.timeouts) == 2


def test_gateway_traces_request_timeout_separately() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.NETWORK_UNAVAILABLE,
                retryable=True,
                request_timed_out=True,
            )
            for _ in range(3)
        ]
    )
    trace = InMemoryModelTraceSink()

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=trace,
        )

    assert captured.value.deadline_reason == "REQUEST_TIMEOUT"
    assert {
        item.deadline_reason for item in trace.attempts
    } == {"REQUEST_TIMEOUT"}


def test_gateway_does_not_retry_schema_invalid() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            valid_response("not-json"),
            valid_response('{"value":"must-not-be-used"}'),
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert (
        captured.value.failure.kind
        == ProviderFailureKind.SCHEMA_INVALID
    )
    assert captured.value.transport_attempts == 1
    assert len(provider.timeouts) == 1
    assert "json_invalid" in captured.value.failure.detail
    assert "not-json" not in captured.value.failure.detail


def test_retry_after_cannot_cross_stage_deadline() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [
            provider_error(
                ProviderFailureKind.RATE_LIMITED,
                retryable=True,
                retry_after_seconds=30,
            )
        ]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock, stage_seconds=10),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.transport_attempts == 1
    assert captured.value.deadline_reason == "STAGE_DEADLINE"
    assert clock.sleeps == []


def test_total_deadline_is_shared_across_generation_and_repair() -> None:
    clock = FakeClock()
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        now=clock.monotonic,
    )
    generation_provider = ScriptedProvider(
        [valid_response('{"value":"ok"}')],
        on_exchange=lambda: setattr(clock, "value", clock.value + 600),
    )
    _gateway(generation_provider, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=ledger.open_stage(
            ModelStage.GENERATION,
            stage_deadline_seconds=360,
            now=clock.monotonic,
        ),
        trace=InMemoryModelTraceSink(),
    )
    repair_provider = ScriptedProvider(
        [valid_response('{"value":"must-not-run"}')]
    )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(repair_provider, clock).generate_structured(
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
    assert repair_provider.timeouts == []


def test_lineage_transport_limit_is_reserved_before_exchange() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [valid_response('{"value":"must-not-run"}')]
    )

    with pytest.raises(LineageBudgetExhausted):
        _gateway(provider, clock).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=_window(clock, attempts_used=7),
            trace=InMemoryModelTraceSink(),
        )

    assert provider.timeouts == []


def test_trace_records_hash_and_bytes_without_prompt_text() -> None:
    clock = FakeClock()
    provider = ScriptedProvider(
        [valid_response('{"value":"ok"}')]
    )
    trace = InMemoryModelTraceSink()

    _gateway(provider, clock).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=_window(clock),
        trace=trace,
    )

    payload = trace.attempts[0].model_dump_json()
    assert len(trace.attempts[0].request_hash) == 64
    assert trace.attempts[0].prompt_bytes > 0
    assert "Return one value" not in payload
    assert '{"value":"ok"}' not in payload
