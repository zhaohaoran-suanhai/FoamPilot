"""Strict, auditable performance evidence contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PathKind = Literal["cold", "warm_plan", "warm_mesh", "repair_reuse"]
ReuseState = Literal["miss", "hit", "disabled"]
RerunStage = Literal["mesh", "initialize", "solve", "postprocess"]


class PerformanceStages(StrictModel):
    environment_seconds: float | None = Field(default=0.0, ge=0)
    geometry_seconds: float | None = Field(default=0.0, ge=0)
    routing_seconds: float | None = Field(default=0.0, ge=0)
    context_seconds: float | None = Field(default=0.0, ge=0)
    generation_seconds: float | None = Field(default=0.0, ge=0)
    materialization_seconds: float | None = Field(default=0.0, ge=0)
    inspection_seconds: float | None = Field(default=0.0, ge=0)
    mesh_seconds: float | None = Field(default=0.0, ge=0)
    initialization_seconds: float | None = Field(default=0.0, ge=0)
    solver_seconds: float | None = Field(default=0.0, ge=0)
    postprocess_seconds: float | None = Field(default=0.0, ge=0)
    validation_seconds: float | None = Field(default=0.0, ge=0)
    repair_model_seconds: float | None = Field(default=0.0, ge=0)


class ModelPerformance(StrictModel):
    logical_requests: int = Field(default=0, ge=0)
    transport_attempts: int = Field(default=0, ge=0)
    retry_delay_seconds: float = Field(default=0.0, ge=0)


class PerformanceReuse(StrictModel):
    plan: ReuseState = "disabled"
    geometry: ReuseState = "disabled"
    mesh: ReuseState = "disabled"
    repair_start_stage: RerunStage | None = None


class PerformanceSummary(StrictModel):
    schema_version: Literal[1] = 1
    path_kind: PathKind
    workflow_seconds_before_manifest: float | None = Field(default=None, ge=0)
    time_to_first_openfoam_command_seconds: float | None = Field(
        default=None,
        ge=0,
    )
    stages: PerformanceStages = Field(default_factory=PerformanceStages)
    model: ModelPerformance = Field(default_factory=ModelPerformance)
    reuse: PerformanceReuse = Field(default_factory=PerformanceReuse)
    diagnostics: list[str] = Field(default_factory=list)


class TaskBuilderPerformance(StrictModel):
    schema_version: Literal[1] = 1
    draft_id: str
    total_seconds: float = Field(ge=0)
    logical_requests: int = Field(ge=0)
    transport_attempts: int = Field(ge=0)
    retry_delay_seconds: float = Field(ge=0)
