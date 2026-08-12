"""Monotonic-clock budgets for one model run and its lineage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import threading
import time


class ModelStage(StrEnum):
    TASK_EXTRACTION = "task_extraction"
    INTENT_INTERPRETATION = "intent_interpretation"
    CASE_DESIGN = "case_design"
    GENERATION = "generation"
    REPAIR = "repair"
    ROUTING = "routing"


class LineageBudgetExhausted(RuntimeError):
    """No transport attempt may be sent for this lineage."""


@dataclass(frozen=True)
class ModelBudgetWindow:
    stage: ModelStage
    request_timeout_seconds: float
    stage_deadline_monotonic: float
    total_deadline_monotonic: float
    max_transport_attempts: int
    ledger: "ModelBudgetLedger"


class ModelBudgetLedger:
    """Thread-safe transport-attempt accounting for one run lineage."""

    def __init__(
        self,
        *,
        total_deadline_monotonic: float,
        lineage_transport_attempt_limit: int,
        transport_attempts_used: int,
    ) -> None:
        if lineage_transport_attempt_limit < 1:
            raise ValueError(
                "lineage_transport_attempt_limit must be positive"
            )
        if not 0 <= transport_attempts_used <= (
            lineage_transport_attempt_limit
        ):
            raise ValueError("transport_attempts_used is outside the limit")
        self.total_deadline_monotonic = total_deadline_monotonic
        self.lineage_transport_attempt_limit = (
            lineage_transport_attempt_limit
        )
        self._transport_attempts_used = transport_attempts_used
        self._lock = threading.Lock()

    @classmethod
    def start(
        cls,
        *,
        total_model_deadline_seconds: float = 600,
        lineage_transport_attempt_limit: int = 7,
        transport_attempts_used: int = 0,
        now: Callable[[], float] = time.monotonic,
    ) -> "ModelBudgetLedger":
        if total_model_deadline_seconds <= 0:
            raise ValueError(
                "total_model_deadline_seconds must be positive"
            )
        return cls(
            total_deadline_monotonic=(
                now() + total_model_deadline_seconds
            ),
            lineage_transport_attempt_limit=(
                lineage_transport_attempt_limit
            ),
            transport_attempts_used=transport_attempts_used,
        )

    @property
    def transport_attempts_used(self) -> int:
        with self._lock:
            return self._transport_attempts_used

    @property
    def transport_attempts_remaining(self) -> int:
        """Return the lineage transport budget without reserving an attempt."""

        with self._lock:
            return max(
                0,
                self.lineage_transport_attempt_limit
                - self._transport_attempts_used,
            )

    def total_seconds_remaining(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> float:
        """Return a monotonic, non-negative view of the total model budget."""

        return max(0.0, self.total_deadline_monotonic - now())

    def open_stage(
        self,
        stage: ModelStage,
        *,
        request_timeout_seconds: float = 300,
        stage_deadline_seconds: float,
        max_transport_attempts: int = 3,
        now: Callable[[], float] = time.monotonic,
    ) -> ModelBudgetWindow:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if stage_deadline_seconds <= 0:
            raise ValueError("stage_deadline_seconds must be positive")
        if max_transport_attempts < 1:
            raise ValueError("max_transport_attempts must be positive")
        return ModelBudgetWindow(
            stage=stage,
            request_timeout_seconds=request_timeout_seconds,
            stage_deadline_monotonic=now() + stage_deadline_seconds,
            total_deadline_monotonic=self.total_deadline_monotonic,
            max_transport_attempts=max_transport_attempts,
            ledger=self,
        )

    def reserve_transport_attempt(self) -> int:
        with self._lock:
            if self._transport_attempts_used >= (
                self.lineage_transport_attempt_limit
            ):
                raise LineageBudgetExhausted(
                    "lineage transport-attempt budget is exhausted"
                )
            self._transport_attempts_used += 1
            return self._transport_attempts_used
