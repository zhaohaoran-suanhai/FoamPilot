"""Coherent finite model budgets for the native cold path."""

from __future__ import annotations

from dataclasses import dataclass

from foampilot.models import (
    ModelBudgetLedger,
    ModelBudgetWindow,
    ModelStage,
    NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT,
)


@dataclass(frozen=True)
class NativeModelStagePolicy:
    request_timeout_seconds: float
    stage_deadline_seconds: float
    max_transport_attempts: int
    retry_margin_seconds: float = 0.0

    @property
    def full_retry_window_seconds(self) -> float:
        if self.max_transport_attempts == 1:
            return self.request_timeout_seconds
        return (
            self.request_timeout_seconds * self.max_transport_attempts
            + self.retry_margin_seconds
        )

    def open(
        self,
        ledger: ModelBudgetLedger,
        stage: ModelStage,
    ) -> ModelBudgetWindow:
        return ledger.open_stage(
            stage,
            request_timeout_seconds=self.request_timeout_seconds,
            stage_deadline_seconds=self.stage_deadline_seconds,
            max_transport_attempts=self.max_transport_attempts,
        )


ROUTING_MODEL_POLICY = NativeModelStagePolicy(60, 60, 1)
INTENT_MODEL_POLICY = NativeModelStagePolicy(300, 615, 2, 15)
DESIGN_MODEL_POLICY = NativeModelStagePolicy(300, 615, 2, 15)
AUTHOR_MODEL_POLICY = NativeModelStagePolicy(420, 855, 2, 15)
REPAIR_MODEL_POLICY = NativeModelStagePolicy(300, 615, 2, 15)

NATIVE_MODEL_TOTAL_DEADLINE_SECONDS = 3000
__all__ = [
    "AUTHOR_MODEL_POLICY",
    "DESIGN_MODEL_POLICY",
    "INTENT_MODEL_POLICY",
    "NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT",
    "NATIVE_MODEL_TOTAL_DEADLINE_SECONDS",
    "NativeModelStagePolicy",
    "REPAIR_MODEL_POLICY",
    "ROUTING_MODEL_POLICY",
]
