"""Strict data contracts for reviewable FoamPilot learning candidates."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RootCause(StrEnum):
    ENVIRONMENT = "environment"
    TASK_SPEC = "task_spec"
    CASE_GENERATION = "case_generation"
    VERSION_CONTRACT = "version_contract"
    MESH = "mesh"
    INITIALIZATION = "initialization"
    NUMERICS = "numerics"
    PHYSICS_MODEL = "physics_model"
    EXECUTION = "execution"
    VALIDATION = "validation"
    EVALUATOR = "evaluator"


class ImprovementTarget(StrEnum):
    KNOWLEDGE = "knowledge"
    SKILL = "skill"
    PROMPT = "prompt"
    INSPECTION = "inspection"
    RUNNER = "runner"
    EVALUATOR = "evaluator"


class SourceRun(StrictModel):
    path: Path
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicEvidence(StrictModel):
    failure_fingerprints: list[str] = Field(default_factory=list)
    failed_steps: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


class OfficialExampleEvidence(StrictModel):
    used: bool = False
    source_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    extracted_principles: list[str] = Field(default_factory=list)


class LearningCandidate(StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_runs: list[SourceRun] = Field(min_length=1)
    root_cause: RootCause
    secondary_root_causes: list[RootCause] = Field(default_factory=list)
    public_evidence: PublicEvidence
    official_example: OfficialExampleEvidence
    generalized_lesson: str = Field(min_length=1)
    proposed_target: ImprovementTarget
    leakage_families: list[str] = Field(default_factory=list)
    development_cases: list[str] = Field(default_factory=list)
    regression_cases: list[str] = Field(default_factory=list)
    holdout_cases: list[str] = Field(default_factory=list)
    promotion_criteria: list[str] = Field(min_length=1)
    max_total_model_calls_delta: int = Field(default=0, ge=0)
    max_total_duration_ratio: float = Field(default=1.25, ge=1.0)

    @model_validator(mode="after")
    def validate_governance(self) -> Self:
        roles = (
            self.development_cases,
            self.regression_cases,
            self.holdout_cases,
        )
        flattened = [case for role in roles for case in role]
        if len(flattened) != len(set(flattened)):
            raise ValueError("case roles must be disjoint")

        example = self.official_example
        if example.used and (
            example.source_sha256 is None
            or not example.extracted_principles
            or not self.leakage_families
        ):
            raise ValueError(
                "official example use requires hash, principles, and leakage"
            )
        if not example.used and (
            example.source_sha256 is not None
            or example.extracted_principles
        ):
            raise ValueError(
                "unused official example cannot carry derived evidence"
            )
        if (
            self.root_cause == RootCause.ENVIRONMENT
            and self.proposed_target != ImprovementTarget.RUNNER
        ):
            raise ValueError(
                "environment candidates can only target the runner"
            )
        return self


class PromotionGate(StrictModel):
    name: str
    passed: bool
    detail: str


class PromotionCaseDelta(StrictModel):
    case_id: str
    role: Literal["source", "development", "regression", "holdout"]
    baseline_status: str
    current_status: str
    baseline_rank: int
    current_rank: int


class PromotionReport(StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    protocol_id: str
    model_name: str
    eligible: bool
    physics_pass_delta: int
    model_calls_delta: int
    duration_ratio: float
    gates: list[PromotionGate]
    cases: list[PromotionCaseDelta]
