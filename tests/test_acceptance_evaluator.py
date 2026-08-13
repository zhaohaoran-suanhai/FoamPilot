from __future__ import annotations

import pytest

from foampilot.acceptance import (
    AcceptanceCondition,
    AcceptanceEvaluator,
    AcceptancePlan,
    AcceptanceScope,
)
from foampilot.observations import ObservationRequest, ObservationScope, TimeSelection
from foampilot.postprocessing import DerivedMetrics, MetricSample, MetricSeries
from foampilot.simulation import FactEvidence


PROVENANCE = (FactEvidence(kind="user_quote", detail="explicit condition"),)


def _request(observation_id: str = "continuity") -> ObservationRequest:
    return ObservationRequest(
        observation_id=observation_id,
        kind="continuity",
        quantity="continuity",
        dimension="1",
        scope=ObservationScope(kind="global"),
        time_selection=TimeSelection(kind="latest"),
        provenance=PROVENANCE,
    )


def _condition(operator="less_equal", **updates) -> AcceptanceCondition:
    payload = {
        "condition_id": "continuity-limit",
        "observation_id": "continuity",
        "operator": operator,
        "limit": 1e-5 if operator in {"less_equal", "greater_equal", "absolute_balance"} else None,
        "unit": "1",
        "scope": AcceptanceScope(time="latest"),
        "provenance": PROVENANCE,
    }
    payload.update(updates)
    return AcceptanceCondition(**payload)


def _metrics(status="AVAILABLE", value=1e-6, unit="1", samples=None) -> DerivedMetrics:
    samples = (
        tuple(samples)
        if samples is not None
        else (
            (MetricSample(time=1, value=value, unit=unit, evidence_refs=("facts",)),)
            if status == "AVAILABLE"
            else ()
        )
    )
    return DerivedMetrics(
        run_facts_sha256="a" * 64,
        observation_plan_sha256="b" * 64,
        series=(
            MetricSeries(
                observation_id="continuity",
                quantity="continuity",
                dimension="1",
                scope=ObservationScope(kind="global"),
                status=status,
                samples=samples,
                detail=None if samples else "missing",
            ),
        ),
    )


def _plan(*conditions) -> AcceptancePlan:
    return AcceptancePlan(
        conditions=conditions,
        observation_requests=(_request(),),
    )


def test_missing_required_metric_is_incomplete_not_pass() -> None:
    report = AcceptanceEvaluator().evaluate(
        _plan(_condition()), _metrics(status="UNAVAILABLE")
    )
    assert report.verdict == "INCOMPLETE"
    assert report.conditions[0].status == "NOT_EVALUATED"


def test_partial_history_cannot_produce_an_acceptance_verdict() -> None:
    condition = _condition(
        limit=1.0,
        scope=AcceptanceScope(time="all"),
    )
    report = AcceptanceEvaluator().evaluate(
        _plan(condition),
        _metrics(status="PARTIAL", value=0.5),
    )

    assert report.verdict == "INCOMPLETE"
    assert report.conditions[0].status == "NOT_EVALUATED"


def test_partial_projection_cannot_claim_latest_coverage() -> None:
    report = AcceptanceEvaluator().evaluate(
        _plan(_condition(limit=1.0)),
        _metrics(status="PARTIAL", value=0.5),
    )

    assert report.verdict == "INCOMPLETE"


@pytest.mark.parametrize("operator", ["exists", "finite"])
def test_predicate_conditions_accept_finite_vector_samples(operator: str) -> None:
    samples = (
        MetricSample(
            time=1,
            value=(1.0, 2.0, 3.0),
            unit="1",
            evidence_refs=("vector",),
        ),
    )
    report = AcceptanceEvaluator().evaluate(
        _plan(_condition(operator, limit=None)),
        _metrics(samples=samples),
    )

    assert report.verdict == "PASS"
    assert report.conditions[0].observed_value is None


def test_observation_only_metric_never_changes_verdict() -> None:
    report = AcceptanceEvaluator().evaluate(_plan(), _metrics())
    assert report.verdict == "NOT_REQUESTED"
    assert report.observations[0].status == "AVAILABLE"


def test_condition_truth_table_and_unit_mismatch() -> None:
    evaluator = AcceptanceEvaluator()
    assert evaluator.evaluate(_plan(_condition()), _metrics(value=1e-5)).verdict == "PASS"
    assert evaluator.evaluate(_plan(_condition()), _metrics(value=1e-3)).verdict == "FAIL"
    assert evaluator.evaluate(_plan(_condition()), _metrics(unit="m/s")).verdict == "INCOMPLETE"


def test_between_relative_error_exists_and_finite() -> None:
    evaluator = AcceptanceEvaluator()
    cases = (
        (_condition("between", lower=0.0, upper=2.0), 1.0),
        (_condition("relative_error", reference=1.0, tolerance=0.1), 1.05),
        (_condition("exists", limit=None), 1.0),
        (_condition("finite", limit=None), 1.0),
    )
    for condition, value in cases:
        report = evaluator.evaluate(_plan(condition), _metrics(value=value))
        assert report.verdict == "PASS"


def test_all_scope_evaluates_every_sample_and_cannot_hide_an_earlier_failure() -> None:
    samples = (
        MetricSample(time=1, value=10.0, unit="1", evidence_refs=("first",)),
        MetricSample(time=2, value=0.0, unit="1", evidence_refs=("last",)),
    )
    condition = _condition(
        limit=1.0,
        scope=AcceptanceScope(time="all"),
    )
    report = AcceptanceEvaluator().evaluate(
        _plan(condition),
        _metrics(samples=samples),
    )
    assert report.verdict == "FAIL"
    assert report.conditions[0].observed_value == pytest.approx(10.0)


def test_range_scope_evaluates_only_samples_inside_closed_interval() -> None:
    samples = (
        MetricSample(time=0, value=10.0, unit="1", evidence_refs=("zero",)),
        MetricSample(time=1, value=0.5, unit="1", evidence_refs=("one",)),
        MetricSample(time=2, value=0.25, unit="1", evidence_refs=("two",)),
        MetricSample(time=3, value=10.0, unit="1", evidence_refs=("three",)),
    )
    condition = _condition(
        limit=1.0,
        scope=AcceptanceScope(time="range", start=1, end=2),
    )
    report = AcceptanceEvaluator().evaluate(
        _plan(condition),
        _metrics(samples=samples),
    )
    assert report.verdict == "PASS"


def test_range_scope_without_timed_samples_is_incomplete() -> None:
    condition = _condition(
        scope=AcceptanceScope(time="range", start=2, end=3),
    )
    report = AcceptanceEvaluator().evaluate(_plan(condition), _metrics())
    assert report.verdict == "INCOMPLETE"
    assert report.conditions[0].status == "NOT_EVALUATED"
