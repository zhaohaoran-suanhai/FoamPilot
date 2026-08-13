"""Auditable natural-language task drafting contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from foampilot.tasks import PublicAsset, TaskSpec

from .context import TaskIngressContext


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactSource(StrEnum):
    USER_TEXT = "user_text"
    PUBLIC_ASSET = "public_asset"
    USER_CONFIRMATION = "user_confirmation"
    SYSTEM_DEFAULT = "system_default"
    MODEL_INFERENCE = "model_inference"


class TaskDraftStatus(StrEnum):
    INCOMPLETE = "incomplete"
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    CONFIRMED = "confirmed"


class TaskFact(StrictModel):
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
    value: JsonValue
    source: FactSource
    evidence: str = Field(min_length=1)
    impact: Literal["low", "medium", "high"]
    confirmed: bool = False

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fact evidence must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_source_authority(self) -> Self:
        if (
            self.source == FactSource.MODEL_INFERENCE
            and self.confirmed
            and self.impact in {"medium", "high"}
        ):
            raise ValueError(
                "medium/high-impact model inference cannot be confirmed"
            )
        if self.source == FactSource.SYSTEM_DEFAULT and self.impact != "low":
            raise ValueError("system defaults must be low impact")
        return self


class TaskAssumption(StrictModel):
    assumption_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
    value: JsonValue
    source: Literal["system_default", "model_inference"]
    impact: Literal["low", "medium", "high"]
    explanation_zh: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_default_impact(self) -> Self:
        if self.source == "system_default" and self.impact != "low":
            raise ValueError("system defaults must be low impact")
        return self


class TaskQuestion(StrictModel):
    question_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
    kind: Literal["blocking", "confirmable"]
    prompt_zh: str = Field(min_length=1)
    reason_zh: str = Field(min_length=1)
    candidate: JsonValue | None = None
    evidence: str | None = None


class TaskDraft(StrictModel):
    schema_version: Literal[2] = 2
    draft_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    request_text: str = Field(min_length=1)
    facts: list[TaskFact] = Field(default_factory=list)
    assumptions: list[TaskAssumption] = Field(default_factory=list)
    unresolved_questions: list[TaskQuestion] = Field(default_factory=list)
    assets: list[PublicAsset] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    ingress_context: TaskIngressContext = Field(
        default_factory=TaskIngressContext
    )
    status: TaskDraftStatus

    @model_validator(mode="before")
    @classmethod
    def migrate_v1(cls, value):
        if not isinstance(value, dict):
            return value
        if value.get("schema_version", 1) != 1:
            return value
        migrated = dict(value)
        migrated["schema_version"] = 2
        migrated.setdefault(
            "ingress_context",
            TaskIngressContext().model_dump(mode="json"),
        )
        return migrated

    @field_validator("request_text")
    @classmethod
    def normalize_request(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_text must not be blank")
        return normalized

    @field_validator("protected_paths")
    @classmethod
    def validate_protected_paths(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            path = Path(item)
            if not path.is_absolute() or path == Path("/"):
                raise ValueError("protected paths must be narrow absolute paths")
            normalized.append(str(path))
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate protected paths are not allowed")
        return normalized

    @model_validator(mode="after")
    def validate_draft_state(self) -> Self:
        fact_paths = [item.path for item in self.facts]
        if len(fact_paths) != len(set(fact_paths)):
            raise ValueError("duplicate fact paths are not allowed")
        asset_paths = [item.path for item in self.assets]
        if len(asset_paths) != len(set(asset_paths)):
            raise ValueError("duplicate asset paths are not allowed")
        question_ids = [item.question_id for item in self.unresolved_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate question IDs are not allowed")
        has_blocking = any(
            item.kind == "blocking" for item in self.unresolved_questions
        )
        if has_blocking and self.status != TaskDraftStatus.INCOMPLETE:
            raise ValueError("blocking questions require incomplete status")
        if self.status == TaskDraftStatus.CONFIRMED:
            if self.unresolved_questions:
                raise ValueError("confirmed draft cannot have unresolved questions")
        for protected in self.protected_paths:
            if protected in self.request_text:
                raise ValueError("request text contains a protected path")
        return self

    def fact_map(self) -> dict[str, TaskFact]:
        return {item.path: item for item in self.facts}


class DraftIssue(StrictModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    severity: Literal["blocking", "confirmable", "advisory"]
    field_path: str
    message_zh: str = Field(min_length=1)
    recovery_zh: str = Field(min_length=1)


class DraftReview(StrictModel):
    schema_version: Literal[1] = 1
    draft: TaskDraft
    issues: list[DraftIssue]
    can_compile: bool

    @model_validator(mode="after")
    def validate_compilation_flag(self) -> Self:
        expected = not any(
            item.severity in {"blocking", "confirmable"}
            for item in self.issues
        )
        if self.can_compile != expected:
            raise ValueError("can_compile is inconsistent with draft review")
        return self


class TaskCompilation(StrictModel):
    schema_version: Literal[1] = 1
    task: TaskSpec
    assumptions: list[TaskAssumption]
    diagnostics: list[DraftIssue]
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
