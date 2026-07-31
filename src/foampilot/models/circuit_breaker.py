"""Thread-safe in-process circuit breaker for shared provider access."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import threading
import time

from .errors import ProviderFailureKind


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerKey:
    provider: str
    model: str
    account_identity_hash: str


class CircuitDeferredError(RuntimeError):
    def __init__(
        self,
        *,
        key: CircuitBreakerKey,
        retry_after_seconds: float,
        last_failure_kind: ProviderFailureKind,
    ) -> None:
        super().__init__("provider circuit is open")
        self.key = key
        self.retry_after_seconds = retry_after_seconds
        self.last_failure_kind = last_failure_kind


@dataclass
class _CircuitRecord:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False
    last_failure_kind: ProviderFailureKind = (
        ProviderFailureKind.NETWORK_UNAVAILABLE
    )


class SharedCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 120,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.monotonic = monotonic
        self._records: dict[CircuitBreakerKey, _CircuitRecord] = {}
        self._lock = threading.Lock()

    def before_request(self, key: CircuitBreakerKey) -> None:
        with self._lock:
            record = self._records.setdefault(key, _CircuitRecord())
            if record.state == CircuitState.CLOSED:
                return
            now = self.monotonic()
            assert record.opened_at is not None
            remaining = self.cooldown_seconds - (now - record.opened_at)
            if record.state == CircuitState.OPEN and remaining <= 0:
                record.state = CircuitState.HALF_OPEN
                record.probe_in_flight = True
                return
            raise CircuitDeferredError(
                key=key,
                retry_after_seconds=max(remaining, 0),
                last_failure_kind=record.last_failure_kind,
            )

    def record_success(self, key: CircuitBreakerKey) -> None:
        with self._lock:
            self._records[key] = _CircuitRecord()

    def record_failure(
        self,
        key: CircuitBreakerKey,
        kind: ProviderFailureKind,
    ) -> None:
        if kind not in {
            ProviderFailureKind.OVERLOADED,
            ProviderFailureKind.NETWORK_UNAVAILABLE,
        }:
            return
        with self._lock:
            record = self._records.setdefault(key, _CircuitRecord())
            record.last_failure_kind = kind
            if record.state == CircuitState.HALF_OPEN:
                record.state = CircuitState.OPEN
                record.opened_at = self.monotonic()
                record.probe_in_flight = False
                record.consecutive_failures = self.failure_threshold
                return
            record.consecutive_failures += 1
            if record.consecutive_failures >= self.failure_threshold:
                record.state = CircuitState.OPEN
                record.opened_at = self.monotonic()
                record.probe_in_flight = False
