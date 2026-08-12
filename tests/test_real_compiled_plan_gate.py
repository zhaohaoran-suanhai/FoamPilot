from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.plans import ExecutionPlan
from tests.support.runtime import real_runtime_config
from tests.test_native_case_generation import RecordingModel, _plan, _task


@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_OPENFOAM") != "1",
    reason="real OpenFOAM Runner integration is opt-in",
)
def test_real_foundation10_solve_uses_compiled_v4_plan(tmp_path: Path) -> None:
    outcome = NativeAgent(
        gateway=RecordingModel([_plan()]),
        runtime_config=real_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
    ).solve(_task())

    assert outcome.status == "RUN_COMPLETED"
    bundle = json.loads(
        (outcome.run_dir / "case-bundle.json").read_text(encoding="utf-8")
    )
    assert "commands" not in bundle
    plan = ExecutionPlan.model_validate_json(
        (outcome.run_dir / "execution-plan.json").read_text(encoding="utf-8")
    )
    assert plan.schema_version == 4
    assert plan.compiled_from_design_sha256 == json.loads(
        (outcome.run_dir / "case-design.json").read_text(encoding="utf-8")
    )["design_sha256"]
    assert plan.compiler_identities
    assert [item.executable for item in plan.commands] == [
        "blockMesh",
        "checkMesh",
        "icoFoam",
    ]
    assert ArtifactStore(outcome.run_dir.parent).verify(outcome.run_dir) == []
