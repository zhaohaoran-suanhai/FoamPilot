"""Small, installable contracts for the official-six qualification."""

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
    "BLOCKED_ENVIRONMENT",
    "INVALID_QUALIFICATION",
]


class QualificationResult(StrictModel):
    case_id: str
    status: QualificationStatus
    native_status: str
    run_dir: Path
    attempts: int
    model_calls: int = 0
    selected_knowledge_ids: list[str] = Field(default_factory=list)
    openfoam_commands: list[list[str]] = Field(default_factory=list)
    manifest_issues: list[str] = Field(default_factory=list)
    metrics: list[QualificationMetric] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)
    message: str


class QualificationReport(StrictModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["official-six-v1"] = "official-six-v1"
    created_at: datetime
    model_name: str
    counts: dict[QualificationStatus, int]
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
