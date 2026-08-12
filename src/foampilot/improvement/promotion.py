"""Read-only promotion gates over two frozen qualification reports."""

from __future__ import annotations

from foampilot.qualification.models import (
    QualificationReport,
    QualificationResult,
)

from .models import (
    LearningCandidate,
    PromotionCaseDelta,
    PromotionGate,
    PromotionReport,
)


_NATIVE_RANK = {
    "REQUEST_INCOMPLETE": 0,
    "BLOCKED_ENVIRONMENT": 0,
    "PLAN_INVALID": 1,
    "GENERATION_INVALID": 1,
    "CASE_GENERATION_FAILED": 1,
    "STATIC_INSPECTION_FAILED": 2,
    "MESH_FAILED": 3,
    "MESH_QUALITY_FAILED": 3,
    "INITIALIZATION_FAILED": 4,
    "SOLVER_FAILED": 5,
    "POSTPROCESS_FAILED": 6,
    "ACCEPTANCE_FAILED": 7,
    "ACCEPTANCE_INCOMPLETE": 7,
    "PUBLIC_VALIDATION_FAILED": 7,
    "RUN_COMPLETED": 8,
    "PUBLIC_VALIDATION_PASS": 8,
}


def _rank(result: QualificationResult) -> int:
    if result.status == "PASS":
        return 9
    return _NATIVE_RANK.get(result.native_status, 0)


def _by_case(
    report: QualificationReport,
    label: str,
) -> dict[str, QualificationResult]:
    indexed = {result.case_id: result for result in report.results}
    if len(indexed) != len(report.results):
        raise ValueError(f"{label} report contains duplicate case IDs")
    return indexed


def _gate(name: str, passed: bool, detail: str) -> PromotionGate:
    return PromotionGate(name=name, passed=passed, detail=detail)


def _role(
    case_id: str,
    *,
    source_cases: set[str],
    candidate: LearningCandidate,
) -> str:
    if case_id in source_cases:
        return "source"
    if case_id in candidate.development_cases:
        return "development"
    if case_id in candidate.regression_cases:
        return "regression"
    return "holdout"


def compare_promotion(
    candidate: LearningCandidate,
    baseline: QualificationReport,
    current: QualificationReport,
) -> PromotionReport:
    if baseline.protocol_id != current.protocol_id:
        raise ValueError(
            "qualification protocol mismatch: "
            f"{baseline.protocol_id!r} != {current.protocol_id!r}"
        )
    if baseline.model_name != current.model_name:
        raise ValueError(
            "qualification model mismatch: "
            f"{baseline.model_name!r} != {current.model_name!r}"
        )

    baseline_cases = _by_case(baseline, "baseline")
    current_cases = _by_case(current, "current")
    if set(baseline_cases) != set(current_cases):
        raise ValueError("qualification case set mismatch")

    source_paths = {source.path.resolve() for source in candidate.source_runs}
    source_cases = {
        case_id
        for case_id, result in baseline_cases.items()
        if result.run_dir.resolve() in source_paths
    }
    if not source_cases:
        raise ValueError(
            "candidate source runs do not match the baseline qualification"
        )

    assigned_cases = (
        source_cases
        | set(candidate.development_cases)
        | set(candidate.regression_cases)
        | set(candidate.holdout_cases)
    )
    unclassified = sorted(set(baseline_cases) - assigned_cases)
    if unclassified:
        raise ValueError(
            "qualification cases missing from candidate roles: "
            + ", ".join(unclassified)
        )
    unknown_roles = sorted(assigned_cases - set(baseline_cases))
    if unknown_roles:
        raise ValueError(
            "candidate roles contain cases outside qualification report: "
            + ", ".join(unknown_roles)
        )

    deltas = [
        PromotionCaseDelta(
            case_id=case_id,
            role=_role(
                case_id,
                source_cases=source_cases,
                candidate=candidate,
            ),
            baseline_status=baseline_cases[case_id].status,
            current_status=current_cases[case_id].status,
            baseline_rank=_rank(baseline_cases[case_id]),
            current_rank=_rank(current_cases[case_id]),
        )
        for case_id in sorted(baseline_cases)
    ]
    delta_by_case = {delta.case_id: delta for delta in deltas}

    source_non_decreasing = all(
        delta_by_case[case_id].current_rank
        >= delta_by_case[case_id].baseline_rank
        for case_id in source_cases
    )
    source_increases = any(
        delta_by_case[case_id].current_rank
        > delta_by_case[case_id].baseline_rank
        for case_id in source_cases
    )
    development_passes = all(
        current_cases[case_id].status == "PASS"
        for case_id in candidate.development_cases
    )
    regression_no_regression = all(
        baseline_cases[case_id].status != "PASS"
        or current_cases[case_id].status == "PASS"
        for case_id in candidate.regression_cases
    )
    holdout_non_decreasing = all(
        delta_by_case[case_id].current_rank
        >= delta_by_case[case_id].baseline_rank
        for case_id in candidate.holdout_cases
    )

    baseline_passes = sum(
        result.status == "PASS" for result in baseline.results
    )
    current_passes = sum(
        result.status == "PASS" for result in current.results
    )
    physics_pass_delta = current_passes - baseline_passes

    baseline_calls = sum(result.model_calls for result in baseline.results)
    current_calls = sum(result.model_calls for result in current.results)
    model_calls_delta = current_calls - baseline_calls

    baseline_duration = sum(
        result.duration_seconds for result in baseline.results
    )
    current_duration = sum(
        result.duration_seconds for result in current.results
    )
    if baseline_duration == 0:
        duration_ratio = 1.0 if current_duration == 0 else float("inf")
    else:
        duration_ratio = current_duration / baseline_duration

    gates = [
        _gate(
            "source_improves",
            source_non_decreasing and source_increases,
            "source ranks must not decrease and at least one must improve",
        ),
        _gate(
            "development_passes",
            development_passes,
            "all declared development cases must strictly pass",
        ),
        _gate(
            "regression_no_regression",
            regression_no_regression,
            "previously passing regression cases must remain passing",
        ),
        _gate(
            "holdout_non_decreasing",
            holdout_non_decreasing,
            "holdout progress ranks must not decrease",
        ),
        _gate(
            "physics_pass_count_increases",
            physics_pass_delta > 0,
            f"strict PASS count delta is {physics_pass_delta}",
        ),
        _gate(
            "model_calls_within_budget",
            model_calls_delta <= candidate.max_total_model_calls_delta,
            (
                f"model-call delta {model_calls_delta} <= "
                f"{candidate.max_total_model_calls_delta}"
            ),
        ),
        _gate(
            "duration_within_budget",
            duration_ratio <= candidate.max_total_duration_ratio,
            (
                f"duration ratio {duration_ratio:.6g} <= "
                f"{candidate.max_total_duration_ratio:.6g}"
            ),
        ),
    ]
    return PromotionReport(
        candidate_id=candidate.candidate_id,
        protocol_id=baseline.protocol_id,
        model_name=baseline.model_name,
        eligible=all(gate.passed for gate in gates),
        physics_pass_delta=physics_pass_delta,
        model_calls_delta=model_calls_delta,
        duration_ratio=duration_ratio,
        gates=gates,
        cases=deltas,
    )
