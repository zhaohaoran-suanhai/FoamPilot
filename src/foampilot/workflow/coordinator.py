"""Declarative workflow transitions with no CFD-domain interpretation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .events import WorkflowEvent
from .models import (
    FailureDomain,
    FailureRecord,
    WorkflowEventState,
    WorkflowStage,
    WorkflowState,
)
from .services import (
    CANONICAL_STAGE_ORDER,
    StageOutcome,
    StageService,
    WorkflowContext,
)
from .store import WorkflowStore


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkflowCoordinatorSummary(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str
    task_id: str
    workflow_state: WorkflowState
    current_stage: WorkflowStage
    completed_stages: tuple[WorkflowStage, ...]
    failure: FailureRecord | None = None


class WorkflowCoordinatorOutcome(_StrictFrozenModel):
    summary: WorkflowCoordinatorSummary


class WorkflowCoordinator:
    """Advance declared stages and persist transitions after checkpoints."""

    def __init__(
        self,
        *,
        store: WorkflowStore,
        cancellation_requested: Callable[[], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.cancellation_requested = cancellation_requested or (lambda: False)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _record(
        self,
        stage: WorkflowStage,
        state: WorkflowEventState,
        *,
        context: WorkflowContext,
        detail: str = "",
        evidence_paths: tuple[str, ...] = (),
    ) -> None:
        self.store.record(
            WorkflowEvent(
                sequence=self.store.next_sequence,
                stage=stage,
                state=state,
                occurred_at=self.now(),
                attempt=context.attempt,
                detail=detail,
                evidence_paths=list(evidence_paths),
            )
        )

    @staticmethod
    def _required_order(context: WorkflowContext) -> tuple[WorkflowStage, ...]:
        if context.start_stage is None:
            return CANONICAL_STAGE_ORDER
        try:
            start = CANONICAL_STAGE_ORDER.index(context.start_stage)
        except ValueError as error:
            raise ValueError("start_stage is not resumable") from error
        return CANONICAL_STAGE_ORDER[start:]

    def _finalize(
        self,
        *,
        context: WorkflowContext,
        state: WorkflowState,
        completed: tuple[WorkflowStage, ...],
        failure: FailureRecord | None,
        detail: str,
    ) -> WorkflowCoordinatorOutcome:
        event_state = {
            WorkflowState.COMPLETED: WorkflowEventState.COMPLETED,
            WorkflowState.FAILED: WorkflowEventState.FAILED,
            WorkflowState.DEFERRED: WorkflowEventState.DEFERRED,
            WorkflowState.CANCELLED: WorkflowEventState.CANCELLED,
            WorkflowState.INTERRUPTED: WorkflowEventState.INTERRUPTED,
        }[state]
        self._record(
            WorkflowStage.RUN_FINALIZED,
            event_state,
            context=context,
            detail=detail,
            evidence_paths=(tuple(failure.evidence_paths) if failure else ()),
        )
        summary = WorkflowCoordinatorSummary(
            run_id=context.run_id,
            task_id=context.task_id,
            workflow_state=state,
            current_stage=WorkflowStage.RUN_FINALIZED,
            completed_stages=completed,
            failure=failure,
        )
        self.store.finish(summary)
        return WorkflowCoordinatorOutcome(summary=summary)

    def advance(
        self,
        context: WorkflowContext,
        service: StageService,
    ) -> StageOutcome:
        """Run one domain service while owning its workflow transitions."""

        if self.cancellation_requested():
            outcome = StageOutcome(
                status="cancelled",
                detail="cancellation requested before the next stage",
                failure=FailureRecord(
                    domain=FailureDomain.WORKFLOW,
                    code="USER_CANCELLED",
                    detail="cancellation requested before the next stage",
                ),
            )
            self._record(
                service.stage,
                WorkflowEventState.CANCELLED,
                context=context,
                detail=outcome.detail,
            )
            return outcome
        self._record(
            service.stage,
            WorkflowEventState.STARTED,
            context=context,
        )
        try:
            outcome = service.run(context)
        except Exception as error:
            outcome = StageOutcome(
                status="failed",
                detail=(
                    f"{type(error).__name__}: stage service raised an exception"
                ),
                failure=FailureRecord(
                    domain=FailureDomain.WORKFLOW,
                    code="STAGE_SERVICE_FAILED",
                    detail=(
                        f"{type(error).__name__}: stage service raised an exception"
                    ),
                ),
            )
        state = {
            "completed": WorkflowEventState.COMPLETED,
            "deferred": WorkflowEventState.DEFERRED,
            "failed": WorkflowEventState.FAILED,
            "cancelled": WorkflowEventState.CANCELLED,
        }[outcome.status]
        if outcome.status == "completed":
            self.store.checkpoint(
                outcome.checkpoint_name or service.stage.value.lower(),
                outcome.checkpoint_payload or {},
            )
        self._record(
            service.stage,
            state,
            context=context,
            detail=outcome.detail,
            evidence_paths=outcome.artifact_paths,
        )
        return outcome

    def run(
        self,
        context: WorkflowContext,
        services: Sequence[StageService],
    ) -> WorkflowCoordinatorOutcome:
        required = self._required_order(context)
        actual = tuple(service.stage for service in services)
        if actual != required:
            raise ValueError("services must match canonical stage order")
        if context.repair_cycles_used > context.max_repair_cycles:
            failure = FailureRecord(
                domain=FailureDomain.WORKFLOW,
                code="REPAIR_BUDGET_EXHAUSTED",
                detail="repair cycle budget was exhausted before execution",
            )
            return self._finalize(
                context=context,
                state=WorkflowState.FAILED,
                completed=(),
                failure=failure,
                detail=failure.detail,
            )

        completed: list[WorkflowStage] = []
        for service in services:
            outcome = self.advance(context, service)
            if outcome.status == "completed":
                completed.append(service.stage)
                continue

            state = {
                "deferred": WorkflowState.DEFERRED,
                "failed": WorkflowState.FAILED,
                "cancelled": WorkflowState.CANCELLED,
            }[outcome.status]
            failure = outcome.failure or FailureRecord(
                domain=FailureDomain.WORKFLOW,
                code=(
                    "USER_CANCELLED"
                    if outcome.status == "cancelled"
                    else "STAGE_DID_NOT_COMPLETE"
                ),
                detail=outcome.detail or "stage did not complete",
            )
            return self._finalize(
                context=context,
                state=state,
                completed=tuple(completed),
                failure=failure,
                detail=outcome.detail or failure.detail,
            )

        return self._finalize(
            context=context,
            state=WorkflowState.COMPLETED,
            completed=tuple(completed),
            failure=None,
            detail="workflow completed",
        )


__all__ = [
    "WorkflowCoordinator",
    "WorkflowCoordinatorOutcome",
    "WorkflowCoordinatorSummary",
]
