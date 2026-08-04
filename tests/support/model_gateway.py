from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from foampilot.models import (
    BackendError,
    BackendFailureKind,
    BackendHealth,
    BackendResponse,
    ModelRequest,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds

    def utc_now(self) -> datetime:
        return datetime(2026, 7, 30, tzinfo=timezone.utc)


class ScriptedBackend:
    def __init__(
        self,
        events: list[BackendResponse | BackendError],
        *,
        backend_id: str = "fake",
        model: str = "fake-model",
        on_exchange: Callable[[], None] | None = None,
    ) -> None:
        self.backend_id = backend_id
        self.model = model
        self.identity_hash = "a" * 64
        self.events = list(events)
        self.timeouts: list[float] = []
        self.on_exchange = on_exchange

    def probe(self, *, timeout_seconds: float) -> BackendHealth:
        del timeout_seconds
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state="available",
            message="模型后端可用。",
            recovery="无需处理。",
            elapsed_seconds=0,
        )

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> BackendResponse:
        del request
        self.timeouts.append(timeout_seconds)
        if self.on_exchange is not None:
            self.on_exchange()
        event = self.events.pop(0)
        if isinstance(event, BackendError):
            raise event
        return event


def valid_response(
    text: str,
    *,
    backend_id: str = "fake",
    model: str = "fake-model",
) -> BackendResponse:
    return BackendResponse(
        backend_id=backend_id,
        model=model,
        purpose="generation",
        output_text=text,
        status_code=200,
        request_id="request-1",
        output_bytes=len(text.encode("utf-8")),
    )


def backend_error(
    kind: BackendFailureKind,
    *,
    retryable: bool,
    retry_after_seconds: float | None = None,
    request_timed_out: bool = False,
) -> BackendError:
    return BackendError(
        kind=kind,
        backend_id="fake",
        model="fake-model",
        purpose="generation",
        detail=kind.value,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        request_timed_out=request_timed_out,
    )
