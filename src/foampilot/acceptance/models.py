"""Typed public acceptance requests and frozen executable conditions."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foampilot.observations import ObservationRequest
from foampilot.simulation.provenance import FactEvidence


AcceptanceOperator = Literal[
    "exists",
    "finite",
    "less_equal",
    "greater_equal",
    "between",
    "relative_error",
    "absolute_balance",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class AcceptanceScope(_StrictFrozenModel):
    time: Literal["latest", "final", "all", "range"]
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.time == "range":
            if self.start is None or self.end is None or self.end < self.start:
                raise ValueError("range requires ordered start/end")
        elif self.start is not None or self.end is not None:
            raise ValueError("start/end are only valid for range")
        return self


class AcceptanceRequest(_StrictFrozenModel):
    condition_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")
    observation: ObservationRequest
    operator: AcceptanceOperator
    limit: float | None = None
    lower: float | None = None
    upper: float | None = None
    reference: float | None = None
    tolerance: float | None = Field(default=None, ge=0)
    unit: str = Field(min_length=1)
    scope: AcceptanceScope
    source: Literal[
        "user_text",
        "user_confirmation",
        "public_asset_fact",
        "deterministic_rule",
        "model_inference",
    ]
    confirmed: bool
    provenance: tuple[FactEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operator_parameters(self) -> Self:
        if self.operator in {"exists", "finite"}:
            forbidden = (
                self.limit,
                self.lower,
                self.upper,
                self.reference,
                self.tolerance,
            )
            if any(value is not None for value in forbidden):
                raise ValueError("predicate operator does not accept numeric parameters")
        elif self.operator in {"less_equal", "greater_equal", "absolute_balance"}:
            if self.limit is None:
                raise ValueError("comparison operator requires limit")
        elif self.operator == "between":
            if self.lower is None or self.upper is None or self.lower > self.upper:
                raise ValueError("between requires ordered lower/upper")
        elif self.operator == "relative_error":
            if self.reference is None or self.tolerance is None:
                raise ValueError("relative_error requires reference and tolerance")
        return self


class AcceptanceCondition(_StrictFrozenModel):
    condition_id: str
    observation_id: str
    operator: AcceptanceOperator
    limit: float | None = None
    lower: float | None = None
    upper: float | None = None
    reference: float | None = None
    tolerance: float | None = None
    unit: str
    scope: AcceptanceScope
    provenance: tuple[FactEvidence, ...] = Field(min_length=1)


class UncompiledRequirement(_StrictFrozenModel):
    condition_id: str
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1)
    recovery: str = Field(min_length=1)


class AcceptancePlan(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    conditions: tuple[AcceptanceCondition, ...] = ()
    observation_requests: tuple[ObservationRequest, ...] = ()
    uncompiled: tuple[UncompiledRequirement, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        ids = [item.condition_id for item in self.conditions]
        ids.extend(item.condition_id for item in self.uncompiled)
        if len(ids) != len(set(ids)):
            raise ValueError("condition IDs must be unique")
        observable_ids = [item.observation_id for item in self.observation_requests]
        if len(observable_ids) != len(set(observable_ids)):
            raise ValueError("observation request IDs must be unique")
        if any(
            condition.observation_id not in set(observable_ids)
            for condition in self.conditions
        ):
            raise ValueError("condition requires a compiled observation")
        return self

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class ConditionResult(_StrictFrozenModel):
    condition_id: str
    observation_id: str
    status: Literal["PASS", "FAIL", "NOT_EVALUATED"]
    observed_value: float | None = None
    unit: str | None = None
    detail: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class ObservationResult(_StrictFrozenModel):
    observation_id: str
    status: Literal["AVAILABLE", "UNAVAILABLE", "PARTIAL"]
    latest_value: float | tuple[float, ...] | None = None
    unit: str | None = None
    evidence_refs: tuple[str, ...] = ()


class ResultReport(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    verdict: Literal["PASS", "FAIL", "INCOMPLETE", "NOT_REQUESTED"]
    acceptance_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    derived_metrics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    conditions: tuple[ConditionResult, ...]
    observations: tuple[ObservationResult, ...]
    missing_evidence: tuple[str, ...] = ()


__all__ = [
    "AcceptanceCondition",
    "AcceptanceOperator",
    "AcceptancePlan",
    "AcceptanceRequest",
    "AcceptanceScope",
    "ConditionResult",
    "ObservationResult",
    "ResultReport",
    "UncompiledRequirement",
]
