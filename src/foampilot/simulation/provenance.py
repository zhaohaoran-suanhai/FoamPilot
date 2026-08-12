"""Frozen source-authority and uncertainty contracts for simulation design."""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


EvidenceSource = Literal[
    "user_text",
    "user_confirmation",
    "public_asset_fact",
    "deterministic_rule",
    "system_default",
    "model_inference",
]
ImpactLevel = Literal["low", "medium", "high"]
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
T = TypeVar("T")
_FIELD_PATH = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*$"
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _json_value(value: object) -> object:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("value must be JSON serializable") from error
    parsed = json.loads(serialized)
    TypeAdapter(JsonValue).validate_python(parsed)
    return value


class FactEvidence(StrictModel):
    kind: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    reference: str | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("evidence kind must be a stable identifier")
        return normalized

    @field_validator("detail")
    @classmethod
    def validate_detail(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence detail must not be blank")
        return normalized


class ResolvedValue(StrictModel, Generic[T]):
    field_path: str
    value: T
    source: EvidenceSource
    impact: ImpactLevel
    evidence: tuple[FactEvidence, ...] = Field(min_length=1)
    confirmed: bool = False

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        if not _FIELD_PATH.fullmatch(value):
            raise ValueError("field path must use dot-separated identifiers")
        return value

    @field_validator("value")
    @classmethod
    def validate_json_value(cls, value: T) -> T:
        _json_value(value)
        return value

    @field_validator("evidence")
    @classmethod
    def validate_unique_evidence(
        cls,
        values: tuple[FactEvidence, ...],
    ) -> tuple[FactEvidence, ...]:
        identities = [item.model_dump_json() for item in values]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate evidence is not allowed")
        return values

    @model_validator(mode="after")
    def validate_authority(self) -> "ResolvedValue[T]":
        if self.source == "model_inference" and self.confirmed:
            raise ValueError("model inference cannot self-confirm")
        if self.source == "system_default" and self.impact != "low":
            raise ValueError("system defaults are low impact only")
        if self.source == "user_confirmation" and not self.confirmed:
            raise ValueError("user confirmation must be confirmed")
        return self


class DesignCandidate(StrictModel):
    candidate_id: str
    value: JsonValue
    rationale: str = Field(min_length=1)
    evidence: tuple[FactEvidence, ...] = Field(min_length=1)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("candidate ID must be a stable identifier")
        return value

    @field_validator("value")
    @classmethod
    def validate_candidate_value(cls, value: JsonValue) -> JsonValue:
        _json_value(value)
        return value


class Uncertainty(StrictModel):
    question_id: str
    field_path: str
    impact: ImpactLevel
    kind: Literal["confirmable", "information_required", "conflict"]
    prompt_zh: str = Field(min_length=1)
    reason_zh: str = Field(min_length=1)
    candidates: tuple[DesignCandidate, ...] = ()
    conflicting_evidence: tuple[FactEvidence, ...] = ()

    @field_validator("question_id")
    @classmethod
    def validate_question_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("question ID must be a stable identifier")
        return value

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        if not _FIELD_PATH.fullmatch(value):
            raise ValueError("field path must use dot-separated identifiers")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> "Uncertainty":
        if self.kind == "confirmable" and not self.candidates:
            raise ValueError("confirmable uncertainty requires a candidate")
        if self.kind == "information_required" and self.candidates:
            raise ValueError(
                "information-required uncertainty must not contain a candidate"
            )
        if (
            self.kind == "conflict"
            and len(self.candidates) < 2
            and len(self.conflicting_evidence) < 2
        ):
            raise ValueError(
                "conflict uncertainty requires two candidates or conflicting evidence"
            )
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("uncertainty candidate IDs must be unique")
        return self


class ConfirmationRecord(StrictModel):
    confirmation_id: str
    question_id: str
    field_path: str
    candidate_id: str
    confirmed_value: JsonValue
    source: Literal["user_confirmation"] = "user_confirmation"
    answered_at: datetime

    @field_validator("confirmation_id", "question_id", "candidate_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("confirmation identifiers must be stable")
        return value

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        if not _FIELD_PATH.fullmatch(value):
            raise ValueError("field path must use dot-separated identifiers")
        return value

    @field_validator("confirmed_value")
    @classmethod
    def validate_confirmed_value(cls, value: JsonValue) -> JsonValue:
        _json_value(value)
        return value

    @field_validator("answered_at")
    @classmethod
    def validate_utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("answered_at must be timezone aware")
        return value


__all__ = [
    "ConfirmationRecord",
    "DesignCandidate",
    "EvidenceSource",
    "FactEvidence",
    "ImpactLevel",
    "JsonValue",
    "ResolvedValue",
    "Uncertainty",
]
