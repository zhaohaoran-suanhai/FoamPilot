import json
from pathlib import Path

import pytest

from foampilot.artifacts import (
    AttemptSummary,
    NativeAgentOutcome,
    RunSummary,
)
from foampilot.qualification.models import (
    QualificationMetric,
    compare_metric,
)
from foampilot.qualification.reporting import (
    build_qualification_report,
    classify_qualification,
    native_case_dir,
)


def _outcome(
    tmp_path: Path,
    *,
    status: str,
    attempts: list[AttemptSummary] | None = None,
) -> NativeAgentOutcome:
    summary = RunSummary(
        task_id="laminar-cavity",
        status=status,
        attempts=attempts or [],
        message="test",
    )
    return NativeAgentOutcome(
        status=status,
        run_dir=tmp_path / "run-1",
        summary=summary,
    )


def test_metric_comparison_supports_bounds_and_relative_vectors() -> None:
    assert compare_metric(
        observed=0.005,
        reference=0.001,
        tolerance=0.01,
        mode="absolute_upper_bound",
    ).passed
    relative = compare_metric(
        observed=[1.0, 2.1, 3.0],
        reference=[1.0, 2.0, 3.0],
        tolerance=0.05,
        mode="relative_l2",
    )
    assert relative.passed
    assert relative.error is not None
    assert 0 < relative.error < 0.05


def test_missing_observation_is_not_converted_to_zero() -> None:
    result = compare_metric(
        observed=None,
        reference=1.0,
        tolerance=0.1,
        mode="relative_absolute",
    )

    assert result.passed is None
    assert "missing" in result.detail


def test_native_case_dir_uses_the_final_attempt(tmp_path: Path) -> None:
    outcome = _outcome(
        tmp_path,
        status="PUBLIC_VALIDATION_PASS",
        attempts=[
            AttemptSummary(attempt=1, status="SOLVER_FAILED"),
            AttemptSummary(attempt=2, status="PUBLIC_VALIDATION_PASS"),
        ],
    )

    assert native_case_dir(outcome) == (
        outcome.run_dir / "attempt-02" / "case"
    )


def test_classification_preserves_failure_layers(tmp_path: Path) -> None:
    blocked = _outcome(tmp_path, status="BLOCKED_ENVIRONMENT")
    failed = _outcome(tmp_path, status="SOLVER_FAILED")
    passed = _outcome(
        tmp_path,
        status="PUBLIC_VALIDATION_PASS",
        attempts=[
            AttemptSummary(attempt=1, status="PUBLIC_VALIDATION_PASS")
        ],
    )

    assert classify_qualification(blocked, [], []) == "BLOCKED_ENVIRONMENT"
    assert classify_qualification(failed, [], []) == "FAIL_AGENT"
    assert classify_qualification(passed, ["hash mismatch"], []) == "FAIL_AGENT"
    assert (
        classify_qualification(
            passed,
            [],
            [
                QualificationMetric(
                    name="physics",
                    passed=False,
                    required=True,
                    detail="failed",
                )
            ],
        )
        == "FAIL_AGENT"
    )


def test_report_preserves_protocol_order_and_mpi_rendering(
    tmp_path: Path,
) -> None:
    outcome = _outcome(tmp_path, status="PLAN_INVALID")
    outcome.run_dir.mkdir(parents=True)
    (outcome.run_dir / "execution-plan.json").write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "executable": "buoyantFoam",
                        "args": ["-caseOption", "-parallel"],
                        "mpi_ranks": 15,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_qualification_report(
        [
            {
                "case_id": "buoyant-cavity",
                "outcome": outcome,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 1.0,
                "message": "failed",
            }
        ],
        model_name="gpt-test",
    )

    assert report.results[0].case_id == "buoyant-cavity"
    assert report.results[0].openfoam_commands == [
        [
            "mpirun",
            "-n",
            "15",
            "buoyantFoam",
            "-caseOption",
            "-parallel",
        ]
    ]
    assert report.counts["FAIL_AGENT"] == 1
