from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from foampilot.improvement import (
    LearningCandidate,
    OfficialExampleEvidence,
    PublicEvidence,
    SourceRun,
)
from foampilot.improvement.models import PromotionGate, PromotionReport
from foampilot.improvement.promotion import compare_promotion
from foampilot.qualification.models import (
    QualificationReport,
    QualificationResult,
)


def _candidate(
    *,
    development_cases: list[str] | None = None,
    regression_cases: list[str] | None = None,
    holdout_cases: list[str] | None = None,
    max_total_model_calls_delta: int = 0,
    max_total_duration_ratio: float = 1.25,
) -> LearningCandidate:
    return LearningCandidate(
        candidate_id="candidate-1",
        source_runs=[
            SourceRun(
                path=Path("/runs/source"),
                manifest_sha256="a" * 64,
            )
        ],
        root_cause="numerics",
        public_evidence=PublicEvidence(),
        official_example=OfficialExampleEvidence(),
        generalized_lesson="Use a bounded transient time step.",
        proposed_target="knowledge",
        development_cases=development_cases or [],
        regression_cases=regression_cases or [],
        holdout_cases=holdout_cases or [],
        promotion_criteria=["source_improves"],
        max_total_model_calls_delta=max_total_model_calls_delta,
        max_total_duration_ratio=max_total_duration_ratio,
    )


def _native_status(status: str) -> str:
    if status == "PASS":
        return "PUBLIC_VALIDATION_PASS"
    if status == "BLOCKED_ENVIRONMENT":
        return "BLOCKED_ENVIRONMENT"
    return "SOLVER_FAILED"


def _report(
    statuses: dict[str, str],
    *,
    native_statuses: dict[str, str] | None = None,
    model_name: str = "gpt-test",
    model_calls: int = 1,
    duration_seconds: float = 1.0,
) -> QualificationReport:
    native_statuses = native_statuses or {}
    results = [
        QualificationResult(
            case_id=case_id,
            status=status,
            native_status=native_statuses.get(
                case_id,
                _native_status(status),
            ),
            run_dir=Path("/runs") / case_id,
            attempts=1,
            model_calls=model_calls,
            duration_seconds=duration_seconds,
            message="synthetic",
        )
        for case_id, status in statuses.items()
    ]
    return QualificationReport(
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        backend_id="test-backend",
        model_name=model_name,
        counts={
            "PASS": sum(result.status == "PASS" for result in results),
            "FAIL_AGENT": sum(
                result.status == "FAIL_AGENT" for result in results
            ),
            "BLOCKED_ENVIRONMENT": sum(
                result.status == "BLOCKED_ENVIRONMENT" for result in results
            ),
            "INVALID_QUALIFICATION": sum(
                result.status == "INVALID_QUALIFICATION"
                for result in results
            ),
        },
        results=results,
    )


def _gate(report: PromotionReport, name: str) -> PromotionGate:
    return next(gate for gate in report.gates if gate.name == name)


def test_regression_failure_blocks_promotion() -> None:
    candidate = _candidate(
        development_cases=["source"],
        regression_cases=["regression"],
    )
    baseline = _report({"regression": "PASS", "source": "FAIL_AGENT"})
    current = _report({"regression": "FAIL_AGENT", "source": "PASS"})

    report = compare_promotion(candidate, baseline, current)

    assert not report.eligible
    assert _gate(report, "regression_no_regression").passed is False


def test_source_fix_and_holdout_non_decrease_is_eligible() -> None:
    candidate = _candidate(
        development_cases=["source"],
        holdout_cases=["holdout"],
    )
    baseline = _report(
        {"source": "FAIL_AGENT", "holdout": "FAIL_AGENT"}
    )
    current = _report(
        {"source": "PASS", "holdout": "FAIL_AGENT"}
    )

    report = compare_promotion(candidate, baseline, current)

    assert report.eligible
    assert report.physics_pass_delta == 1
    assert [delta.case_id for delta in report.cases] == [
        "holdout",
        "source",
    ]
    assert next(
        delta for delta in report.cases if delta.case_id == "source"
    ).role == "source"


def test_holdout_rank_decrease_blocks_promotion() -> None:
    candidate = _candidate(
        development_cases=["source"],
        holdout_cases=["holdout"],
    )
    baseline = _report(
        {"source": "FAIL_AGENT", "holdout": "FAIL_AGENT"},
        native_statuses={"holdout": "PUBLIC_VALIDATION_FAILED"},
    )
    current = _report(
        {"source": "PASS", "holdout": "FAIL_AGENT"},
        native_statuses={"holdout": "SOLVER_FAILED"},
    )

    report = compare_promotion(candidate, baseline, current)

    assert not report.eligible
    assert _gate(report, "holdout_non_decreasing").passed is False


def test_cost_budget_blocks_otherwise_eligible_candidate() -> None:
    candidate = _candidate(
        development_cases=["source"],
        max_total_model_calls_delta=0,
        max_total_duration_ratio=1.1,
    )
    baseline = _report(
        {"source": "FAIL_AGENT"},
        model_calls=1,
        duration_seconds=1.0,
    )
    current = _report(
        {"source": "PASS"},
        model_calls=2,
        duration_seconds=2.0,
    )

    report = compare_promotion(candidate, baseline, current)

    assert not report.eligible
    assert _gate(report, "model_calls_within_budget").passed is False
    assert _gate(report, "duration_within_budget").passed is False


def test_comparison_requires_matching_model_protocol_and_cases() -> None:
    candidate = _candidate(development_cases=["source"])
    baseline = _report({"source": "FAIL_AGENT"})

    with pytest.raises(ValueError, match="model"):
        compare_promotion(
            candidate,
            baseline,
            _report({"source": "PASS"}, model_name="other-model"),
        )

    protocol_mismatch = _report({"source": "PASS"}).model_copy(
        update={"protocol_id": "other-protocol"}
    )
    with pytest.raises(ValueError, match="protocol"):
        compare_promotion(candidate, baseline, protocol_mismatch)

    with pytest.raises(ValueError, match="case set"):
        compare_promotion(
            candidate,
            baseline,
            _report({"source": "PASS", "extra": "PASS"}),
        )


def test_comparison_requires_every_case_to_have_a_role() -> None:
    candidate = _candidate(development_cases=["source"])
    baseline = _report(
        {"source": "FAIL_AGENT", "unclassified": "FAIL_AGENT"}
    )
    current = _report(
        {"source": "PASS", "unclassified": "FAIL_AGENT"}
    )

    with pytest.raises(ValueError, match="candidate roles"):
        compare_promotion(candidate, baseline, current)


def test_comparison_rejects_candidate_roles_outside_report() -> None:
    candidate = _candidate(
        development_cases=["source"],
        holdout_cases=["missing-holdout"],
    )
    baseline = _report({"source": "FAIL_AGENT"})
    current = _report({"source": "PASS"})

    with pytest.raises(ValueError, match="candidate roles"):
        compare_promotion(candidate, baseline, current)
