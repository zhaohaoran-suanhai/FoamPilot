"""Strict, Qt-independent activity event contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivityKind(StrEnum):
    STAGE = "stage"
    COMMAND = "command"
    HEARTBEAT = "heartbeat"
    LOG = "log"
    METRIC = "metric"
    WARNING = "warning"


class ActivityState(StrEnum):
    STARTED = "started"
    ALIVE = "alive"
    PROGRESSED = "progressed"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ActivitySource(StrEnum):
    TASK_BUILDER = "task_builder"
    MODEL = "model"
    RUNNER = "runner"
    VALIDATOR = "validator"
    WORKFLOW = "workflow"


class ActivityEvent(StrictModel):
    """One safe operational observation; never a CFD success verdict."""

    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=255)
    kind: ActivityKind
    state: ActivityState
    source: ActivitySource
    occurred_at: datetime
    elapsed_seconds: float = Field(default=0, ge=0)
    deadline_seconds: float | None = Field(default=None, gt=0)
    attempt: int | None = Field(default=None, ge=1)
    stage: str | None = Field(default=None, max_length=128)
    step_id: str | None = Field(default=None, max_length=255)
    pid: int | None = Field(default=None, ge=1)
    detail_code: str | None = Field(default=None, max_length=128)
    message: str = Field(default="", max_length=480)
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    evidence_path: str | None = Field(default=None, max_length=1024)
    evidence_offset: int | None = Field(default=None, ge=0)


__all__ = [
    "ActivityEvent",
    "ActivityKind",
    "ActivitySource",
    "ActivityState",
]
