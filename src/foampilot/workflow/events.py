"""Append-only workflow event contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import WorkflowEventState, WorkflowStage


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    stage: WorkflowStage
    state: WorkflowEventState
    occurred_at: datetime
    attempt: int | None = Field(default=None, ge=1)
    step_id: str | None = None
    detail: str = ""
    evidence_paths: list[str] = Field(default_factory=list)

    @classmethod
    def started(
        cls,
        *,
        stage: WorkflowStage,
        sequence: int,
        occurred_at: datetime,
        attempt: int | None = None,
        step_id: str | None = None,
        detail: str = "",
        evidence_paths: list[str] | None = None,
    ) -> "WorkflowEvent":
        return cls(
            sequence=sequence,
            stage=stage,
            state=WorkflowEventState.STARTED,
            occurred_at=occurred_at,
            attempt=attempt,
            step_id=step_id,
            detail=detail,
            evidence_paths=evidence_paths or [],
        )

    @classmethod
    def completed(
        cls,
        *,
        stage: WorkflowStage,
        sequence: int,
        occurred_at: datetime,
        attempt: int | None = None,
        step_id: str | None = None,
        detail: str = "",
        evidence_paths: list[str] | None = None,
    ) -> "WorkflowEvent":
        return cls(
            sequence=sequence,
            stage=stage,
            state=WorkflowEventState.COMPLETED,
            occurred_at=occurred_at,
            attempt=attempt,
            step_id=step_id,
            detail=detail,
            evidence_paths=evidence_paths or [],
        )
