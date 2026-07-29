"""Generic public validation results for native OpenFOAM plans."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type JsonScalar = float | int | str | bool | None

FailureLayer = Literal[
    "REQUEST_INCOMPLETE",
    "ENVIRONMENT_BLOCKED",
    "PLAN_INVALID",
    "CASE_GENERATION_FAILED",
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
]


class PublicValidationCheck(StrictModel):
    name: str
    passed: bool
    detail: str
    observed: dict[str, JsonScalar] = Field(default_factory=dict)
    limits: dict[str, JsonScalar] = Field(default_factory=dict)


class PublicValidationReport(StrictModel):
    checks: list[PublicValidationCheck]
    failure_layer: FailureLayer | None = None
    failed_step_id: str | None = None

    @property
    def passed(self) -> bool:
        return (
            bool(self.checks)
            and all(item.passed for item in self.checks)
            and self.failure_layer is None
        )

    def feedback(self) -> str:
        return "\n".join(
            f"- {item.detail}" for item in self.checks if not item.passed
        )
