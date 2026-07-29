"""Stable summaries for the native OpenFOAM Agent path."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


NativeAgentStatus = Literal[
    "REQUEST_INCOMPLETE",
    "BLOCKED_ENVIRONMENT",
    "PLAN_INVALID",
    "CASE_GENERATION_FAILED",
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
    "PUBLIC_VALIDATION_PASS",
]


class AttemptSummary(StrictModel):
    attempt: int = Field(ge=1)
    status: NativeAgentStatus
    failed_step_id: str | None = None
    failure_fingerprint: str | None = None
    changed_files: list[str] = Field(default_factory=list)


class RunSummary(StrictModel):
    schema_version: Literal[1] = 1
    task_id: str
    status: NativeAgentStatus
    attempts: list[AttemptSummary] = Field(default_factory=list)
    message: str


class NativeAgentOutcome(StrictModel):
    status: NativeAgentStatus
    run_dir: Path
    summary: RunSummary
