"""Thread-safe creation and fan-out of operational activity events."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import re
from pathlib import Path
from threading import RLock

from .models import (
    ActivityEvent,
    ActivityKind,
    ActivitySource,
    ActivityState,
)
from .sinks import JsonlActivitySink


ActivityListener = Callable[[ActivityEvent], None]

_SECRET_PATTERNS = (
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def _safe_message(value: str) -> str:
    text = " ".join(value.split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text if len(text) <= 480 else text[:477] + "..."


class ActivityReporter:
    """Own one operation sequence and isolate execution from sink failures."""

    def __init__(
        self,
        *,
        operation_id: str,
        listeners: Iterable[ActivityListener] = (),
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not operation_id:
            raise ValueError("operation_id must not be empty")
        self.operation_id = operation_id
        self._listeners = list(listeners)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._sequence = 0
        self._run_id: str | None = None
        self._degradation_messages: list[str] = []
        self._lock = RLock()

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

    def bind_run(self, run_id: str, path: str | Path) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        with self._lock:
            if self._run_id is not None and self._run_id != run_id:
                raise ValueError("activity reporter is already bound to another run")
            self._run_id = run_id
            self._listeners.append(JsonlActivitySink(path))

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
                metrics=metrics or {},
                evidence_path=evidence_path,
                evidence_offset=evidence_offset,
            )
            listeners = tuple(self._listeners)
            for listener in listeners:
                try:
                    listener(event)
                except Exception as error:  # observability must not fake CFD failure
                    detail = f"{type(error).__name__}: {error}"
                    if detail not in self._degradation_messages:
                        self._degradation_messages.append(detail)
        return event


__all__ = ["ActivityListener", "ActivityReporter"]
