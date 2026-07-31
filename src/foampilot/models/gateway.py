"""Governed structured model calls above a single-exchange provider."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic import ValidationError

from .base import ModelRequest, StrictModel
from .budgets import ModelBudgetWindow
from .circuit_breaker import (
    CircuitBreakerKey,
    CircuitDeferredError,
    SharedCircuitBreaker,
)
from .errors import ProviderError, ProviderFailureKind
from .provider import ProviderClient
from .traces import ModelAttemptTrace, ModelTraceSink


T = TypeVar("T", bound=BaseModel)
DeadlineReason = str | None


class ModelResult(StrictModel, Generic[T]):
    value: T
    logical_request_id: str
    transport_attempts: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class GatewayRequestError(RuntimeError):
    def __init__(
        self,
        *,
        failure: ProviderError,
        logical_request_id: str,
        transport_attempts: int,
        deadline_reason: DeadlineReason,
        deferred_by_circuit: bool = False,
    ) -> None:
        super().__init__(failure.detail)
        self.failure = failure
        self.logical_request_id = logical_request_id
        self.transport_attempts = transport_attempts
        self.deadline_reason = deadline_reason
        self.deferred_by_circuit = deferred_by_circuit


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline_reason(
    *,
    now: float,
    budget: ModelBudgetWindow,
) -> DeadlineReason:
    stage_remaining = budget.stage_deadline_monotonic - now
    total_remaining = budget.total_deadline_monotonic - now
    if total_remaining <= stage_remaining:
        return "TOTAL_MODEL_DEADLINE"
    return "STAGE_DEADLINE"


def _retry_delay(
    failure: ProviderError,
    transport_attempt: int,
) -> float | None:
    if not failure.retryable:
        return None
    if failure.kind == ProviderFailureKind.STREAM_INTERRUPTED:
        return 5 if transport_attempt < 2 else None
    if failure.kind == ProviderFailureKind.RATE_LIMITED:
        if failure.retry_after_seconds is not None:
            return failure.retry_after_seconds
    if failure.kind in {
        ProviderFailureKind.OVERLOADED,
        ProviderFailureKind.RATE_LIMITED,
        ProviderFailureKind.NETWORK_UNAVAILABLE,
    }:
        delays = (5.0, 15.0)
        if transport_attempt <= len(delays):
            return delays[transport_attempt - 1]
    return None


def _schema_validation_detail(
    schema: type[BaseModel],
    error: Exception,
) -> str:
    prefix = f"provider output failed {schema.__name__} validation"
    if not isinstance(error, ValidationError):
        return prefix
    details: list[str] = []
    for item in error.errors(
        include_input=False,
        include_url=False,
    )[:8]:
        location = ".".join(str(part) for part in item["loc"])
        message = " ".join(str(item["msg"]).split())[:160]
        details.append(
            f"{location or '<root>'}: {item['type']}: {message}"
        )
    if not details:
        return prefix
    return prefix + " (" + "; ".join(details) + ")"


class ModelGateway:
    def __init__(
        self,
        *,
        provider: ProviderClient,
        circuit_breaker: SharedCircuitBreaker | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.provider = provider
        self.monotonic = monotonic
        self.sleep = sleep
        self.utc_now = utc_now
        self.circuit_breaker = circuit_breaker or SharedCircuitBreaker(
            monotonic=monotonic
        )

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def provider_name(self) -> str:
        return self.provider.provider

    def _key(self) -> CircuitBreakerKey:
        return CircuitBreakerKey(
            provider=self.provider.provider,
            model=self.provider.model,
            account_identity_hash=self.provider.account_identity_hash,
        )

    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[T],
        *,
        budget: ModelBudgetWindow,
        trace: ModelTraceSink,
    ) -> ModelResult[T]:
        logical_request_id = uuid4().hex
        started = self.monotonic()
        enriched = request.model_copy(
            update={"response_schema": schema.model_json_schema()}
        )
        request_bytes = json.dumps(
            enriched.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_hash = sha256(request_bytes).hexdigest()
        key = self._key()
        try:
            self.circuit_breaker.before_request(key)
        except CircuitDeferredError as error:
            failure = ProviderError(
                kind=error.last_failure_kind,
                provider=key.provider,
                model=key.model,
                purpose=request.purpose,
                detail="provider circuit is open",
                retryable=True,
                retry_after_seconds=error.retry_after_seconds,
            )
            raise GatewayRequestError(
                failure=failure,
                logical_request_id=logical_request_id,
                transport_attempts=0,
                deadline_reason=None,
                deferred_by_circuit=True,
            ) from error

        last_failure: ProviderError | None = None
        last_deadline_reason: DeadlineReason = None
        attempts = 0
        while attempts < budget.max_transport_attempts:
            now = self.monotonic()
            stage_remaining = budget.stage_deadline_monotonic - now
            total_remaining = budget.total_deadline_monotonic - now
            attempt_timeout = min(
                budget.request_timeout_seconds,
                stage_remaining,
                total_remaining,
            )
            if attempt_timeout <= 0:
                last_deadline_reason = _deadline_reason(
                    now=now,
                    budget=budget,
                )
                break
            budget.ledger.reserve_transport_attempt()
            attempts += 1
            attempt_started_mono = self.monotonic()
            attempt_started_utc = self.utc_now()
            response = None
            failure = None
            try:
                response = self.provider.exchange(
                    enriched,
                    timeout_seconds=attempt_timeout,
                )
                self.circuit_breaker.record_success(key)
                try:
                    value = schema.model_validate_json(
                        response.output_text
                    )
                except Exception as error:
                    failure = ProviderError(
                        kind=ProviderFailureKind.SCHEMA_INVALID,
                        provider=response.provider,
                        model=response.model,
                        purpose=request.purpose,
                        detail=_schema_validation_detail(schema, error),
                        retryable=False,
                        http_status=response.http_status,
                        provider_request_id=(
                            response.provider_request_id
                        ),
                    )
                    raise failure from error
            except ProviderError as error:
                failure = error

            attempt_finished_mono = self.monotonic()
            attempt_finished_utc = self.utc_now()
            trace.record(
                ModelAttemptTrace(
                    purpose=request.purpose,
                    provider=self.provider.provider,
                    model=self.provider.model,
                    request_hash=request_hash,
                    logical_request_id=logical_request_id,
                    transport_attempt=attempts,
                    started_at=attempt_started_utc,
                    finished_at=attempt_finished_utc,
                    elapsed_seconds=max(
                        attempt_finished_mono - attempt_started_mono,
                        0,
                    ),
                    prompt_bytes=len(request_bytes),
                    output_bytes=(
                        response.output_bytes
                        if response is not None
                        else 0
                    ),
                    http_status=(
                        response.http_status
                        if response is not None
                        else (
                            failure.http_status
                            if failure is not None
                            else None
                        )
                    ),
                    provider_request_id=(
                        response.provider_request_id
                        if response is not None
                        else (
                            failure.provider_request_id
                            if failure is not None
                            else None
                        )
                    ),
                    provider_error_code=(
                        failure.kind.value
                        if failure is not None
                        else None
                    ),
                    retryable=(
                        failure.retryable
                        if failure is not None
                        else None
                    ),
                    partial_output_bytes=(
                        failure.partial_output_bytes
                        if failure is not None
                        else 0
                    ),
                    deadline_reason=(
                        "REQUEST_TIMEOUT"
                        if failure is not None
                        and failure.request_timed_out
                        else None
                    ),
                )
            )
            if failure is None:
                assert response is not None
                return ModelResult(
                    value=value,
                    logical_request_id=logical_request_id,
                    transport_attempts=attempts,
                    elapsed_seconds=max(
                        self.monotonic() - started,
                        0,
                    ),
                )

            last_failure = failure
            if failure.request_timed_out:
                last_deadline_reason = "REQUEST_TIMEOUT"
            delay = _retry_delay(failure, attempts)
            if (
                delay is None
                or attempts >= budget.max_transport_attempts
            ):
                break
            now = self.monotonic()
            remaining = min(
                budget.stage_deadline_monotonic - now,
                budget.total_deadline_monotonic - now,
            )
            if delay >= remaining:
                last_deadline_reason = _deadline_reason(
                    now=now,
                    budget=budget,
                )
                break
            self.sleep(delay)

        if last_failure is None:
            deadline_reason = last_deadline_reason or _deadline_reason(
                now=self.monotonic(),
                budget=budget,
            )
            raise GatewayRequestError(
                failure=ProviderError(
                    kind=ProviderFailureKind.NETWORK_UNAVAILABLE,
                    provider=self.provider.provider,
                    model=self.provider.model,
                    purpose=request.purpose,
                    detail=(
                        "model request deadline expired before transport"
                    ),
                    retryable=True,
                ),
                logical_request_id=logical_request_id,
                transport_attempts=0,
                deadline_reason=deadline_reason,
            )
        self.circuit_breaker.record_failure(key, last_failure.kind)
        raise GatewayRequestError(
            failure=last_failure,
            logical_request_id=logical_request_id,
            transport_attempts=attempts,
            deadline_reason=last_deadline_reason,
        )
