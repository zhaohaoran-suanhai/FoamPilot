"""Strict persistent contracts for one local FoamPilot job."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobOperation(StrEnum):
    DRAFT = "draft"
    PLAN = "plan"
    SOLVE = "solve"
    RESUME = "resume"


class JobState(StrEnum):
    SUBMITTED = "SUBMITTED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class RecoveryState(StrEnum):
    RUNNING = "RUNNING"
    UNRESPONSIVE = "UNRESPONSIVE"
    ORPHANED_ACTIVE = "ORPHANED_ACTIVE"
    ORPHANED_STOPPED = "ORPHANED_STOPPED"
    FINALIZED = "FINALIZED"
    EVIDENCE_DAMAGED = "EVIDENCE_DAMAGED"


class RecoveryAction(StrEnum):
    ATTACH = "attach"
    INSPECT = "inspect"
    CANCEL = "cancel"
    TERMINATE_ORPHAN = "terminate_orphan"
    RECOVER_FINALIZE = "recover_finalize"
    STRICT_RESUME = "strict_resume"
    RERUN = "rerun"
    REPORT = "report"


class ProcessIdentity(StrictModel):
    pid: int = Field(ge=1)
    pgid: int = Field(ge=1)
    start_token: int = Field(ge=0)
    boot_id: str = Field(min_length=1, max_length=128)


class JobSpec(StrictModel):
    schema_version: Literal[1] = 1
    worker_protocol_version: Literal[1] = 1
    job_id: str = Field(pattern=r"^job-[A-Za-z0-9._-]+$")
    operation: JobOperation
    created_at: datetime
    project_root: Path
    arguments: tuple[str, ...] = Field(min_length=1)
    input_paths: tuple[str, ...] = ()
    input_sha256: dict[str, str] = Field(default_factory=dict)

    @field_validator("project_root")
    @classmethod
    def _absolute_project_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("project_root must be absolute")
        return value.resolve()

    @model_validator(mode="after")
    def _input_hashes_match(self) -> "JobSpec":
        if set(self.input_paths) != set(self.input_sha256):
            raise ValueError("input paths and hashes do not match")
        for digest in self.input_sha256.values():
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("input hash must be lowercase SHA256")
        return self


class JobStatus(StrictModel):
    schema_version: Literal[1] = 1
    job_id: str
    revision: int = Field(ge=1)
    state: JobState
    worker: ProcessIdentity | None = None
    current_child: ProcessIdentity | None = None
    current_stage: str | None = Field(default=None, max_length=128)
    current_step_id: str | None = Field(default=None, max_length=255)
    run_dir: str | None = None
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    terminal_code: str | None = Field(default=None, max_length=128)


class CancelRequest(StrictModel):
    schema_version: Literal[1] = 1
    job_id: str
    requested_at: datetime
    requested_by: str = Field(min_length=1, max_length=128)


class RecoveryDecision(StrictModel):
    schema_version: Literal[1] = 1
    job_id: str
    state: RecoveryState
    code: str = Field(min_length=1, max_length=128)
    reason_zh: str = Field(min_length=1)
    recovery_zh: str = Field(min_length=1)
    allowed_actions: tuple[RecoveryAction, ...] = ()
    worker_alive: bool
    child_alive: bool
    writer_lock_held: bool
    run_dir: Path | None = None
    manifest_issues: tuple[str, ...] = ()


__all__ = [
    "CancelRequest",
    "JobOperation",
    "JobSpec",
    "JobState",
    "JobStatus",
    "ProcessIdentity",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryState",
]
