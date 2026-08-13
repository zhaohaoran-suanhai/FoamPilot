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


def _latest_sample(series: MetricSeries):
    if not series.samples:
        return None
    timed = [sample for sample in series.samples if sample.time is not None]
    return max(timed, key=lambda sample: float(sample.time)) if timed else series.samples[-1]


def _scoped_samples(condition: AcceptanceCondition, series: MetricSeries):
    samples = list(series.samples)
    scope = condition.scope
    if scope.time == "range":
        assert scope.start is not None and scope.end is not None
        return [
            sample
            for sample in samples
            if sample.time is not None and scope.start <= sample.time <= scope.end
        ]
    if scope.time in {"latest", "final"}:
        timed = [sample for sample in samples if sample.time is not None]
        if timed:
            latest = max(float(sample.time) for sample in timed)
            return [sample for sample in timed if float(sample.time) == latest]
        return samples[-1:]
    return samples


def _matches(
    condition: AcceptanceCondition,
    value: float | tuple[float, ...],
) -> bool:
    if condition.operator == "exists":
        return True
    if condition.operator == "finite":
        values = value if isinstance(value, tuple) else (value,)
        return all(math.isfinite(component) for component in values)
    if isinstance(value, tuple):
        return False
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
            sample = _latest_sample(series)
            observations.append(
                ObservationResult(
                    observation_id=request.observation_id,
                    status=series.status,
                    latest_value=(sample.value if sample is not None else None),
                    unit=(sample.unit if sample is not None else None),
                    evidence_refs=(sample.evidence_refs if sample is not None else ()),
                )
            )
        results: list[ConditionResult] = []
        missing: list[str] = []
        for condition in plan.conditions:
            series = by_id.get(condition.observation_id)
            selected = (
                _scoped_samples(condition, series)
                if series is not None and series.status == "AVAILABLE"
                else []
            )
            accepts_vectors = condition.operator in {"exists", "finite"}
            supported_samples = [
                sample
                for sample in selected
                if accepts_vectors or isinstance(sample.value, (int, float))
            ]
            if not selected or len(supported_samples) != len(selected):
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
            if any(sample.unit != condition.unit for sample in supported_samples):
                sample = supported_samples[-1]
                missing.append(condition.observation_id)
                results.append(
                    ConditionResult(
                        condition_id=condition.condition_id,
                        observation_id=condition.observation_id,
                        status="NOT_EVALUATED",
                        observed_value=(
                            float(sample.value)
                            if isinstance(sample.value, (int, float))
                            else None
                        ),
                        unit=sample.unit,
                        detail=f"unit mismatch: expected {condition.unit}",
                        evidence_refs=sample.evidence_refs,
                    )
                )
                continue
            evaluated = [
                (sample, _matches(condition, sample.value))
                for sample in supported_samples
            ]
            failed = next(
                ((sample, passed) for sample, passed in evaluated if not passed),
                None,
            )
            sample, passed = failed or evaluated[-1]
            results.append(
                ConditionResult(
                    condition_id=condition.condition_id,
                    observation_id=condition.observation_id,
                    status="PASS" if passed else "FAIL",
                    observed_value=(
                        float(sample.value)
                        if isinstance(sample.value, (int, float))
                        else None
                    ),
                    unit=sample.unit,
                    detail=("condition satisfied" if passed else "condition not satisfied"),
                    evidence_refs=sample.evidence_refs,
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
