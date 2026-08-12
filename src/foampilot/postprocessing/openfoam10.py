"""Foundation v10 metric calculators over RunFacts or declared outputs."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from foampilot.evidence import RunFacts
from foampilot.observations import ObservationItem

from .models import MetricSample, MetricSeries


def _fact_ref(run_facts: RunFacts) -> str:
    return f"attempt-{run_facts.attempt:02d}/run-facts.json"


class ResidualCalculator:
    def calculate(self, item, run_facts, case_root, artifacts):
        del case_root, artifacts
        samples = tuple(
            MetricSample(
                time=fact.simulation_time,
                value=fact.initial,
                unit="1",
                evidence_refs=(_fact_ref(run_facts), fact.step_id),
            )
            for fact in run_facts.residuals
        )
        return MetricSeries(
            observation_id=item.observation_id, quantity=item.quantity,
            dimension=item.dimension, scope=item.scope,
            status="AVAILABLE" if samples else "UNAVAILABLE", samples=samples,
            detail=None if samples else "RunFacts contains no residual samples",
        )


class ContinuityCalculator:
    def calculate(self, item, run_facts, case_root, artifacts):
        del case_root, artifacts
        samples = tuple(
            MetricSample(
                time=fact.simulation_time,
                value=abs(fact.cumulative if fact.cumulative is not None else (fact.global_value or 0.0)),
                unit="1",
                evidence_refs=(_fact_ref(run_facts), fact.step_id),
            )
            for fact in run_facts.continuity
        )
        return MetricSeries(
            observation_id=item.observation_id, quantity=item.quantity,
            dimension=item.dimension, scope=item.scope,
            status="AVAILABLE" if samples else "UNAVAILABLE", samples=samples,
            detail=None if samples else "RunFacts contains no continuity samples",
        )


class StructuredOutputCalculator:
    def __init__(self, *, expected_unit: str, outward_flow: bool = False) -> None:
        self.expected_unit = expected_unit
        self.outward_flow = outward_flow

    def calculate(self, item, run_facts, case_root, artifacts):
        del run_facts, case_root
        path = artifacts.get(item.observation_id)
        if path is None or path.is_symlink() or not path.is_file():
            raise FileNotFoundError("declared structured metric output is unavailable")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("unit") != self.expected_unit:
            raise ValueError(f"metric unit mismatch: expected {self.expected_unit}")
        digest = sha256(path.read_bytes()).hexdigest()
        samples = []
        for raw in payload.get("samples", []):
            value = raw["value"]
            if self.outward_flow:
                # OpenFOAM patch flux is outward-positive; report inlet/outlet
                # throughput as a positive magnitude for balance comparisons.
                value = abs(float(value))
            samples.append(
                MetricSample(
                    time=raw.get("time"), value=value, unit=self.expected_unit,
                    evidence_refs=(path.name, f"sha256:{digest}"),
                )
            )
        return MetricSeries(
            observation_id=item.observation_id, quantity=item.quantity,
            dimension=item.dimension, scope=item.scope, status="AVAILABLE",
            samples=tuple(samples),
        )


def foundation10_calculators():
    return {
        "residual": ResidualCalculator(),
        "continuity": ContinuityCalculator(),
        "flow_rate": StructuredOutputCalculator(expected_unit="m3/s", outward_flow=True),
        "pressure_difference": StructuredOutputCalculator(expected_unit="m2/s2"),
        "region_average": StructuredOutputCalculator(expected_unit="m/s"),
        "force": StructuredOutputCalculator(expected_unit="N"),
        "heat_flux": StructuredOutputCalculator(expected_unit="W"),
    }


__all__ = ["foundation10_calculators"]
