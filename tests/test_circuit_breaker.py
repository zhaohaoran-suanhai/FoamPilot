from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from foampilot.models import (
    CircuitBreakerKey,
    CircuitDeferredError,
    ProviderFailureKind,
    SharedCircuitBreaker,
)

from tests.support.model_gateway import FakeClock


KEY = CircuitBreakerKey(
    provider="fake",
    model="fake-model",
    account_identity_hash="a" * 64,
)


def _breaker(clock: FakeClock) -> SharedCircuitBreaker:
    return SharedCircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=120,
        monotonic=clock.monotonic,
    )


def test_breaker_opens_after_two_failed_logical_requests() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    breaker.before_request(KEY)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    breaker.before_request(KEY)
    breaker.record_failure(
        KEY,
        ProviderFailureKind.NETWORK_UNAVAILABLE,
    )

    with pytest.raises(CircuitDeferredError):
        breaker.before_request(KEY)


def test_non_breaker_failure_does_not_open_circuit() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(4):
        breaker.before_request(KEY)
        breaker.record_failure(
            KEY,
            ProviderFailureKind.RATE_LIMITED,
        )

    breaker.before_request(KEY)


def test_half_open_allows_only_one_probe() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    clock.sleep(120)

    def attempt() -> str:
        try:
            breaker.before_request(KEY)
        except CircuitDeferredError:
            return "deferred"
        return "probe"

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _: attempt(), range(16)))

    assert results.count("probe") == 1
    assert results.count("deferred") == 15


def test_half_open_success_closes_circuit() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    clock.sleep(120)

    breaker.before_request(KEY)
    breaker.record_success(KEY)

    breaker.before_request(KEY)


def test_half_open_failure_reopens_circuit() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)
    clock.sleep(120)

    breaker.before_request(KEY)
    breaker.record_failure(KEY, ProviderFailureKind.OVERLOADED)

    with pytest.raises(CircuitDeferredError):
        breaker.before_request(KEY)
