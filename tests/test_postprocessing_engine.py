from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from foampilot.evidence import RawCommandEvidence, RunFacts
from foampilot.observations import EvidenceStrategy, ObservationItem, ObservationPlan, ObservationScope, TimeSelection
from foampilot.postprocessing import MetricSample, MetricSeries, PostProcessingEngine
from foampilot.simulation import FactEvidence


def _item(observation_id: str, kind: str = "continuity") -> ObservationItem:
    return ObservationItem(
        observation_id=observation_id,
        kind=kind,
        quantity=kind,
        dimension="1",
        scope=ObservationScope(kind="global"),
        time_selection=TimeSelection(kind="history"),
        evidence_strategy=EvidenceStrategy(kind="run_facts"),
        provenance=(FactEvidence(kind="user_quote", detail=kind),),
    )


def _facts() -> RunFacts:
    now = datetime.now(timezone.utc)
    step = RawCommandEvidence(
        step_id="solve",
        stage="solve",
        executable="pisoFoam",
        argv=("pisoFoam",),
        return_code=0,
        started_at=now,
        finished_at=now,
        elapsed_seconds=0,
        timed_out=False,
        stdout_path="attempt-01/log.stdout",
        stderr_path="attempt-01/log.stderr",
        stdout_sha256="a" * 64,
        stderr_sha256="b" * 64,
        execution_backend="bubblewrap",
    )
    return RunFacts(
        run_id="run-test",
        attempt=1,
        plan_sha256="c" * 64,
        extractor_identities={"test": "1"},
        raw_steps=(step,),
        source_sha256={
            "attempt-01/log.stdout": "a" * 64,
            "attempt-01/log.stderr": "b" * 64,
        },
    )


class GoodCalculator:
    def calculate(self, item, run_facts, case_root, artifacts):
        del run_facts, case_root, artifacts
        return MetricSeries(
            observation_id=item.observation_id,
            quantity=item.quantity,
            dimension=item.dimension,
            scope=item.scope,
            status="AVAILABLE",
            samples=(MetricSample(time=1, value=0.01, unit="1", evidence_refs=("run-facts.json",)),),
        )


class FailingCalculator:
    def calculate(self, item, run_facts, case_root, artifacts):
        del item, run_facts, case_root, artifacts
        raise ValueError("missing source")


def test_metric_carries_evidence_provenance(tmp_path: Path) -> None:
    engine = PostProcessingEngine(calculators={"continuity": GoodCalculator()})
    metrics = engine.derive(
        ObservationPlan(items=(_item("continuity"),)), _facts(), tmp_path
    )

    value = metrics.require("continuity")
    assert value.samples[-1].unit == "1"
    assert value.samples[-1].evidence_refs


def test_one_failed_metric_does_not_erase_other_metrics(tmp_path: Path) -> None:
    engine = PostProcessingEngine(
        calculators={
            "continuity": GoodCalculator(),
            "residual": FailingCalculator(),
        }
    )
    plan = ObservationPlan(
        items=(_item("good"), _item("missing", kind="residual"))
    )

    metrics = engine.derive(plan, _facts(), tmp_path)

    assert metrics.require("good").status == "AVAILABLE"
    assert metrics.require("missing").status == "UNAVAILABLE"
    assert "missing source" in metrics.require("missing").detail


def test_duplicate_calculator_registration_and_nonfinite_values_fail() -> None:
    with pytest.raises(ValueError, match="DUPLICATE"):
        PostProcessingEngine(calculators=[("continuity", GoodCalculator()), ("continuity", GoodCalculator())])
    with pytest.raises(Exception):
        MetricSample(value=float("nan"), unit="1", evidence_refs=("x",))

