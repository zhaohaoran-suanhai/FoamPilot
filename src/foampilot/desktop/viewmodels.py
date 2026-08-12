"""Qt-independent, immutable view models for the desktop run inspector."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from foampilot.artifacts import RunSummary
from foampilot.workflow import WorkflowProjection


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )


class RunFileView(FrozenModel):
    path: str
    bytes: int
    category: Literal["case", "log", "report", "workflow", "other"]


class TimelineView(FrozenModel):
    sequence: int
    stage: str
    state: str
    attempt: int | None
    step_id: str | None
    detail: str


class KnowledgeReference(FrozenModel):
    stage: Literal["author", "repair"]
    attempt: int | None
    slot: str
    entry_id: str
    title: str | None
    knowledge_type: str | None
    source_locator: str | None
    source_sha256: str | None


class SkillReference(FrozenModel):
    stage: Literal["author", "repair"]
    attempt: int | None
    name: str


class ResidualSample(FrozenModel):
    attempt: int | None
    source_log: str
    sequence: int
    simulation_time: float | None
    field: str
    initial_residual: float
    final_residual: float | None


class RunSnapshot(FrozenModel):
    run_dir: Path
    summary: RunSummary | None
    timeline: tuple[TimelineView, ...]
    files: tuple[RunFileView, ...]
    manifest_state: Literal["verified", "invalid", "pending"]
    manifest_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    projection: WorkflowProjection
    context_references: tuple[KnowledgeReference, ...] = ()
    skill_references: tuple[SkillReference, ...] = ()
    residual_samples: tuple[ResidualSample, ...] = ()
    runtime_config: dict[str, object] | None = None
    runtime_provenance: dict[str, object] | None = None
    execution_risk: dict[str, object] | None = None
    execution_policy: dict[str, object] | None = None
    sandbox_probe: dict[str, object] | None = None
    design_questions: dict[str, object] | None = None
