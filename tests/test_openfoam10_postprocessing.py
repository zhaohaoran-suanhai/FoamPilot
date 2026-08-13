from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from foampilot.evidence import ContinuityFact, RawCommandEvidence, ResidualFact, RunFacts
from foampilot.observations import EvidenceStrategy, ObservationItem, ObservationPlan, ObservationScope, TimeSelection
from foampilot.postprocessing import PostProcessingEngine, foundation10_calculators
from foampilot.simulation import FactEvidence


def _facts() -> RunFacts:
    now = datetime.now(timezone.utc)
    step = RawCommandEvidence(
        step_id="solve", stage="solve", executable="pisoFoam", argv=("pisoFoam",),
        return_code=0, started_at=now, finished_at=now, elapsed_seconds=1,
        timed_out=False, stdout_path="logs/solve.out", stderr_path="logs/solve.err",
        stdout_sha256="a" * 64, stderr_sha256="b" * 64, execution_backend="bubblewrap",
    )
    return RunFacts(
        run_id="run", attempt=1, plan_sha256="c" * 64,
        extractor_identities={"foundation10": "1"}, raw_steps=(step,),
        residuals=(ResidualFact(step_id="solve", simulation_time=1, field="p", initial=0.1, final=0.01, iterations=2),),
        continuity=(ContinuityFact(step_id="solve", simulation_time=1, local=1e-6, global_value=2e-7, cumulative=3e-6),),
        source_sha256={"logs/solve.out": "a" * 64, "logs/solve.err": "b" * 64},
    )


def _item(kind: str, unit: str, scope_kind: str = "global", names=()) -> ObservationItem:
    quantities = {
        "flow_rate": "volumetric_flow_rate",
        "pressure_difference": "pressure_difference",
        "region_average": "velocity",
    }
    dimensions = {
        "flow_rate": "0 3 -1 0 0 0 0",
        "pressure_difference": "0 2 -2 0 0 0 0",
        "region_average": "0 1 -1 0 0 0 0",
    }
    return ObservationItem(
        observation_id=kind,
        kind=kind,
        quantity=quantities.get(kind, kind),
        dimension=dimensions.get(kind, "1"),
        scope=ObservationScope(kind=scope_kind, names=names),
        time_selection=TimeSelection(kind="latest"),
        evidence_strategy=EvidenceStrategy(kind="run_facts" if kind in {"residual", "continuity"} else "postprocess_command", collector_id=None if kind in {"residual", "continuity"} else f"foundation10.{kind}"),
        provenance=(FactEvidence(kind="user_quote", detail=kind),),
    )


@pytest.mark.parametrize(
    ("kind", "unit", "scope_kind", "names"),
    [
        ("residual", "1", "global", ()),
        ("continuity", "1", "global", ()),
        ("flow_rate", "m3/s", "patch", ("outlet",)),
        ("pressure_difference", "m2/s2", "patch_pair", ("inlet", "outlet")),
        ("region_average", "m/s", "cell_zone", ("porous",)),
        ("force", "N", "patch", ("wall",)),
        ("heat_flux", "W", "patch", ("hot",)),
    ],
)
def test_first_party_metric_family(tmp_path: Path, kind, unit, scope_kind, names) -> None:
    item = _item(kind, unit, scope_kind, names)
    artifacts = {}
    if kind not in {"residual", "continuity"}:
        path = tmp_path / f"{kind}.json"
        value = -2.0 if kind == "flow_rate" else 2.0
        if kind == "region_average":
            value = [2.0, 0.0, 0.0]
        path.write_text(json.dumps({"unit": unit, "samples": [{"time": 1, "value": value}]}), encoding="utf-8")
        artifacts[item.observation_id] = path
    metrics = PostProcessingEngine(calculators=foundation10_calculators()).derive(
        ObservationPlan(items=(item,)), _facts(), tmp_path, artifacts
    )
    series = metrics.require(kind)
    assert series.status == "AVAILABLE"
    assert series.samples[-1].unit == unit
    assert series.samples[-1].evidence_refs
    if kind == "flow_rate":
        assert series.samples[-1].value == pytest.approx(2.0)


def test_missing_structured_postprocess_output_is_unavailable(tmp_path: Path) -> None:
    item = _item("force", "N", "patch", ("wall",))
    metrics = PostProcessingEngine(calculators=foundation10_calculators()).derive(
        ObservationPlan(items=(item,)), _facts(), tmp_path
    )
    assert metrics.require("force").status == "UNAVAILABLE"


def test_region_average_velocity_magnitude_is_explicit_in_contract(tmp_path: Path) -> None:
    item = _item("region_average", "m/s", "cell_zone", ("porous",))
    item = item.model_copy(update={"quantity": "velocity_magnitude"})
    path = tmp_path / "region_average.json"
    path.write_text(
        json.dumps({"unit": "m/s", "samples": [{"time": 1, "value": [3, 4, 0]}]}),
        encoding="utf-8",
    )

    metrics = PostProcessingEngine(calculators=foundation10_calculators()).derive(
        ObservationPlan(items=(item,)),
        _facts(),
        tmp_path,
        {item.observation_id: path},
    )

    assert metrics.require("region_average").samples[-1].value == pytest.approx(5.0)


def test_signed_flow_contract_preserves_openfoam_patch_orientation(tmp_path: Path) -> None:
    item = _item("flow_rate", "m3/s", "patch", ("inlet",)).model_copy(
        update={"quantity": "signed_volumetric_flow_rate"}
    )
    path = tmp_path / "flow.json"
    path.write_text(
        json.dumps({"unit": "m3/s", "samples": [{"time": 1, "value": -2.0}]}),
        encoding="utf-8",
    )
    metrics = PostProcessingEngine(calculators=foundation10_calculators()).derive(
        ObservationPlan(items=(item,)),
        _facts(),
        tmp_path,
        {item.observation_id: path},
    )
    assert metrics.require("flow_rate").samples[-1].value == pytest.approx(-2.0)


def test_region_average_uses_contract_specific_unit(tmp_path: Path) -> None:
    item = _item("region_average", "K", "cell_zone", ("solid",)).model_copy(
        update={"quantity": "temperature", "dimension": "0 0 0 1 0 0 0"}
    )
    path = tmp_path / "temperature.json"
    path.write_text(
        json.dumps({"unit": "K", "samples": [{"time": 1, "value": 300.0}]}),
        encoding="utf-8",
    )

    metrics = PostProcessingEngine(calculators=foundation10_calculators()).derive(
        ObservationPlan(items=(item,)),
        _facts(),
        tmp_path,
        {item.observation_id: path},
    )

    assert metrics.require("region_average").samples[-1].unit == "K"


@pytest.mark.parametrize("kind", ["residual", "continuity"])
def test_run_fact_history_over_projection_limit_is_partial(
    tmp_path: Path,
    kind: str,
) -> None:
    facts = _facts()
    if kind == "residual":
        updates = {
            "residuals": tuple(
                ResidualFact(
                    step_id="solve",
                    simulation_time=float(index),
                    field="p",
                    initial=0.1,
                    final=0.01,
                    iterations=2,
                )
                for index in range(1001)
            )
        }
    else:
        updates = {
            "continuity": tuple(
                ContinuityFact(
                    step_id="solve",
                    simulation_time=float(index),
                    local=1e-6,
                    global_value=2e-7,
                    cumulative=3e-6,
                )
                for index in range(1001)
            )
        }
    metrics = PostProcessingEngine(calculators=foundation10_calculators()).derive(
        ObservationPlan(items=(_item(kind, "1"),)),
        facts.model_copy(update=updates),
        tmp_path,
    )

    series = metrics.require(kind)
    assert series.status == "PARTIAL"
    assert len(series.samples) == 1000
    assert "1000" in series.detail
