from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.environment import EnvironmentSnapshot
from foampilot.models import (
    ModelGateway,
    load_backend_registry,
)
from foampilot.plans import ExecutionPlan
from foampilot.runtime.protection import runtime_protected_paths
from foampilot.runtime.sandbox import build_sandbox_argv
from foampilot.tasks import load_task_spec
from tests.test_native_case_generation import RecordingModel
from tests.support.runtime import real_runtime_config


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = PACKAGE_ROOT / "examples/tasks"
TASKS = (
    TASK_ROOT / "non-tutorial-side-driven-box.yaml",
    TASK_ROOT / "non-tutorial-two-phase-column-collapse.yaml",
)
FROZEN_PLAN = (
    PACKAGE_ROOT / "tests/fixtures/gates/non-tutorial-side-driven-plan.json"
)


@pytest.mark.skipif(
    not os.environ.get("FOAMPILOT_OPENFOAM_ROOT"),
    reason="FOAMPILOT_OPENFOAM_ROOT is required for the real frozen-plan gate",
)
def test_real_frozen_plan_executes_with_requested_runtime_policy(
    tmp_path: Path,
) -> None:
    runtime = real_runtime_config()
    plan = ExecutionPlan.model_validate_json(
        FROZEN_PLAN.read_text(encoding="utf-8")
    )
    store = ArtifactStore(tmp_path / "runs")

    outcome = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=runtime,
        artifact_store=store,
    ).solve(load_task_spec(TASKS[0]))

    assert outcome.status == "RUN_COMPLETED", outcome.summary
    assert store.verify(outcome.run_dir) == []
    policy = json.loads(
        (outcome.run_dir / "execution-policy.json").read_text(
            encoding="utf-8"
        )
    )
    if runtime.isolation == "trusted_host":
        assert policy["actual_backend"] == "host"
        assert "当前 attempt 在宿主机" in policy["unisolated_warning"]
    elif runtime.isolation == "sandbox_required":
        assert policy["actual_backend"] == "bubblewrap"
        result = json.loads(
            (outcome.run_dir / "attempt-01/run-result.json").read_text(
                encoding="utf-8"
            )
        )
        assert {
            step["execution_backend"] for step in result["steps"]
        } == {"bubblewrap"}

        environment = EnvironmentSnapshot.model_validate_json(
            (outcome.run_dir / "environment.json").read_text(
                encoding="utf-8"
            )
        )
        assert environment.tutorial_root is not None
        case = outcome.run_dir / "attempt-01/case"
        launch = build_sandbox_argv(
            config=runtime,
            environment=environment,
            case_dir=case,
            protected_paths=runtime_protected_paths((), environment),
            memory_mib=256,
            cpu_seconds=5,
            typed_argv=(
                "/usr/bin/test",
                "!",
                "-e",
                str(environment.tutorial_root),
            ),
        )
        completed = subprocess.run(
            launch.argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(
    not os.environ.get("FOAMPILOT_OPENFOAM_ROOT"),
    reason="FOAMPILOT_OPENFOAM_ROOT is required for the fallback risk gate",
)
def test_real_preferred_fallback_allows_low_risk_and_blocks_dynamic_code(
    tmp_path: Path,
) -> None:
    base_runtime = real_runtime_config()
    runtime = base_runtime.model_copy(
        update={
            "isolation": "sandbox_preferred",
            "bubblewrap": Path("/tmp/foampilot-intentionally-missing-bwrap"),
        }
    )
    plan = ExecutionPlan.model_validate_json(
        FROZEN_PLAN.read_text(encoding="utf-8")
    )
    task = load_task_spec(TASKS[0])

    low_store = ArtifactStore(tmp_path / "low-risk-runs")
    low = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=runtime,
        artifact_store=low_store,
    ).solve(task)
    assert low.status == "RUN_COMPLETED", low.summary
    low_policy = json.loads(
        (low.run_dir / "execution-policy.json").read_text(encoding="utf-8")
    )
    assert low_policy["actual_backend"] == "host"
    assert low_policy["code"] == "HOST_FALLBACK_SELECTED"
    assert low_store.verify(low.run_dir) == []

    coded_files = [
        item.model_copy(
            update={
                "content": (
                    item.content
                    + "\n#codeStream\n{\ncode #{ int generated = 1; #};\n}\n"
                )
            }
        )
        if item.path == "system/controlDict"
        else item
        for item in plan.files
    ]
    coded_plan = plan.model_copy(update={"files": coded_files})
    high_store = ArtifactStore(tmp_path / "high-risk-runs")
    high = NativeAgent(
        gateway=RecordingModel([coded_plan]),
        runtime_config=runtime,
        artifact_store=high_store,
    ).solve(task)

    assert high.status == "BLOCKED_ENVIRONMENT"
    assert high.summary.primary_failure is not None
    assert high.summary.primary_failure.code == "HOST_DYNAMIC_CODE_BLOCKED"
    risk = json.loads(
        (high.run_dir / "attempt-01/execution-risk-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert risk["risk_level"] == "high"
    assert risk["policy_decision"] == "HOST_DYNAMIC_CODE_BLOCKED"
    assert not (high.run_dir / "attempt-01/run-result.json").exists()
    assert high_store.verify(high.run_dir) == []


@pytest.mark.parametrize(
    "task_path",
    TASKS,
    ids=("side-driven-box", "two-phase-column"),
)
def test_real_native_task_contracts_are_valid(task_path: Path) -> None:
    task = load_task_spec(task_path)

    assert task.openfoam_target.version == "10"
    assert task.resource_budget.max_mpi_ranks == 1
    assert task.protected_paths == []


def test_two_phase_public_prompt_does_not_prescribe_application() -> None:
    task = load_task_spec(TASKS[1])
    public_text = f"{task.title}\n{task.prompt}"

    assert "interFoam" not in public_text
    assert "setFields" not in public_text


@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_MODEL") != "1",
    reason="real model/OpenFOAM integration is opt-in",
)
@pytest.mark.parametrize(
    "task_path",
    TASKS,
    ids=("side-driven-box", "two-phase-column"),
)
def test_real_model_authors_and_solves_native_case(
    tmp_path: Path,
    task_path: Path,
) -> None:
    model_name = os.environ.get("OFKIT_CODEX_MODEL", "gpt-5.6-sol")
    config = real_runtime_config()
    outcome = NativeAgent(
        gateway=ModelGateway(
            registry=load_backend_registry(None, default_model=model_name),
        ),
        runtime_config=config,
        artifact_store=ArtifactStore(tmp_path / "runs"),
    ).solve(load_task_spec(task_path))

    assert outcome.status == "RUN_COMPLETED", outcome.summary
    assert ArtifactStore(tmp_path / "runs").verify(outcome.run_dir) == []
    assert not (outcome.run_dir / "attempt-01/case/Allrun").exists()
    trace = (
        outcome.run_dir / "attempt-01/generation-trace.json"
    ).read_text(encoding="utf-8")
    assert "deterministic_renderer" not in trace
    configuration = json.loads(
        (outcome.run_dir / "model-configuration.json").read_text(
            encoding="utf-8"
        )
    )
    assert configuration["case_bundle_calls"] == 1
    assert configuration["backend_id"] == "codex-cli"
    assert not (outcome.run_dir / "plan-review.json").exists()
