"""Stable workflow stages and event states."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowStage(StrEnum):
    INGESTING_ASSETS = "INGESTING_ASSETS"
    INSPECTING_INPUT = "INSPECTING_INPUT"
    PLANNING_OBSERVATIONS = "PLANNING_OBSERVATIONS"
    AUTHORING_CASE = "AUTHORING_CASE"
    VERIFYING_CASE = "VERIFYING_CASE"
    EXECUTING = "EXECUTING"
    EXTRACTING_EVIDENCE = "EXTRACTING_EVIDENCE"
    POST_PROCESSING = "POST_PROCESSING"
    EVALUATING = "EVALUATING"
    TASK_VALIDATED = "TASK_VALIDATED"
    ENVIRONMENT_READY = "ENVIRONMENT_READY"
    GEOMETRY_READY = "GEOMETRY_READY"
    ROUTING_READY = "ROUTING_READY"
    CONTEXT_READY = "CONTEXT_READY"
    INTERPRETING_INTENT = "INTERPRETING_INTENT"
    RESOLVING_REQUIREMENTS = "RESOLVING_REQUIREMENTS"
    DESIGNING_CASE = "DESIGNING_CASE"
    WAITING_FOR_INFORMATION = "WAITING_FOR_INFORMATION"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    MODEL_GENERATION_STARTED = "MODEL_GENERATION_STARTED"
    PLAN_READY = "PLAN_READY"
    CASE_MATERIALIZED = "CASE_MATERIALIZED"
    STATIC_INSPECTION_COMPLETE = "STATIC_INSPECTION_COMPLETE"
    OPENFOAM_STEP_STARTED = "OPENFOAM_STEP_STARTED"
    OPENFOAM_STEP_COMPLETE = "OPENFOAM_STEP_COMPLETE"
    MESH_QUALITY_COMPLETE = "MESH_QUALITY_COMPLETE"
    PUBLIC_VALIDATION_COMPLETE = "PUBLIC_VALIDATION_COMPLETE"
    REPAIR_SCOPE_READY = "REPAIR_SCOPE_READY"
    MODEL_REPAIR_STARTED = "MODEL_REPAIR_STARTED"
    REPAIR_APPLIED = "REPAIR_APPLIED"
    RUN_FINALIZED = "RUN_FINALIZED"


class WorkflowEventState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class WorkflowState(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEFERRED = "DEFERRED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class FailureDomain(StrEnum):
    TASK = "task"
    ENVIRONMENT = "environment"
    BACKEND = "backend"
    DESIGN = "design"
    PLAN = "plan"
    CASE = "case"
    INSPECTION = "inspection"
    MESH = "mesh"
    INITIALIZATION = "initialization"
    SOLVER = "solver"
    POSTPROCESS = "postprocess"
    VALIDATION = "validation"
    WORKFLOW = "workflow"
    LEGACY = "legacy"


class FailureRecord(StrictModel):
    domain: FailureDomain
    code: str
    step_id: str | None = None
    retryable: bool = False
    detail: str
    message: str | None = None
    recovery: str | None = None
    evidence_paths: list[str] = Field(default_factory=list)


class ResumeMetadata(StrictModel):
    allowed: bool = False
    from_stage: WorkflowStage | None = None
    reason: str


class ParentRun(StrictModel):
    run_id: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResumeCompatibility(StrictModel):
    task_sha256: str
    public_assets_sha256: str | None = None
    model: str
    backend_id: str
    backend_policy_sha256: str
    runtime_policy_sha256: str
    package_version: str
    package_artifact_sha256: str
    git_revision: str | None = None
    execution_plan_schema: int = Field(ge=1)
    knowledge_ids: list[str] = Field(default_factory=list)
    knowledge_hash: str
    skill_ids: list[str] = Field(default_factory=list)
    skill_hash: str
    openfoam_target: dict[str, str]
    executable_names: list[str]
    executable_paths: dict[str, str]
    executable_identities: dict[str, str]

    @field_validator(
        "task_sha256",
        "public_assets_sha256",
        "backend_policy_sha256",
        "runtime_policy_sha256",
        "package_artifact_sha256",
        "knowledge_hash",
        "skill_hash",
    )
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("value must be a lowercase SHA256 digest")
        return value


class ResumeCompatibilityError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(
            f"strict resume rejected: {field} changed; "
            "use rerun_with_changes"
        )
        self.field = field
