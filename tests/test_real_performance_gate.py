from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

import pytest

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.performance import PerformanceSummary
from foampilot.plans import ExecutionPlan
from foampilot.runtime import PlanRunResult
from foampilot.tasks import load_task_spec
from tests.test_native_case_generation import RecordingModel
from tests.support.runtime import real_runtime_config


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "examples/tasks/non-tutorial-side-driven-box.yaml"
PLAN = ROOT / "tests/fixtures/gates/non-tutorial-side-driven-plan.json"


def _first_workflow_time(run_dir: Path) -> datetime:
    payload = json.loads(
        (run_dir / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    return datetime.fromisoformat(payload["occurred_at"])


@pytest.mark.skipif(
    os.environ.get("FOAMPILOT_RUN_REAL_PERFORMANCE") != "1",
    reason="real Performance v1 OpenFOAM gate is opt-in",
)
def test_verified_plan_and_mesh_cache_reach_real_solver_quickly(
    tmp_path: Path,
) -> None:
    task = load_task_spec(TASK)
    plan = ExecutionPlan.model_validate_json(PLAN.read_text(encoding="utf-8"))
    store = ArtifactStore(tmp_path / "runs")
    cache = tmp_path / "derived-cache"
    model = RecordingModel([plan])
    runtime = real_runtime_config()

    cold = NativeAgent(
        gateway=model,
        runtime_config=runtime,
        artifact_store=store,
    ).solve(task, derived_cache=cache)

    assert cold.status == "RUN_COMPLETED", cold.summary
    assert len(model.requests) == 1
    assert store.verify(cold.run_dir) == []

    warm = NativeAgent(
        gateway=None,
        runtime_config=runtime,
        artifact_store=store,
    ).solve(
        task,
        reuse_verified_plan=cold.run_dir,
        derived_cache=cache,
    )

    assert warm.status == "RUN_COMPLETED", warm.summary
    assert store.verify(warm.run_dir) == []
    performance = PerformanceSummary.model_validate_json(
        (warm.run_dir / "performance-summary.json").read_text(encoding="utf-8")
    )
    assert performance.model.logical_requests == 0
    assert performance.model.transport_attempts == 0
    assert performance.reuse.plan == "hit"
    assert performance.reuse.mesh == "hit"
    assert performance.time_to_first_openfoam_command_seconds is not None
    assert performance.time_to_first_openfoam_command_seconds <= 5

    result = PlanRunResult.model_validate_json(
        (warm.run_dir / "attempt-01/run-result.json").read_text(encoding="utf-8")
    )
    assert [step.command[0] for step in result.steps] == ["checkMesh", "icoFoam"]
    solve_step = result.steps[-1]
    assert (
        solve_step.started_at - _first_workflow_time(warm.run_dir)
    ).total_seconds() <= 30
