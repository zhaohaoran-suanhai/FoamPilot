from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from foampilot.models import (
    ModelRequest,
    ProviderError,
    ProviderFailureKind,
    ProviderResponse,
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


class ScriptedProvider:
    provider = "fake"
    model = "fake-model"
    account_identity_hash = "a" * 64

    def __init__(
        self,
        events: list[ProviderResponse | ProviderError],
        *,
        on_exchange: Callable[[], None] | None = None,
    ) -> None:
        self.events = list(events)
        self.timeouts: list[float] = []
        self.on_exchange = on_exchange

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        del request
        self.timeouts.append(timeout_seconds)
        if self.on_exchange is not None:
            self.on_exchange()
        event = self.events.pop(0)
        if isinstance(event, ProviderError):
            raise event
        return event


def valid_response(text: str) -> ProviderResponse:
    return ProviderResponse(
        provider="fake",
        model="fake-model",
        purpose="generation",
        output_text=text,
        http_status=200,
        provider_request_id="request-1",
        output_bytes=len(text.encode("utf-8")),
    )


def provider_error(
    kind: ProviderFailureKind,
    *,
    retryable: bool,
    retry_after_seconds: float | None = None,
    request_timed_out: bool = False,
) -> ProviderError:
    return ProviderError(
        kind=kind,
        provider="fake",
        model="fake-model",
        purpose="generation",
        detail=kind.value,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        request_timed_out=request_timed_out,
    )
