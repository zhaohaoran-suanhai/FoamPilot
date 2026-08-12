from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

import pytest

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.jobs import (
    JobOperation,
    JobState,
    LocalJobStore,
    build_job_spec,
    launch_local_job,
)
from foampilot.plans import ExecutionPlan
from foampilot.tasks import load_task_spec
from tests.support.runtime import real_runtime_config
from tests.test_native_case_generation import RecordingModel


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TASK = PACKAGE_ROOT / "examples/tasks/non-tutorial-side-driven-box.yaml"
FROZEN_PLAN = (
    PACKAGE_ROOT / "tests/fixtures/gates/non-tutorial-side-driven-plan.json"
)


@pytest.mark.skipif(
    not os.environ.get("FOAMPILOT_OPENFOAM_ROOT"),
    reason="FOAMPILOT_OPENFOAM_ROOT is required for the real detached-job gate",
)
def test_real_detached_job_reuses_verified_plan_and_finalizes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    task_path = project / "task.yaml"
    shutil.copy2(TASK, task_path)
    runtime = real_runtime_config().model_copy(
        update={"isolation": "trusted_host"}
    )
    plan = ExecutionPlan.model_validate_json(
        FROZEN_PLAN.read_text(encoding="utf-8")
    )
    source_store = ArtifactStore(project / "source-runs")
    source = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=runtime,
        artifact_store=source_store,
    ).solve(load_task_spec(task_path))
    assert source.status == "RUN_COMPLETED", source.summary

    job_root = project / "runs/job-real-detached"
    job_root.mkdir(parents=True)
    arguments = (
        "solve",
        str(task_path),
        "--run-root",
        str(job_root),
        "--public-asset-root",
        str(project),
        "--reuse-verified-plan",
        str(source.run_dir),
        "--openfoam-root",
        str(runtime.openfoam_root),
        "--execution-isolation",
        "trusted_host",
        "--progress",
        "jsonl",
        "--json",
    )
    store = LocalJobStore(job_root)
    store.create(
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation=JobOperation.SOLVE,
            arguments=arguments,
        )
    )
    store.initialize_status()

    worker_pid = launch_local_job(job_root)
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        status = store.read_status()
        if status.state in {
            JobState.CANCELLED,
            JobState.COMPLETED,
            JobState.FAILED,
        }:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"detached worker {worker_pid} did not finish")

    assert status.state == JobState.COMPLETED
    assert status.terminal_code == "CLI_EXIT_0"
    assert status.run_dir is not None
    run_dir = job_root / status.run_dir
    summary = ArtifactStore.read_summary(run_dir)
    assert summary.native_status == "RUN_COMPLETED"
    assert ArtifactStore(job_root).verify(run_dir) == []
    events = [
        json.loads(line)
        for line in (job_root / "job-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert any(event["kind"] == "command" for event in events)


@pytest.mark.skipif(
    not os.environ.get("FOAMPILOT_OPENFOAM_ROOT"),
    reason="FOAMPILOT_OPENFOAM_ROOT is required for the real rerun gate",
)
def test_real_rerun_executes_cold_solver_and_preserves_parent(
    tmp_path: Path,
) -> None:
    runtime = real_runtime_config().model_copy(
        update={"isolation": "trusted_host"}
    )
    task = load_task_spec(TASK)
    plan = ExecutionPlan.model_validate_json(
        FROZEN_PLAN.read_text(encoding="utf-8")
    )
    parent_store = ArtifactStore(tmp_path / "parent-job")
    parent = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=runtime,
        artifact_store=parent_store,
    ).solve(task)
    assert parent.status == "RUN_COMPLETED", parent.summary
    parent_manifest = (
        parent.run_dir / ArtifactStore.manifest_name
    ).read_bytes()

    child_store = ArtifactStore(tmp_path / "rerun-job")
    child = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=runtime,
        artifact_store=child_store,
    ).rerun(parent.run_dir)

    assert child.status == "RUN_COMPLETED", child.summary
    assert parent_store.verify(parent.run_dir) == []
    assert child_store.verify(child.run_dir) == []
    assert (
        parent.run_dir / ArtifactStore.manifest_name
    ).read_bytes() == parent_manifest
    lineage = json.loads(
        (child.run_dir / "lineage.json").read_text(encoding="utf-8")
    )
    assert lineage["relation"] == "rerun_same_input"
    assert lineage["parent_run_id"] == parent.run_dir.name
    assert lineage["reused_evidence_paths"] == []
