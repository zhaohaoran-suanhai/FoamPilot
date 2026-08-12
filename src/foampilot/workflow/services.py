"""Domain-stage service contracts consumed by the workflow coordinator."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import FailureRecord, WorkflowStage


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactSink(Protocol):
    """Domain artifact sink that cannot mutate workflow state."""

    def write(self, name: str, payload: BaseModel | dict[str, Any]) -> str: ...


class WorkflowContext(_StrictFrozenModel):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    values: dict[str, Any] = Field(default_factory=dict)
    start_stage: WorkflowStage | None = None
    repair_cycles_used: int = Field(default=0, ge=0)
    max_repair_cycles: int = Field(default=1, ge=0)


class StageOutcome(_StrictFrozenModel):
    status: Literal["completed", "deferred", "failed", "cancelled"]
    checkpoint_name: str | None = None
    checkpoint_payload: dict[str, Any] | BaseModel | None = None
    detail: str = ""
    failure: FailureRecord | None = None
    artifact_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_contract(self) -> "StageOutcome":
        if self.status == "completed":
            if not self.checkpoint_name or self.checkpoint_payload is None:
                raise ValueError(
                    "completed stages require an immutable checkpoint"
                )
            if self.failure is not None:
                raise ValueError("completed stages cannot include failure")
        elif self.status in {"deferred", "failed"} and self.failure is None:
            raise ValueError(f"{self.status} stages require failure detail")
        return self


class StageService(Protocol):
    stage: WorkflowStage

    def run(self, context: WorkflowContext) -> StageOutcome: ...


class StageDescriptor(_StrictFrozenModel):
    stage: WorkflowStage
    input_artifacts: tuple[str, ...] = ()
    output_artifact: str
    resumable: bool = True


CANONICAL_STAGE_DESCRIPTORS: tuple[StageDescriptor, ...] = (
    StageDescriptor(
        stage=WorkflowStage.INGESTING_ASSETS,
        output_artifact="mesh-asset.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.INSPECTING_INPUT,
        input_artifacts=("mesh-asset.json",),
        output_artifact="mesh-facts.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.INTERPRETING_INTENT,
        input_artifacts=("mesh-facts.json",),
        output_artifact="simulation-intent.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.RESOLVING_REQUIREMENTS,
        input_artifacts=("simulation-intent.json",),
        output_artifact="requirements.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.DESIGNING_CASE,
        input_artifacts=("requirements.json",),
        output_artifact="case-design.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.PLANNING_OBSERVATIONS,
        input_artifacts=("case-design.json",),
        output_artifact="observation-plan.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.AUTHORING_CASE,
        input_artifacts=("case-design.json", "observation-plan.json"),
        output_artifact="case-bundle.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.VERIFYING_CASE,
        input_artifacts=("case-bundle.json",),
        output_artifact="verification-report.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.EXECUTING,
        input_artifacts=("verification-report.json",),
        output_artifact="raw-run-evidence.json",
        resumable=False,
    ),
    StageDescriptor(
        stage=WorkflowStage.EXTRACTING_EVIDENCE,
        input_artifacts=("raw-run-evidence.json",),
        output_artifact="run-facts.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.POST_PROCESSING,
        input_artifacts=("run-facts.json",),
        output_artifact="physics-metrics.json",
    ),
    StageDescriptor(
        stage=WorkflowStage.EVALUATING,
        input_artifacts=("run-facts.json", "physics-metrics.json"),
        output_artifact="acceptance-report.json",
    ),
)

CANONICAL_STAGE_ORDER: tuple[WorkflowStage, ...] = tuple(
    item.stage for item in CANONICAL_STAGE_DESCRIPTORS
)


__all__ = [
    "ArtifactSink",
    "CANONICAL_STAGE_DESCRIPTORS",
    "CANONICAL_STAGE_ORDER",
    "StageDescriptor",
    "StageOutcome",
    "StageService",
    "WorkflowContext",
]
