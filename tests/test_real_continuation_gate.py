from __future__ import annotations

import os
from pathlib import Path
import re

import pytest

from foampilot.agent import NativeAgent
from foampilot.agent.repair_patch import RepairPatch
from foampilot.artifacts import ArtifactStore
from foampilot.models import (
    BackendFailureKind,
    BackendRegistry,
    ModelGateway,
)
from foampilot.plans import ExecutionPlan, GeneratedFile
from foampilot.tasks import load_task_spec
from tests.support.model_gateway import (
    ScriptedBackend,
    backend_error,
    valid_response,
)
from tests.support.runtime import real_runtime_config


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "examples/tasks/non-tutorial-side-driven-box.yaml"
PLAN = (
    ROOT
    / "tests/fixtures/gates/non-tutorial-side-driven-plan.json"
)


def _gateway(events) -> ModelGateway:
    registry = BackendRegistry()
    registry.register(ScriptedBackend(events), priority=10)
    return ModelGateway(
        registry=registry,
        sleep=lambda seconds: None,
    )


@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_OPENFOAM") != "1",
    reason="real OpenFOAM Runner integration is opt-in",
)
def test_solver_failure_backend_deferred_resume_repair_real_gate(
    tmp_path: Path,
) -> None:
    task = load_task_spec(TASK)
    valid_plan = ExecutionPlan.model_validate_json(
        PLAN.read_text(encoding="utf-8")
    )
    valid_schemes = next(
        item.content
        for item in valid_plan.files
        if item.path == "system/fvSchemes"
    )
    invalid_schemes = re.sub(
        r"(?m)^\s*div\(phi,U\)\s+[^;]+;\s*$",
        "",
        valid_schemes,
    )
    invalid_files = [
        (
            GeneratedFile(
                path=item.path,
                content=invalid_schemes,
            )
            if item.path == "system/fvSchemes"
            else item
        )
        for item in valid_plan.files
    ]
    invalid_plan = valid_plan.model_copy(
        update={"files": invalid_files}
    )
    repair = RepairPatch(
        because="The icoFoam log reports that div(phi,U) has no scheme.",
        evidence=["div(phi,U) scheme is undefined in fvSchemes"],
        file_operations=[
            {
                "operation": "replace",
                "path": "system/fvSchemes",
                "content": valid_schemes,
            }
        ],
        command_operations=[],
        expected_check="icoFoam reaches End at time 1.",
        stable_control="Mesh, fields, viscosity, and commands are unchanged.",
    )
    overloads = [
        backend_error(
            BackendFailureKind.OVERLOADED,
            retryable=True,
        )
        for _ in range(3)
    ]
    store = ArtifactStore(tmp_path / "runs")
    runtime = real_runtime_config()
    parent = NativeAgent(
        gateway=_gateway(
            [
                valid_response(invalid_plan.model_dump_json()),
                *overloads,
            ]
        ),
        runtime_config=runtime,
        artifact_store=store,
    ).solve(task)
    parent_manifest = (
        parent.run_dir / "artifact-manifest.json"
    ).read_bytes()

    assert parent.summary.workflow_state == "DEFERRED"
    assert parent.summary.native_status == "SOLVER_FAILED"
    assert parent.summary.primary_failure is not None
    assert parent.summary.primary_failure.domain == "solver"
    assert parent.summary.terminal_blocker is not None
    assert parent.summary.terminal_blocker.domain == "backend"

    child = NativeAgent(
        gateway=_gateway(
            [valid_response(repair.model_dump_json())]
        ),
        runtime_config=runtime,
        artifact_store=store,
    ).resume(parent.run_dir)

    assert child.summary.workflow_state == "COMPLETED"
    assert child.summary.native_status == "PUBLIC_VALIDATION_PASS"
    assert store.verify(parent.run_dir) == []
    assert store.verify(child.run_dir) == []
    assert (
        parent.run_dir / "artifact-manifest.json"
    ).read_bytes() == parent_manifest
