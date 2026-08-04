from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel
import pytest

from foampilot.models import (
    BackendError,
    BackendFailureKind,
    BackendMode,
    BackendRegistry,
    BackendResponse,
    CircuitBreakerKey,
    GatewayRequestError,
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelGateway,
    ModelRequest,
    ModelStage,
    SharedCircuitBreaker,
)

from tests.support.model_gateway import FakeClock


class Answer(BaseModel):
    answer: int


REQUEST = ModelRequest(
    purpose="generation",
    system_prompt="Return structured output.",
    user_prompt="Return seven.",
)


class ScriptedBackend:
    def __init__(
        self,
        backend_id: str,
        events: list[BackendResponse | BackendError],
        *,
        model: str = "test-model",
        on_exchange: Callable[[], None] | None = None,
    ) -> None:
        self.backend_id = backend_id
        self.model = model
        self.identity_hash = (backend_id.encode().hex() + "0" * 64)[:64]
        self.events = list(events)
        self.calls = 0
        self.timeouts: list[float] = []
        self.on_exchange = on_exchange

    def probe(self, *, timeout_seconds: float):
        raise AssertionError("gateway must not probe during a request")

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> BackendResponse:
        del request
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        if self.on_exchange is not None:
            self.on_exchange()
        event = self.events.pop(0)
        if isinstance(event, BackendError):
            raise event
        return event


def _response(backend_id: str, text: str) -> BackendResponse:
    return BackendResponse(
        backend_id=backend_id,
        model="test-model",
        purpose="generation",
        output_text=text,
        status_code=200,
        request_id=f"request-{backend_id}",
        output_bytes=len(text.encode()),
    )


def _error(
    backend_id: str,
    kind: BackendFailureKind,
    *,
    retryable: bool = True,
) -> BackendError:
    return BackendError(
        kind=kind,
        backend_id=backend_id,
        model="test-model",
        purpose="generation",
        detail=kind.value,
        retryable=retryable,
    )


def _window(clock: FakeClock, *, attempts: int = 3):
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        now=clock.monotonic,
    )
    return ledger.open_stage(
        ModelStage.GENERATION,
        request_timeout_seconds=300,
        stage_deadline_seconds=360,
        max_transport_attempts=attempts,
        now=clock.monotonic,
    )


def _registry(*backends: ScriptedBackend) -> BackendRegistry:
    registry = BackendRegistry()
    for priority, backend in enumerate(backends, start=1):
        registry.register(backend, priority=priority)
    return registry


def _gateway(
    clock: FakeClock,
    *backends: ScriptedBackend,
    mode: BackendMode = BackendMode.NORMAL,
    pinned_backend_id: str | None = None,
    circuit_breaker: SharedCircuitBreaker | None = None,
) -> ModelGateway:
    return ModelGateway(
        registry=_registry(*backends),
        mode=mode,
        pinned_backend_id=pinned_backend_id,
        pinned_model=("test-model" if pinned_backend_id else None),
        circuit_breaker=circuit_breaker,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )


def test_normal_mode_fails_over_after_bounded_retry() -> None:
    clock = FakeClock()
    first = ScriptedBackend(
        "a",
        [
            _error("a", BackendFailureKind.OVERLOADED),
            _error("a", BackendFailureKind.OVERLOADED),
        ],
    )
    second = ScriptedBackend("b", [_response("b", '{"answer":7}')])

    result = _gateway(clock, first, second).generate_structured(
        REQUEST,
        Answer,
        budget=_window(clock),
        trace=InMemoryModelTraceSink(),
    )

    assert result.value.answer == 7
    assert result.backend_id == "b"
    assert result.backend_switches == 1
    assert first.calls == 2
    assert second.calls == 1


def test_qualification_never_calls_second_backend() -> None:
    clock = FakeClock()
    first = ScriptedBackend(
        "a",
        [_error("a", BackendFailureKind.OVERLOADED) for _ in range(3)],
    )
    second = ScriptedBackend("b", [_response("b", '{"answer":7}')])

    with pytest.raises(GatewayRequestError):
        _gateway(
            clock,
            first,
            second,
            mode=BackendMode.QUALIFICATION,
            pinned_backend_id="a",
        ).generate_structured(
            REQUEST,
            Answer,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert second.calls == 0


def test_schema_invalid_gets_one_zero_delay_correction() -> None:
    clock = FakeClock()
    backend = ScriptedBackend(
        "a",
        [_response("a", "not-json"), _response("a", '{"answer":7}')],
    )

    result = _gateway(clock, backend).generate_structured(
        REQUEST,
        Answer,
        budget=_window(clock),
        trace=InMemoryModelTraceSink(),
    )

    assert result.value.answer == 7
    assert result.transport_attempts == 2
    assert clock.sleeps == []


def test_policy_rejection_never_fails_over() -> None:
    clock = FakeClock()
    first = ScriptedBackend(
        "a",
        [_error("a", BackendFailureKind.POLICY_REJECTED, retryable=False)],
    )
    second = ScriptedBackend("b", [_response("b", '{"answer":7}')])

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(clock, first, second).generate_structured(
            REQUEST,
            Answer,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.failure.kind == BackendFailureKind.POLICY_REJECTED
    assert second.calls == 0


def test_auth_failure_switches_without_waiting() -> None:
    clock = FakeClock()
    first = ScriptedBackend(
        "a",
        [_error("a", BackendFailureKind.AUTH_FAILED, retryable=False)],
    )
    second = ScriptedBackend("b", [_response("b", '{"answer":7}')])

    result = _gateway(clock, first, second).generate_structured(
        REQUEST,
        Answer,
        budget=_window(clock),
        trace=InMemoryModelTraceSink(),
    )

    assert result.backend_id == "b"
    assert result.backend_switches == 1
    assert clock.sleeps == []


def test_open_circuit_is_skipped_and_recorded_as_switch_reason() -> None:
    clock = FakeClock()
    breaker = SharedCircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=120,
        monotonic=clock.monotonic,
    )
    first = ScriptedBackend("a", [_response("a", '{"answer":1}')])
    second = ScriptedBackend("b", [_response("b", '{"answer":7}')])
    key = CircuitBreakerKey(
        backend_id="a",
        model="test-model",
        identity_hash=first.identity_hash,
    )
    breaker.record_failure(key, BackendFailureKind.OVERLOADED)
    breaker.record_failure(key, BackendFailureKind.OVERLOADED)
    trace = InMemoryModelTraceSink()

    result = _gateway(
        clock,
        first,
        second,
        circuit_breaker=breaker,
    ).generate_structured(
        REQUEST,
        Answer,
        budget=_window(clock),
        trace=trace,
    )

    assert result.backend_id == "b"
    assert result.backend_switches == 1
    assert first.calls == 0
    assert trace.attempts[0].switch_reason == "CIRCUIT_OPEN"
    assert trace.attempts[0].schema_version == 2


def test_all_open_circuits_defer_without_transport() -> None:
    clock = FakeClock()
    breaker = SharedCircuitBreaker(
        failure_threshold=1,
        cooldown_seconds=120,
        monotonic=clock.monotonic,
    )
    first = ScriptedBackend("a", [_response("a", '{"answer":1}')])
    second = ScriptedBackend("b", [_response("b", '{"answer":2}')])
    for backend in (first, second):
        breaker.record_failure(
            CircuitBreakerKey(
                backend_id=backend.backend_id,
                model=backend.model,
                identity_hash=backend.identity_hash,
            ),
            BackendFailureKind.OVERLOADED,
        )

    with pytest.raises(GatewayRequestError) as captured:
        _gateway(
            clock,
            first,
            second,
            circuit_breaker=breaker,
        ).generate_structured(
            REQUEST,
            Answer,
            budget=_window(clock),
            trace=InMemoryModelTraceSink(),
        )

    assert captured.value.deferred_by_circuit is True
    assert captured.value.transport_attempts == 0
    assert first.calls == second.calls == 0


def test_gateway_policy_identity_is_stable_and_backend_neutral() -> None:
    clock = FakeClock()
    backend = ScriptedBackend("a", [_response("a", '{"answer":7}')])
    gateway = _gateway(clock, backend)

    assert gateway.primary_backend_id == "a"
    assert gateway.primary_model == "test-model"
    assert len(gateway.policy_sha256) == 64
