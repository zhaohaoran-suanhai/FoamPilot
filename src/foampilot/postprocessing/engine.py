"""Extension-driven metric derivation isolated by observation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol

from foampilot.evidence import RunFacts
from foampilot.observations import ObservationItem, ObservationPlan

from .models import DerivedMetrics, MetricSeries


class MetricCalculator(Protocol):
    def calculate(
        self,
        item: ObservationItem,
        run_facts: RunFacts,
        case_root: Path,
        artifacts: Mapping[str, Path],
    ) -> MetricSeries: ...


def _hash_model(value) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class PostProcessingEngine:
    def __init__(
        self,
        *,
        calculators: Mapping[str, MetricCalculator]
        | Iterable[tuple[str, MetricCalculator]],
    ) -> None:
        entries = (
            list(calculators.items())
            if isinstance(calculators, Mapping)
            else list(calculators)
        )
        if len({key for key, _ in entries}) != len(entries):
            raise ValueError("POSTPROCESS_CALCULATOR_DUPLICATE")
        self.calculators = dict(entries)

    def derive(
        self,
        plan: ObservationPlan,
        run_facts: RunFacts,
        case_root: str | Path,
        artifacts: Mapping[str, Path] | None = None,
        preflight_errors: Mapping[str, str] | None = None,
    ) -> DerivedMetrics:
        root = Path(case_root).resolve()
        values: list[MetricSeries] = []
        for item in plan.items:
            if item.observation_id in (preflight_errors or {}):
                values.append(
                    MetricSeries(
                        observation_id=item.observation_id,
                        quantity=item.quantity,
                        dimension=item.dimension,
                        scope=item.scope,
                        status="UNAVAILABLE",
                        detail=str(preflight_errors[item.observation_id]),
                    )
                )
                continue
            calculator = self.calculators.get(item.kind)
            if calculator is None:
                values.append(
                    MetricSeries(
                        observation_id=item.observation_id,
                        quantity=item.quantity,
                        dimension=item.dimension,
                        scope=item.scope,
                        status="UNAVAILABLE",
                        detail=f"no calculator registered for {item.kind}",
                    )
                )
                continue
            try:
                result = calculator.calculate(
                    item,
                    run_facts,
                    root,
                    artifacts or {},
                )
                if result.observation_id != item.observation_id:
                    raise ValueError("calculator returned wrong observation ID")
                values.append(result)
            except Exception as error:
                values.append(
                    MetricSeries(
                        observation_id=item.observation_id,
                        quantity=item.quantity,
                        dimension=item.dimension,
                        scope=item.scope,
                        status="UNAVAILABLE",
                        detail=f"{type(error).__name__}: {error}",
                    )
                )
        return DerivedMetrics(
            run_facts_sha256=_hash_model(run_facts),
            observation_plan_sha256=plan.canonical_sha256(),
            series=tuple(values),
        )


__all__ = ["MetricCalculator", "PostProcessingEngine"]
