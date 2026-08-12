"""Read-only contracts for historical ``public-validation.json`` artifacts.

These models preserve replay compatibility.  New solve workflows must use
``RunAssessment`` for execution truth and ``ResultReport`` for explicit user
acceptance; no evaluator is exposed from this module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


type JsonScalar = float | int | str | bool | None

LegacyFailureLayer = Literal[
    "REQUEST_INCOMPLETE",
    "ENVIRONMENT_BLOCKED",
    "PLAN_INVALID",
    "CASE_GENERATION_FAILED",
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "MESH_QUALITY_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
]


class LegacyPublicValidationCheck(_StrictFrozenModel):
    name: str
    passed: bool
    detail: str
    observed: dict[str, JsonScalar] = Field(default_factory=dict)
    limits: dict[str, JsonScalar] = Field(default_factory=dict)


class LegacyPublicValidationReport(_StrictFrozenModel):
    checks: tuple[LegacyPublicValidationCheck, ...]
    failure_layer: LegacyFailureLayer | None = None
    failed_step_id: str | None = None


__all__ = [
    "LegacyFailureLayer",
    "LegacyPublicValidationCheck",
    "LegacyPublicValidationReport",
]
