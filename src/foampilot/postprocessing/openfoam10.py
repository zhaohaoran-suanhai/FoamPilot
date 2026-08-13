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
        facts = run_facts.residuals
        truncated = len(facts) > 1000
        samples = tuple(
            MetricSample(
                time=fact.simulation_time,
                value=fact.initial,
                unit="1",
                evidence_refs=(_fact_ref(run_facts), fact.step_id),
            )
            for fact in facts[-1000:]
        )
        return MetricSeries(
            observation_id=item.observation_id, quantity=item.quantity,
            dimension=item.dimension, scope=item.scope,
            status=("PARTIAL" if truncated else "AVAILABLE") if samples else "UNAVAILABLE",
            samples=samples,
            detail=(
                "bounded projection retained the latest 1000 samples"
                if truncated
                else None if samples else "RunFacts contains no residual samples"
            ),
        )


class ContinuityCalculator:
    def calculate(self, item, run_facts, case_root, artifacts):
        del case_root, artifacts
        facts = run_facts.continuity
        truncated = len(facts) > 1000
        samples = tuple(
            MetricSample(
                time=fact.simulation_time,
                value=abs(fact.cumulative if fact.cumulative is not None else (fact.global_value or 0.0)),
                unit="1",
                evidence_refs=(_fact_ref(run_facts), fact.step_id),
            )
            for fact in facts[-1000:]
        )
        return MetricSeries(
            observation_id=item.observation_id, quantity=item.quantity,
            dimension=item.dimension, scope=item.scope,
            status=("PARTIAL" if truncated else "AVAILABLE") if samples else "UNAVAILABLE",
            samples=samples,
            detail=(
                "bounded projection retained the latest 1000 samples"
                if truncated
                else None if samples else "RunFacts contains no continuity samples"
            ),
        )


class StructuredOutputCalculator:
    def __init__(
        self,
        *,
        expected_unit: str | None = None,
    ) -> None:
        self.expected_unit = expected_unit

    def calculate(self, item, run_facts, case_root, artifacts):
        del run_facts, case_root
        path = artifacts.get(item.observation_id)
        if path is None or path.is_symlink() or not path.is_file():
            raise FileNotFoundError("declared structured metric output is unavailable")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") == "UNAVAILABLE":
            raise ValueError(str(payload.get("detail") or "observation unavailable"))
        declared_status = payload.get("status", "AVAILABLE")
        if declared_status not in {"AVAILABLE", "PARTIAL"}:
            raise ValueError(f"invalid structured metric status: {declared_status}")
        expected_unit = self.expected_unit
        if item.kind in {"flow_rate", "pressure_difference", "region_average"}:
            from foampilot.observations.openfoam10 import _metric_unit

            expected_unit = _metric_unit(item)
        if payload.get("unit") != expected_unit:
            raise ValueError(f"metric unit mismatch: expected {expected_unit}")
        digest = sha256(path.read_bytes()).hexdigest()
        samples = []
        from foampilot.observations import first_party_observation_registry

        contract = first_party_observation_registry().resolve(
            item.kind
        ).resolve_quantity_contract(item.quantity, item.dimension)
        for raw in payload.get("samples", []):
            value = raw["value"]
            if contract is not None and contract.value_shape == "vector":
                if not isinstance(value, list):
                    raise ValueError("metric contract requires a vector value")
            elif isinstance(value, list):
                raise ValueError("metric contract requires a scalar value")
            if contract is not None and contract.reduction == "magnitude":
                if isinstance(value, list):
                    value = sum(float(component) ** 2 for component in value) ** 0.5
                else:
                    value = abs(float(value))
            samples.append(
                MetricSample(
                    time=raw.get("time"), value=value, unit=expected_unit,
                    evidence_refs=(path.name, f"sha256:{digest}"),
                )
            )
        return MetricSeries(
            observation_id=item.observation_id, quantity=item.quantity,
            dimension=item.dimension, scope=item.scope, status=declared_status,
            samples=tuple(samples),
            detail=payload.get("detail"),
        )


def foundation10_calculators():
    return {
        "residual": ResidualCalculator(),
        "continuity": ContinuityCalculator(),
        "flow_rate": StructuredOutputCalculator(),
        "pressure_difference": StructuredOutputCalculator(expected_unit="m2/s2"),
        "region_average": StructuredOutputCalculator(),
        "force": StructuredOutputCalculator(expected_unit="N"),
        "heat_flux": StructuredOutputCalculator(expected_unit="W"),
    }


__all__ = ["foundation10_calculators"]
