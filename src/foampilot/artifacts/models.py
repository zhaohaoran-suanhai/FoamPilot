"""Stable summaries for the native OpenFOAM Agent path."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.workflow import (
    FailureRecord,
    ParentRun,
    ResumeMetadata,
    WorkflowState,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


NativeStatus = Literal[
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "MESH_QUALITY_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
    "PUBLIC_VALIDATION_PASS",
]


AttemptStatus = Literal[
    "BLOCKED_ENVIRONMENT",
    "CASE_GENERATION_FAILED",
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "MESH_QUALITY_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
    "PUBLIC_VALIDATION_PASS",
]

# Kept as an import alias for callers that only type native attempt results.
NativeAgentStatus = AttemptStatus


class AttemptSummary(StrictModel):
    attempt: int = Field(ge=1)
    status: AttemptStatus
    failed_step_id: str | None = None
    failure_fingerprint: str | None = None
    changed_files: list[str] = Field(default_factory=list)


class RunSummary(StrictModel):
    schema_version: Literal[2] = 2
    task_id: str
    workflow_state: WorkflowState
    native_status: NativeStatus | None = None
    last_completed_stage: str | None = None
    attempts: list[AttemptSummary] = Field(default_factory=list)
    primary_failure: FailureRecord | None = None
    terminal_blocker: FailureRecord | None = None
    resume: ResumeMetadata
    parent_run: ParentRun | None = None
    message: str

    @property
    def status(self) -> str:
        if self.native_status is not None:
            return self.native_status
        if (
            self.primary_failure is not None
            and self.primary_failure.domain == "environment"
        ):
            return "BLOCKED_ENVIRONMENT"
        if self.primary_failure is not None and self.primary_failure.code in {
            "REQUEST_INCOMPLETE",
            "ROUTING_UNRESOLVED",
            "BLOCKED_ENVIRONMENT",
            "PLAN_INVALID",
            "GENERATION_INVALID",
            "CASE_GENERATION_FAILED",
            "PLAN_REUSE_REJECTED",
        }:
            return self.primary_failure.code
        return self.workflow_state.value


class NativeAgentOutcome(StrictModel):
    run_dir: Path
    summary: RunSummary

    @property
    def status(self) -> str:
        return self.summary.status
