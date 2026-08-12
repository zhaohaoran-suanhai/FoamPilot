"""Thread-safe creation and fan-out of operational activity events."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import re
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from foampilot.evidence import MetricsWriter

if TYPE_CHECKING:
    from foampilot.evidence.telemetry import ResidualMetric

from .models import (
    ActivityEvent,
    ActivityKind,
    ActivitySource,
    ActivityState,
)
from .sinks import JsonlActivitySink


ActivityListener = Callable[[ActivityEvent], None]
CancellationCheck = Callable[[], bool]


class OperationCancelled(RuntimeError):
    """The owning local job requested cancellation at a safe boundary."""

    def __init__(self, message: str = "operation cancelled by user") -> None:
        super().__init__(message)
        self.code = "OPERATION_CANCELLED"

_SECRET_PATTERNS = (
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)
_SENSITIVE_METRIC_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "auth_token",
        "authorization",
        "output_text",
        "password",
        "prompt",
        "request_body",
        "response",
        "response_body",
        "system_prompt",
        "user_prompt",
    }
)
_ALLOWED_METRIC_KEYS = frozenset(
    {
        "backend_id",
        "field",
        "final_residual",
        "initial_residual",
        "iteration",
        "model",
        "new_bytes",
        "output_bytes",
        "simulation_time",
        "solver_iterations",
        "transport_attempt",
    }
)


def _safe_message(value: str) -> str:
    text = " ".join(value.split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text if len(text) <= 480 else text[:477] + "..."


def _safe_metrics(
    values: dict[str, float | int | str] | None,
) -> dict[str, float | int | str]:
    if not values:
        return {}
    sanitized: dict[str, float | int | str] = {}
    for key, value in values.items():
        normalized_key = key.casefold()
        if (
            normalized_key in _SENSITIVE_METRIC_KEYS
            or normalized_key not in _ALLOWED_METRIC_KEYS
        ):
            continue
        sanitized[normalized_key] = (
            _safe_message(value) if isinstance(value, str) else value
        )
    return sanitized


class ActivityReporter:
    """Own one operation sequence and isolate execution from sink failures."""

    def __init__(
        self,
        *,
        operation_id: str,
        listeners: Iterable[ActivityListener] = (),
        critical_listeners: Iterable[ActivityListener] = (),
        utc_now: Callable[[], datetime] | None = None,
        cancel_requested: CancellationCheck | None = None,
        metric_heartbeat_seconds: float = 5.0,
    ) -> None:
        if not operation_id:
            raise ValueError("operation_id must not be empty")
        self.operation_id = operation_id
        self._listeners = list(listeners)
        self._critical_listeners = list(critical_listeners)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._cancel_requested = cancel_requested
        if metric_heartbeat_seconds <= 0:
            raise ValueError("metric heartbeat interval must be positive")
        self._metric_heartbeat_seconds = metric_heartbeat_seconds
        self._last_metric_heartbeat_elapsed: dict[str, float] = {}
        self._metrics_writer: MetricsWriter | None = None
        self._sequence = 0
        self._run_id: str | None = None
        self._degradation_messages: list[str] = []
        self._lock = RLock()

    @property
    def cancellation_enabled(self) -> bool:
        return self._cancel_requested is not None

    def is_cancel_requested(self) -> bool:
        check = self._cancel_requested
        if check is None:
            return False
        try:
            return bool(check())
        except Exception as error:
            with self._lock:
                detail = f"cancellation check failed: {type(error).__name__}: {error}"
                if detail not in self._degradation_messages:
                    self._degradation_messages.append(detail)
            return False

    def raise_if_cancelled(self) -> None:
        if self.is_cancel_requested():
            raise OperationCancelled()

    @property
    def degraded(self) -> bool:
        with self._lock:
            return bool(self._degradation_messages)

    @property
    def degradation_messages(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._degradation_messages)

    def add_listener(self, listener: ActivityListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def bind_run(
        self,
        run_id: str,
        path: str | Path,
        *,
        metrics_path: str | Path | None = None,
        metrics_sample_interval_seconds: float = 0.2,
        metrics_max_points_per_series: int = 500,
    ) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        with self._lock:
            if self._run_id is not None and self._run_id != run_id:
                raise ValueError("activity reporter is already bound to another run")
            self._run_id = run_id
            self._listeners.append(JsonlActivitySink(path))
            self._metrics_writer = MetricsWriter(
                metrics_path or Path(path).with_name("metrics.jsonl"),
                sample_interval_seconds=metrics_sample_interval_seconds,
                max_points_per_series=metrics_max_points_per_series,
            )

    def emit_solver_metric(
        self,
        *,
        metric: "ResidualMetric",
        elapsed_seconds: float,
        attempt: int | None,
        stage: str,
        step_id: str,
        pid: int,
    ) -> None:
        """Write live numbers separately and emit only bounded heartbeats."""

        with self._lock:
            occurred_at = self._utc_now()
            writer = self._metrics_writer
            if writer is not None:
                for series, value in metric.series_values().items():
                    try:
                        writer.write(
                            occurred_at=occurred_at,
                            attempt=attempt,
                            step_id=step_id,
                            simulation_time=metric.simulation_time,
                            series=series,
                            value=value,
                        )
                    except Exception as error:
                        detail = f"{type(error).__name__}: {error}"
                        if detail not in self._degradation_messages:
                            self._degradation_messages.append(detail)
            last = self._last_metric_heartbeat_elapsed.get(step_id)
            if last is not None and (
                elapsed_seconds - last < self._metric_heartbeat_seconds
            ):
                return
            self._last_metric_heartbeat_elapsed[step_id] = elapsed_seconds
        self.emit(
            kind="heartbeat",
            state="alive",
            source="runner",
            elapsed_seconds=elapsed_seconds,
            attempt=attempt,
            stage=stage,
            step_id=step_id,
            pid=pid,
            message="OpenFOAM solver is producing metrics",
        )

    def emit(
        self,
        *,
        kind: ActivityKind | str,
        state: ActivityState | str,
        source: ActivitySource | str,
        elapsed_seconds: float = 0,
        deadline_seconds: float | None = None,
        attempt: int | None = None,
        stage: str | None = None,
        step_id: str | None = None,
        pid: int | None = None,
        detail_code: str | None = None,
        message: str = "",
        metrics: dict[str, float | int | str] | None = None,
        evidence_path: str | None = None,
        evidence_offset: int | None = None,
    ) -> ActivityEvent:
        with self._lock:
            self._sequence += 1
            event = ActivityEvent(
                sequence=self._sequence,
                operation_id=self.operation_id,
                run_id=self._run_id,
                kind=kind,
                state=state,
                source=source,
                occurred_at=self._utc_now(),
                elapsed_seconds=max(elapsed_seconds, 0),
                deadline_seconds=deadline_seconds,
                attempt=attempt,
                stage=stage,
                step_id=step_id,
                pid=pid,
                detail_code=detail_code,
                message=_safe_message(message),
                metrics=_safe_metrics(metrics),
                evidence_path=evidence_path,
                evidence_offset=evidence_offset,
            )
            critical_listeners = tuple(self._critical_listeners)
            listeners = tuple(self._listeners)
            for listener in critical_listeners:
                listener(event)
            for listener in listeners:
                try:
                    listener(event)
                except Exception as error:  # observability must not fake CFD failure
                    detail = f"{type(error).__name__}: {error}"
                    if detail not in self._degradation_messages:
                        self._degradation_messages.append(detail)
        return event


__all__ = [
    "ActivityListener",
    "ActivityReporter",
    "CancellationCheck",
    "OperationCancelled",
]
