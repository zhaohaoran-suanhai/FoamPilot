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
    return ObservationItem(
        observation_id=kind, kind=kind, quantity=kind, dimension="1",
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

