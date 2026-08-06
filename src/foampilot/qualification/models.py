"""Small, installable contracts for role-aware qualification suites."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationMetricCategory(StrEnum):
    COMPLETION = "completion"
    CONSERVATION = "conservation"
    PHYSICS_GOLDEN = "physics_golden"


class ValidationMetric(StrictModel):
    name: str = Field(min_length=1)
    category: ValidationMetricCategory
    formula: str = Field(min_length=1)
    field: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    sample_coordinates: list[list[float]] = Field(min_length=1)
    comparison_mode: str = Field(min_length=1)
    tolerance: float = Field(ge=0)
    tolerance_source: str = Field(min_length=1)
    required: bool = True


class PrivateValidation(StrictModel):
    case_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    golden_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_application: str = Field(min_length=1)
    expected_end_time: float = Field(gt=0)
    metrics: list[ValidationMetric] = Field(min_length=3)
    notes: list[str] = Field(default_factory=list)


class ComparisonResult(StrictModel):
    passed: bool | None
    error: float | None = None
    detail: str = ""


class QualificationMetric(StrictModel):
    name: str
    passed: bool | None
    required: bool = True
    value: float | int | str | None = None
    unit: str = ""
    detail: str = ""


QualificationStatus = Literal[
    "PASS",
    "FAIL_AGENT",
    "DEFERRED_BACKEND",
    "BLOCKED_ENVIRONMENT",
    "INVALID_QUALIFICATION",
]


class QualificationResult(StrictModel):
    case_id: str
    evaluation_level: Literal[
        "physics_qualification",
        "public_validation",
    ] = "physics_qualification"
    status: QualificationStatus
    workflow_state: str = "FAILED"
    native_status: str | None
    run_dir: Path
    attempts: int
    model_calls: int = 0
    logical_model_requests: int = 0
    transport_attempts: int = 0
    model_time_seconds: float = Field(default=0, ge=0)
    backend_deferred: bool = False
    generation_success: bool = False
    native_execution_started: bool = False
    mesh_generation_pass: bool | None = None
    check_mesh_pass: bool | None = None
    target_solver_started: bool = False
    solver_normal_completion: bool = False
    public_validation_pass: bool = False
    physics_qualification_pass: bool = False
    time_to_first_openfoam_command: float | None = Field(
        default=None,
        ge=0,
    )
    openfoam_time_seconds: float = Field(default=0, ge=0)
    path_kind: Literal[
        "cold",
        "warm_plan",
        "warm_mesh",
        "repair_reuse",
    ] | None = None
    pre_solve_latency_seconds: float | None = Field(default=None, ge=0)
    end_to_end_latency_seconds: float | None = Field(default=None, ge=0)
    performance_evidence_complete: bool = False
    plan_reuse: Literal["miss", "hit", "disabled"] = "disabled"
    geometry_reuse: Literal["miss", "hit", "disabled"] = "disabled"
    mesh_reuse: Literal["miss", "hit", "disabled"] = "disabled"
    repair_start_stage: str | None = None
    performance_diagnostics: list[str] = Field(default_factory=list)
    selected_knowledge_ids: list[str] = Field(default_factory=list)
    openfoam_commands: list[list[str]] = Field(default_factory=list)
    manifest_issues: list[str] = Field(default_factory=list)
    metrics: list[QualificationMetric] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)
    message: str


class LatencyPercentiles(StrictModel):
    count: int = Field(default=0, ge=0)
    p50_seconds: float | None = Field(default=None, ge=0)
    p95_seconds: float | None = Field(default=None, ge=0)


class QualificationAggregates(StrictModel):
    task_count: int = Field(default=0, ge=0)
    logical_model_requests: int = Field(default=0, ge=0)
    transport_attempts: int = Field(default=0, ge=0)
    backend_deferred_count: int = Field(default=0, ge=0)
    generation_success_count: int = Field(default=0, ge=0)
    native_execution_started_count: int = Field(default=0, ge=0)
    mesh_generation_pass_count: int = Field(default=0, ge=0)
    check_mesh_pass_count: int = Field(default=0, ge=0)
    target_solver_started_count: int = Field(default=0, ge=0)
    solver_normal_completion_count: int = Field(default=0, ge=0)
    public_validation_pass_count: int = Field(default=0, ge=0)
    physics_qualification_pass_count: int = Field(default=0, ge=0)
    model_time_seconds: float = Field(default=0, ge=0)
    openfoam_time_seconds: float = Field(default=0, ge=0)
    environment_blocked_count: int = Field(default=0, ge=0)
    cold_path_pre_solve: LatencyPercentiles = Field(
        default_factory=LatencyPercentiles
    )
    warm_path_pre_solve: LatencyPercentiles = Field(
        default_factory=LatencyPercentiles
    )
    end_to_end_latency: LatencyPercentiles = Field(
        default_factory=LatencyPercentiles
    )
    plan_reuse_hit_count: int = Field(default=0, ge=0)
    derived_cache_hit_count: int = Field(default=0, ge=0)
    derived_cache_miss_count: int = Field(default=0, ge=0)
    derived_cache_invalid_count: int = Field(default=0, ge=0)
    repair_run_count: int = Field(default=0, ge=0)
    repaired_success_count: int = Field(default=0, ge=0)


class QualificationReport(StrictModel):
    schema_version: Literal[3] = 3
    protocol_id: str = Field(
        default="official-six-v1",
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    created_at: datetime
    backend_id: str
    model_name: str
    automatic_failover: Literal[False] = False
    counts: dict[QualificationStatus, int]
    aggregates: QualificationAggregates = Field(
        default_factory=QualificationAggregates
    )
    results: list[QualificationResult]


def compare_metric(
    *,
    observed: Any,
    reference: Any,
    tolerance: float,
    mode: str,
) -> ComparisonResult:
    """Compare one extracted observation with a compact reference metric."""

    if observed is None:
        return ComparisonResult(
            passed=None,
            detail="missing required observation",
        )
    try:
        actual = np.asarray(observed, dtype=float)
        expected = np.asarray(reference, dtype=float)
    except (TypeError, ValueError) as error:
        return ComparisonResult(
            passed=False,
            detail=f"non-numeric observation: {error}",
        )
    if mode not in {"absolute_upper_bound", "lower_bound"} and (
        actual.shape != expected.shape
    ):
        return ComparisonResult(
            passed=False,
            detail=(
                f"shape mismatch: observed {actual.shape}, "
                f"reference {expected.shape}"
            ),
        )

    difference = actual - expected
    if mode in {"relative_l2", "normalized_l2"}:
        error = float(
            np.linalg.norm(difference.reshape(-1))
            / max(np.linalg.norm(expected.reshape(-1)), 1e-12)
        )
    elif mode == "relative_l1":
        error = float(
            np.sum(np.abs(difference))
            / max(np.sum(np.abs(expected)), 1e-12)
        )
    elif mode == "relative_absolute":
        error = float(
            np.max(np.abs(difference))
            / max(float(np.max(np.abs(expected))), 1e-12)
        )
    elif mode == "absolute_max":
        error = float(np.max(np.abs(difference)))
    elif mode == "absolute_mean":
        error = float(np.mean(np.abs(difference)))
    elif mode == "absolute_upper_bound":
        error = float(np.max(np.abs(actual)))
    elif mode == "lower_bound":
        error = max(
            float(np.max(expected)) - float(np.min(actual)),
            0.0,
        )
    else:
        error = float(np.max(np.abs(difference)))
    return ComparisonResult(
        passed=bool(error <= tolerance),
        error=error,
        detail=f"{mode} error {error:.8g} <= {tolerance:.8g}",
    )
