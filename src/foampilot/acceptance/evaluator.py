"""Deterministic evaluation of explicit conditions over derived metrics."""

from __future__ import annotations

import math

from foampilot.postprocessing import DerivedMetrics, MetricSeries

from .models import (
    AcceptanceCondition,
    AcceptancePlan,
    ConditionResult,
    ObservationResult,
    ResultReport,
)


def _latest_scalar(series: MetricSeries) -> tuple[float | None, str | None, tuple[str, ...]]:
    if not series.samples:
        return None, None, ()
    sample = series.samples[-1]
    if not isinstance(sample.value, (int, float)):
        return None, sample.unit, sample.evidence_refs
    return float(sample.value), sample.unit, sample.evidence_refs


def _matches(condition: AcceptanceCondition, value: float) -> bool:
    if condition.operator == "exists":
        return True
    if condition.operator == "finite":
        return math.isfinite(value)
    if condition.operator == "less_equal":
        assert condition.limit is not None
        return value <= condition.limit
    if condition.operator == "greater_equal":
        assert condition.limit is not None
        return value >= condition.limit
    if condition.operator == "between":
        assert condition.lower is not None and condition.upper is not None
        return condition.lower <= value <= condition.upper
    if condition.operator == "relative_error":
        assert condition.reference is not None and condition.tolerance is not None
        denominator = abs(condition.reference)
        error = abs(value - condition.reference)
        relative = error / denominator if denominator else error
        return relative <= condition.tolerance
    assert condition.operator == "absolute_balance"
    assert condition.limit is not None
    return abs(value) <= condition.limit


class AcceptanceEvaluator:
    def evaluate(
        self,
        plan: AcceptancePlan,
        metrics: DerivedMetrics,
    ) -> ResultReport:
        by_id = {item.observation_id: item for item in metrics.series}
        observations: list[ObservationResult] = []
        for request in plan.observation_requests:
            series = by_id.get(request.observation_id)
            if series is None:
                observations.append(
                    ObservationResult(
                        observation_id=request.observation_id,
                        status="UNAVAILABLE",
                    )
                )
                continue
            value, unit, refs = _latest_scalar(series)
            observations.append(
                ObservationResult(
                    observation_id=request.observation_id,
                    status=series.status,
                    latest_value=value,
                    unit=unit,
                    evidence_refs=refs,
                )
            )
        results: list[ConditionResult] = []
        missing: list[str] = []
        for condition in plan.conditions:
            series = by_id.get(condition.observation_id)
            value, unit, refs = (
                _latest_scalar(series)
                if series is not None and series.status != "UNAVAILABLE"
                else (None, None, ())
            )
            if value is None:
                detail = "required scalar metric is unavailable"
                missing.append(condition.observation_id)
                results.append(
                    ConditionResult(
                        condition_id=condition.condition_id,
                        observation_id=condition.observation_id,
                        status="NOT_EVALUATED",
                        detail=detail,
                    )
                )
                continue
            if unit != condition.unit:
                missing.append(condition.observation_id)
                results.append(
                    ConditionResult(
                        condition_id=condition.condition_id,
                        observation_id=condition.observation_id,
                        status="NOT_EVALUATED",
                        observed_value=value,
                        unit=unit,
                        detail=f"unit mismatch: expected {condition.unit}",
                        evidence_refs=refs,
                    )
                )
                continue
            passed = _matches(condition, value)
            results.append(
                ConditionResult(
                    condition_id=condition.condition_id,
                    observation_id=condition.observation_id,
                    status="PASS" if passed else "FAIL",
                    observed_value=value,
                    unit=unit,
                    detail=("condition satisfied" if passed else "condition not satisfied"),
                    evidence_refs=refs,
                )
            )
        if not results:
            verdict = "NOT_REQUESTED"
        elif any(item.status == "FAIL" for item in results):
            verdict = "FAIL"
        elif any(item.status == "NOT_EVALUATED" for item in results):
            verdict = "INCOMPLETE"
        else:
            verdict = "PASS"
        return ResultReport(
            verdict=verdict,
            acceptance_plan_sha256=plan.canonical_sha256(),
            observation_plan_sha256=metrics.observation_plan_sha256,
            run_facts_sha256=metrics.run_facts_sha256,
            derived_metrics_sha256=metrics.canonical_sha256(),
            conditions=tuple(results),
            observations=tuple(observations),
            missing_evidence=tuple(dict.fromkeys(missing)),
        )


__all__ = ["AcceptanceEvaluator"]
