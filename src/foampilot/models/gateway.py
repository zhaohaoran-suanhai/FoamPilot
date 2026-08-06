"""位于模型后端之上的预算、降级、熔断和结构化输出边界。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from .backend import BackendResponse, ModelBackend
from .base import ModelRequest, StrictModel
from .budgets import ModelBudgetWindow
from .circuit_breaker import (
    CircuitBreakerKey,
    CircuitDeferredError,
    SharedCircuitBreaker,
)
from .errors import BackendError, BackendFailureKind
from .registry import BackendMode, BackendRegistry
from .schema import strict_response_schema
from .traces import (
    ModelAttemptTrace,
    ModelTraceSink,
    StructuredOutputNormalization,
)


T = TypeVar("T", bound=BaseModel)
DeadlineReason = str | None


class ModelResult(StrictModel, Generic[T]):
    value: T
    logical_request_id: str
    backend_id: str
    model: str
    transport_attempts: int = Field(ge=0)
    backend_switches: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class GatewayRequestError(RuntimeError):
    def __init__(
        self,
        *,
        failure: BackendError,
        logical_request_id: str,
        transport_attempts: int,
        backend_switches: int,
        deadline_reason: DeadlineReason,
        deferred_by_circuit: bool = False,
    ) -> None:
        super().__init__(failure.detail)
        self.failure = failure
        self.logical_request_id = logical_request_id
        self.transport_attempts = transport_attempts
        self.backend_switches = backend_switches
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
    failure: BackendError,
    backend_attempt: int,
) -> float | None:
    if failure.kind == BackendFailureKind.RATE_LIMITED:
        return failure.retry_after_seconds
    if failure.kind in {
        BackendFailureKind.OVERLOADED,
        BackendFailureKind.NETWORK_UNAVAILABLE,
        BackendFailureKind.TIMEOUT,
        BackendFailureKind.PROCESS_INTERRUPTED,
    }:
        delays = (5.0, 15.0)
        if backend_attempt <= len(delays):
            return delays[backend_attempt - 1]
    return None


def _schema_validation_detail(
    schema: type[BaseModel],
    error: Exception,
) -> str:
    prefix = f"backend output failed {schema.__name__} validation"
    if not isinstance(error, ValidationError):
        return prefix
    details: list[str] = []
    for item in error.errors(include_input=False, include_url=False)[:8]:
        location = ".".join(str(part) for part in item["loc"])
        message = " ".join(str(item["msg"]).split())[:160]
        details.append(
            f"{location or '<root>'}: {item['type']}: {message}"
        )
    return prefix if not details else prefix + " (" + "; ".join(details) + ")"


def _corrected_request(
    request: ModelRequest,
    *,
    detail: str,
) -> ModelRequest:
    return request.model_copy(
        update={
            "system_prompt": (
                request.system_prompt
                + "\n\n上一次输出未通过 JSON Schema 验证。"
                "只纠正输出结构，不要解释原因。验证位置："
                + detail
            )
        }
    )


class ModelGateway:
    """普通运行允许受控降级，qualification 始终固定一个后端。"""

    def __init__(
        self,
        *,
        registry: BackendRegistry,
        mode: BackendMode | str = BackendMode.NORMAL,
        pinned_backend_id: str | None = None,
        pinned_model: str | None = None,
        circuit_breaker: SharedCircuitBreaker | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.registry = registry
        self.mode = BackendMode(mode)
        self.pinned_backend_id = pinned_backend_id
        self.pinned_model = pinned_model
        self.monotonic = monotonic
        self.sleep = sleep
        self.utc_now = utc_now
        self.circuit_breaker = circuit_breaker or SharedCircuitBreaker(
            monotonic=monotonic
        )
        self._candidates = tuple(
            registry.candidates(
                mode=self.mode,
                pinned_backend_id=pinned_backend_id,
                pinned_model=pinned_model,
            )
        )
        if not self._candidates:
            raise ValueError("model backend registry has no candidates")
        policy = {
            "schema_version": 1,
            "mode": self.mode.value,
            "pinned_backend_id": pinned_backend_id,
            "pinned_model": pinned_model,
            "candidates": [
                {
                    "backend_id": backend.backend_id,
                    "model": backend.model,
                    "identity_hash": backend.identity_hash,
                }
                for backend in self._candidates
            ],
        }
        canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        self._policy_sha256 = sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def primary_backend_id(self) -> str:
        return self._candidates[0].backend_id

    @property
    def primary_model(self) -> str:
        return self._candidates[0].model

    @property
    def policy_sha256(self) -> str:
        return self._policy_sha256

    @property
    def automatic_failover(self) -> bool:
        return self.mode == BackendMode.NORMAL and len(self._candidates) > 1

    def _key(self, backend: ModelBackend) -> CircuitBreakerKey:
        return CircuitBreakerKey(
            backend_id=backend.backend_id,
            model=backend.model,
            identity_hash=backend.identity_hash,
        )

    def _circuit_failure(
        self,
        backend: ModelBackend,
        request: ModelRequest,
        error: CircuitDeferredError,
    ) -> BackendError:
        return BackendError(
            kind=error.last_failure_kind,
            backend_id=backend.backend_id,
            model=backend.model,
            purpose=request.purpose,
            detail="backend circuit is open",
            retryable=True,
            retry_after_seconds=error.retry_after_seconds,
        )

    def _deadline_failure(
        self,
        backend: ModelBackend,
        request: ModelRequest,
    ) -> BackendError:
        return BackendError(
            kind=BackendFailureKind.TIMEOUT,
            backend_id=backend.backend_id,
            model=backend.model,
            purpose=request.purpose,
            detail="model request deadline expired before transport",
            retryable=True,
        )

    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[T],
        *,
        budget: ModelBudgetWindow,
        trace: ModelTraceSink,
        output_normalizer: Callable[
            [str],
            tuple[T, tuple[StructuredOutputNormalization, ...]],
        ]
        | None = None,
    ) -> ModelResult[T]:
        logical_request_id = uuid4().hex
        started = self.monotonic()
        base_request = request.model_copy(
            update={
                "response_schema": strict_response_schema(
                    schema.model_json_schema()
                )
            }
        )
        request_bytes = json.dumps(
            base_request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_hash = sha256(request_bytes).hexdigest()

        attempts = 0
        backend_switches = 0
        switch_reason: str | None = None
        last_failure: BackendError | None = None
        last_deadline_reason: DeadlineReason = None
        deferred_count = 0

        for backend_index, backend in enumerate(self._candidates):
            if backend_index > 0:
                backend_switches += 1
            key = self._key(backend)
            try:
                self.circuit_breaker.before_request(key)
            except CircuitDeferredError as error:
                deferred_count += 1
                last_failure = self._circuit_failure(backend, request, error)
                switch_reason = "CIRCUIT_OPEN"
                if self.mode == BackendMode.QUALIFICATION:
                    raise GatewayRequestError(
                        failure=last_failure,
                        logical_request_id=logical_request_id,
                        transport_attempts=attempts,
                        backend_switches=backend_switches,
                        deadline_reason=None,
                        deferred_by_circuit=True,
                    ) from error
                continue

            active_request = base_request
            backend_attempt = 0
            first_attempt_timeout: float | None = None
            schema_correction_used = False
            backend_failure: BackendError | None = None

            while attempts < budget.max_transport_attempts:
                now = self.monotonic()
                attempt_timeout = min(
                    budget.request_timeout_seconds,
                    budget.stage_deadline_monotonic - now,
                    budget.total_deadline_monotonic - now,
                )
                if attempt_timeout <= 0:
                    last_deadline_reason = _deadline_reason(
                        now=now,
                        budget=budget,
                    )
                    backend_failure = self._deadline_failure(
                        backend,
                        request,
                    )
                    break
                if first_attempt_timeout is not None:
                    minimum_retry_window = min(
                        120.0,
                        first_attempt_timeout / 2.0,
                    )
                    if attempt_timeout < minimum_retry_window:
                        last_deadline_reason = "INSUFFICIENT_RETRY_WINDOW"
                        break
                else:
                    first_attempt_timeout = attempt_timeout

                budget.ledger.reserve_transport_attempt()
                attempts += 1
                backend_attempt += 1
                attempt_started_mono = self.monotonic()
                attempt_started_utc = self.utc_now()
                response: BackendResponse | None = None
                failure: BackendError | None = None
                value: T | None = None
                normalizations: tuple[
                    StructuredOutputNormalization, ...
                ] = ()
                try:
                    response = backend.exchange(
                        active_request,
                        timeout_seconds=attempt_timeout,
                    )
                    if (
                        response.backend_id != backend.backend_id
                        or response.model != backend.model
                    ):
                        raise BackendError(
                            kind=BackendFailureKind.SCHEMA_INVALID,
                            backend_id=backend.backend_id,
                            model=backend.model,
                            purpose=request.purpose,
                            detail="backend response identity does not match request",
                            retryable=False,
                        )
                    try:
                        if output_normalizer is None:
                            value = schema.model_validate_json(
                                response.output_text
                            )
                        else:
                            value, normalizations = output_normalizer(
                                response.output_text
                            )
                    except Exception as error:
                        raise BackendError(
                            kind=BackendFailureKind.SCHEMA_INVALID,
                            backend_id=backend.backend_id,
                            model=backend.model,
                            purpose=request.purpose,
                            detail=_schema_validation_detail(schema, error),
                            retryable=False,
                            allows_schema_correction=True,
                            status_code=response.status_code,
                            request_id=response.request_id,
                        ) from error
                except BackendError as error:
                    failure = error

                attempt_finished_mono = self.monotonic()
                attempt_finished_utc = self.utc_now()
                trace.record(
                    ModelAttemptTrace(
                        purpose=request.purpose,
                        backend_id=backend.backend_id,
                        model=backend.model,
                        request_hash=request_hash,
                        logical_request_id=logical_request_id,
                        transport_attempt=attempts,
                        backend_ordinal=backend_index + 1,
                        backend_attempt=backend_attempt,
                        switch_reason=(
                            switch_reason if backend_attempt == 1 else None
                        ),
                        started_at=attempt_started_utc,
                        finished_at=attempt_finished_utc,
                        elapsed_seconds=max(
                            attempt_finished_mono - attempt_started_mono,
                            0,
                        ),
                        request_bytes=len(request_bytes),
                        output_bytes=(
                            response.output_bytes if response is not None else 0
                        ),
                        status_code=(
                            response.status_code
                            if response is not None
                            else (failure.status_code if failure else None)
                        ),
                        request_id=(
                            response.request_id
                            if response is not None
                            else (failure.request_id if failure else None)
                        ),
                        error_code=(failure.kind.value if failure else None),
                        retryable=(failure.retryable if failure else None),
                        partial_output_bytes=(
                            failure.partial_output_bytes if failure else 0
                        ),
                        deadline_reason=(
                            "REQUEST_TIMEOUT"
                            if failure is not None
                            and failure.request_timed_out
                            else None
                        ),
                        normalizations=normalizations,
                    )
                )

                if failure is None:
                    assert value is not None
                    self.circuit_breaker.record_success(key)
                    return ModelResult(
                        value=value,
                        logical_request_id=logical_request_id,
                        backend_id=backend.backend_id,
                        model=backend.model,
                        transport_attempts=attempts,
                        backend_switches=backend_switches,
                        elapsed_seconds=max(self.monotonic() - started, 0),
                    )

                backend_failure = failure
                last_failure = failure
                if failure.request_timed_out:
                    last_deadline_reason = "REQUEST_TIMEOUT"
                if failure.kind == BackendFailureKind.POLICY_REJECTED:
                    self.circuit_breaker.record_failure(key, failure.kind)
                    raise GatewayRequestError(
                        failure=failure,
                        logical_request_id=logical_request_id,
                        transport_attempts=attempts,
                        backend_switches=backend_switches,
                        deadline_reason=last_deadline_reason,
                    )

                if (
                    failure.kind == BackendFailureKind.SCHEMA_INVALID
                    and failure.allows_schema_correction
                    and not schema_correction_used
                    and attempts < budget.max_transport_attempts
                ):
                    schema_correction_used = True
                    active_request = _corrected_request(
                        base_request,
                        detail=failure.detail,
                    )
                    continue

                if failure.kind in {
                    BackendFailureKind.BACKEND_UNAVAILABLE,
                    BackendFailureKind.BACKEND_MISCONFIGURED,
                    BackendFailureKind.AUTH_FAILED,
                    BackendFailureKind.SCHEMA_INVALID,
                }:
                    break

                delay = _retry_delay(failure, backend_attempt)
                if delay is None or attempts >= budget.max_transport_attempts:
                    break

                remaining_candidates = len(self._candidates) - backend_index - 1
                attempts_left = budget.max_transport_attempts - attempts
                if (
                    self.mode == BackendMode.NORMAL
                    and remaining_candidates > 0
                    and attempts_left <= remaining_candidates
                ):
                    break

                now = self.monotonic()
                remaining_deadline = min(
                    budget.stage_deadline_monotonic - now,
                    budget.total_deadline_monotonic - now,
                )
                if delay >= remaining_deadline:
                    last_deadline_reason = _deadline_reason(
                        now=now,
                        budget=budget,
                    )
                    break
                self.sleep(delay)

            if backend_failure is not None:
                last_failure = backend_failure
                self.circuit_breaker.record_failure(
                    key,
                    backend_failure.kind,
                )
                switch_reason = backend_failure.kind.value
            if self.mode == BackendMode.QUALIFICATION:
                break
            if attempts >= budget.max_transport_attempts:
                break

        if last_failure is None:
            last_failure = self._deadline_failure(
                self._candidates[-1],
                request,
            )
            last_deadline_reason = last_deadline_reason or _deadline_reason(
                now=self.monotonic(),
                budget=budget,
            )
        raise GatewayRequestError(
            failure=last_failure,
            logical_request_id=logical_request_id,
            transport_attempts=attempts,
            backend_switches=backend_switches,
            deadline_reason=last_deadline_reason,
            deferred_by_circuit=(
                attempts == 0 and deferred_count == len(self._candidates)
            ),
        )
