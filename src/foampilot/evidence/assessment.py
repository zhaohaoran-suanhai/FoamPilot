"""Deterministic execution assessment with no user acceptance authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, Self

from pydantic import Field, model_validator

from .models import RawCommandEvidence, RunFacts, StrictFrozenModel

if TYPE_CHECKING:
    from foampilot.preprocessing import MeshQualityReport


class _MeshQuality(Protocol):
    @property
    def passed(self) -> bool: ...


FailureLayer = Literal[
    "ENVIRONMENT_BLOCKED",
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "MESH_QUALITY_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
]


class RunAssessment(StrictFrozenModel):
    schema_version: Literal[1] = 1
    ok: bool
    failure_layer: FailureLayer | None = None
    failed_step_id: str | None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)
    detail: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_terminal_truth(self) -> Self:
        if self.ok and (
            self.failure_layer is not None or self.failed_step_id is not None
        ):
            raise ValueError("passing assessment cannot contain failure facts")
        if not self.ok and self.failure_layer is None:
            raise ValueError("failed assessment requires a failure layer")
        return self


def _failed_layer(step: RawCommandEvidence, facts: RunFacts) -> FailureLayer:
    if any(
        item.step_id == step.step_id
        and item.code == "EXECUTION_BACKEND_ERROR"
        for item in facts.native_errors
    ):
        return "ENVIRONMENT_BLOCKED"
    return {
        "mesh": "MESH_FAILED",
        "check": "MESH_FAILED",
        "initialize": "INITIALIZATION_FAILED",
        "postprocess": "POSTPROCESS_FAILED",
    }.get(step.stage, "SOLVER_FAILED")


def assess_native_run(
    facts: RunFacts,
    *,
    mesh_quality: _MeshQuality | None = None,
) -> RunAssessment:
    failed = next(
        (
            step
            for step in facts.raw_steps
            if step.return_code != 0 or step.timed_out or step.cancelled
        ),
        None,
    )
    if failed is not None:
        reason = (
            "COMMAND_CANCELLED"
            if failed.cancelled
            else "COMMAND_TIMED_OUT"
            if failed.timed_out
            else "COMMAND_FAILED"
        )
        return RunAssessment(
            ok=False,
            failure_layer=_failed_layer(failed, facts),
            failed_step_id=failed.step_id,
            reason_codes=(reason,),
            detail=f"typed command {failed.step_id} did not complete successfully",
        )
    if mesh_quality is not None and not mesh_quality.passed:
        return RunAssessment(
            ok=False,
            failure_layer="MESH_QUALITY_FAILED",
            reason_codes=("MESH_INTENT_NOT_SATISFIED",),
            detail="deterministic mesh facts do not satisfy MeshIntent",
        )
    if not any(
        item.completed_normally is True for item in facts.solver_progress
    ):
        return RunAssessment(
            ok=False,
            failure_layer="SOLVER_FAILED",
            reason_codes=("NORMAL_SOLVER_END_MISSING",),
            detail="no solver step has a canonical normal-End fact",
        )
    return RunAssessment(
        ok=True,
        reason_codes=("NORMAL_SOLVER_END",),
        detail="typed commands completed and solver emitted canonical End",
    )


def assessment_for_inspection(issue_code: str) -> RunAssessment:
    return RunAssessment(
        ok=False,
        failure_layer="STATIC_INSPECTION_FAILED",
        reason_codes=(issue_code,),
        detail="static case inspection failed before execution",
    )


__all__ = [
    "FailureLayer",
    "RunAssessment",
    "assess_native_run",
    "assessment_for_inspection",
]
