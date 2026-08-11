from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from foampilot.artifacts import ArtifactStore, RunSummary
from foampilot.jobs import (
    JobOperation,
    JobState,
    LocalJobStore,
    RecoveryAction,
    RecoveryState,
    build_job_spec,
    current_process_identity,
    process_identity,
    recover_finalize,
    reconcile_job,
    terminate_orphan,
)
from foampilot.workflow import ResumeMetadata, WorkflowState
from foampilot.workflow import WorkflowEvent, WorkflowStage


NOW = datetime.now(timezone.utc)


def _store(tmp_path: Path, *, operation: JobOperation = JobOperation.SOLVE):
    project = tmp_path / "project"
    job_root = project / "runs/job-recovery"
    job_root.mkdir(parents=True)
    task = project / "task.yaml"
    task.write_text("schema_version: 2\ntask_id: recovery\n", encoding="utf-8")
    prefix = (operation.value,)
    arguments = (*prefix, str(task), "--run-root", str(job_root))
    store = LocalJobStore(job_root)
    store.create(
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation=operation,
            arguments=arguments,
        )
    )
    store.initialize_status()
    return store


def _finalize_run(store: LocalJobStore) -> Path:
    run_dir = store.root / "run-final"
    run_dir.mkdir()
    summary = RunSummary(
        task_id="recovery",
        workflow_state=WorkflowState.COMPLETED,
        native_status="PUBLIC_VALIDATION_PASS",
        resume=ResumeMetadata(allowed=False, reason="completed"),
        message="completed",
    )
    (run_dir / "summary.json").write_text(
        summary.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    ArtifactStore(store.root).finalize(run_dir)
    store.update_status(
        state=JobState.COMPLETED,
        run_dir=run_dir.name,
        finished_at=NOW,
        terminal_code="CLI_EXIT_0",
    )
    return run_dir


def test_reconcile_running_requires_identity_lock_and_fresh_heartbeat(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.update_status(
        state=JobState.RUNNING,
        worker=current_process_identity(),
        last_heartbeat_at=NOW,
    )

    with store.writer_lock():
        decision = reconcile_job(
            store.root,
            heartbeat_stale_seconds=5,
            now=lambda: NOW,
        )

    assert decision.state == RecoveryState.RUNNING
    assert decision.writer_lock_held is True
    assert decision.worker_alive is True
    assert decision.allowed_actions == (
        RecoveryAction.ATTACH,
        RecoveryAction.CANCEL,
    )


def test_reconcile_stale_heartbeat_is_unresponsive_not_failed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.update_status(
        state=JobState.RUNNING,
        worker=current_process_identity(),
        last_heartbeat_at=NOW - timedelta(seconds=30),
    )

    with store.writer_lock():
        decision = reconcile_job(
            store.root,
            heartbeat_stale_seconds=5,
            now=lambda: NOW,
        )

    assert decision.state == RecoveryState.UNRESPONSIVE
    assert decision.code == "JOB_HEARTBEAT_STALE"
    assert RecoveryAction.CANCEL in decision.allowed_actions
    assert RecoveryAction.RECOVER_FINALIZE not in decision.allowed_actions


def test_reconcile_dead_worker_and_live_child_is_orphaned_active(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    try:
        store.update_status(
            state=JobState.RUNNING,
            worker=current_process_identity().model_copy(
                update={"start_token": 0}
            ),
            current_child=process_identity(child.pid),
            last_heartbeat_at=NOW - timedelta(seconds=30),
        )

        decision = reconcile_job(store.root, now=lambda: NOW)

        assert decision.state == RecoveryState.ORPHANED_ACTIVE
        assert decision.worker_alive is False
        assert decision.child_alive is True
        assert decision.allowed_actions == (
            RecoveryAction.INSPECT,
            RecoveryAction.TERMINATE_ORPHAN,
        )
    finally:
        try:
            os.killpg(child.pid, 9)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)


def test_terminate_orphan_is_identity_checked_and_leaves_stopped_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ],
        start_new_session=True,
    )
    store.update_status(
        state=JobState.RUNNING,
        worker=current_process_identity().model_copy(update={"start_token": 0}),
        current_child=process_identity(child.pid),
        last_heartbeat_at=NOW - timedelta(seconds=30),
    )

    result = terminate_orphan(store.root, grace_seconds=0.05)
    child.wait(timeout=5)

    assert result.state == RecoveryState.ORPHANED_STOPPED
    assert result.child_alive is False
    assert RecoveryAction.RECOVER_FINALIZE in result.allowed_actions


def test_reconcile_dead_worker_and_child_is_orphaned_stopped(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    dead = current_process_identity().model_copy(update={"start_token": 0})
    store.update_status(
        state=JobState.RUNNING,
        worker=dead,
        current_child=dead,
        last_heartbeat_at=NOW - timedelta(seconds=30),
    )

    decision = reconcile_job(store.root, now=lambda: NOW)

    assert decision.state == RecoveryState.ORPHANED_STOPPED
    assert decision.allowed_actions == (
        RecoveryAction.RECOVER_FINALIZE,
        RecoveryAction.RERUN,
    )


def test_reconcile_valid_terminal_artifacts_is_finalized(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run_dir = _finalize_run(store)

    decision = reconcile_job(store.root, now=lambda: NOW)

    assert decision.state == RecoveryState.FINALIZED
    assert decision.run_dir == run_dir
    assert decision.manifest_issues == ()
    assert decision.allowed_actions == (
        RecoveryAction.REPORT,
        RecoveryAction.RERUN,
    )


def test_reconcile_invalid_terminal_artifacts_is_evidence_damaged(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run_dir = _finalize_run(store)
    (run_dir / "summary.json").write_text("{}\n", encoding="utf-8")

    decision = reconcile_job(store.root, now=lambda: NOW)

    assert decision.state == RecoveryState.EVIDENCE_DAMAGED
    assert decision.manifest_issues
    assert RecoveryAction.STRICT_RESUME not in decision.allowed_actions
    assert decision.allowed_actions == (
        RecoveryAction.INSPECT,
        RecoveryAction.RERUN,
    )


def test_reconcile_rejects_symbolic_run_binding(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = store.root / "run-real"
    target.mkdir()
    alias = store.root / "run-alias"
    alias.symlink_to(target, target_is_directory=True)
    store.update_status(state=JobState.RUNNING, run_dir=alias.name)

    decision = reconcile_job(store.root, now=lambda: NOW)

    assert decision.state == RecoveryState.EVIDENCE_DAMAGED
    assert "symbolic" in decision.manifest_issues[0]


def _partial_run(store: LocalJobStore) -> Path:
    run_dir = store.root / "run-partial"
    run_dir.mkdir()
    (run_dir / "task.yaml").write_text(
        "schema_version: 2\ntask_id: interrupted-case\n",
        encoding="utf-8",
    )
    event = WorkflowEvent.completed(
        stage=WorkflowStage.TASK_VALIDATED,
        sequence=1,
        occurred_at=NOW,
    )
    (run_dir / "workflow-events.jsonl").write_text(
        event.model_dump_json() + "\n",
        encoding="utf-8",
    )
    (run_dir / "partial.log").write_text("partial output\n", encoding="utf-8")
    dead = current_process_identity().model_copy(update={"start_token": 0})
    store.update_status(
        state=JobState.RUNNING,
        worker=dead,
        current_child=dead,
        current_stage="solve",
        current_step_id="solve-icofoam",
        run_dir=run_dir.name,
        last_heartbeat_at=NOW - timedelta(seconds=30),
    )
    return run_dir


def test_recover_finalize_writes_neutral_interruption_and_valid_manifest(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run_dir = _partial_run(store)

    decision = recover_finalize(store.root, recorded_at=lambda: NOW)

    assert decision.state == RecoveryState.FINALIZED
    summary = ArtifactStore.read_summary(run_dir)
    assert summary.workflow_state == WorkflowState.INTERRUPTED
    assert summary.native_status is None
    assert summary.primary_failure is None
    assert summary.terminal_blocker is not None
    assert summary.terminal_blocker.domain == "workflow"
    assert summary.terminal_blocker.code == "WORKER_INTERRUPTED"
    assert summary.resume.allowed is False
    assert summary.last_completed_stage == "TASK_VALIDATED"
    interruption = json.loads(
        (run_dir / "interruption.json").read_text(encoding="utf-8")
    )
    assert interruption["last_stage"] == "solve"
    assert interruption["last_step_id"] == "solve-icofoam"
    events = [
        WorkflowEvent.model_validate_json(line)
        for line in (run_dir / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1].stage == WorkflowStage.RUN_FINALIZED
    assert events[-1].state == "interrupted"
    assert ArtifactStore(store.root).verify(run_dir) == []
    assert store.read_status().state == JobState.INTERRUPTED


def test_recover_finalize_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run_dir = _partial_run(store)
    first = recover_finalize(store.root, recorded_at=lambda: NOW)
    manifest = run_dir / ArtifactStore.manifest_name
    original = manifest.read_bytes()
    revision = store.read_status().revision

    second = recover_finalize(store.root, recorded_at=lambda: NOW)

    assert first == second
    assert manifest.read_bytes() == original
    assert store.read_status().revision == revision


def test_recover_finalize_refuses_held_writer_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _partial_run(store)

    with store.writer_lock():
        try:
            recover_finalize(store.root, recorded_at=lambda: NOW)
        except ValueError as error:
            assert "JOB_RECOVERY_NOT_ALLOWED" in str(error)
        else:
            raise AssertionError("recover-finalize accepted a held writer lock")
