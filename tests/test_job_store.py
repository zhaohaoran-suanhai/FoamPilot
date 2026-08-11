from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from foampilot.jobs import (
    JobState,
    LocalJobStore,
    ProcessIdentity,
    build_job_spec,
    current_process_identity,
    process_identity_matches,
)


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runs = project / "runs"
    job_root = runs / "job-test"
    job_root.mkdir(parents=True)
    task = project / "tasks/task.yaml"
    task.parent.mkdir()
    task.write_text("task_id: test\n", encoding="utf-8")
    return project, job_root, task


def test_job_store_creates_strict_receipt_and_monotonic_status(tmp_path: Path) -> None:
    project, job_root, task = _workspace(tmp_path)
    spec = build_job_spec(
        job_root=job_root,
        project_root=project,
        operation="solve",
        arguments=("solve", str(task), "--run-root", str(job_root), "--json"),
    )
    store = LocalJobStore(job_root)

    store.create(spec)
    first = store.initialize_status()
    second = store.update_status(
        state=JobState.STARTING,
        started_at=datetime.now(timezone.utc),
    )

    assert store.read_spec() == spec
    assert spec.input_paths == ("tasks/task.yaml",)
    assert len(spec.input_sha256["tasks/task.yaml"]) == 64
    assert first.revision == 1
    assert second.revision == 2
    assert store.read_status() == second


def test_job_store_rejects_symlink_root_and_project_escape(tmp_path: Path) -> None:
    project, job_root, task = _workspace(tmp_path)
    link = project / "runs/job-link"
    link.symlink_to(job_root, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        LocalJobStore(link)

    outside = tmp_path / "outside.yaml"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError, match="outside project"):
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation="solve",
            arguments=("solve", str(outside), "--run-root", str(job_root)),
        )


def test_job_receipt_rejects_secret_shaped_arguments(tmp_path: Path) -> None:
    project, job_root, task = _workspace(tmp_path)

    with pytest.raises(ValueError, match="secret"):
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation="solve",
            arguments=("solve", str(task), "--api-key", "sk-secretvalue"),
        )


def test_cancel_request_is_atomic_and_idempotent(tmp_path: Path) -> None:
    project, job_root, task = _workspace(tmp_path)
    store = LocalJobStore(job_root)
    store.create(
        build_job_spec(
            job_root=job_root,
            project_root=project,
            operation="solve",
            arguments=("solve", str(task), "--run-root", str(job_root)),
        )
    )

    first = store.request_cancel(requested_by="desktop")
    second = store.request_cancel(requested_by="desktop-again")

    assert second == first
    assert second.requested_by == "desktop"
    assert store.cancel_requested is True


def test_writer_lock_is_exclusive(tmp_path: Path) -> None:
    _, job_root, _ = _workspace(tmp_path)
    first = LocalJobStore(job_root)
    second = LocalJobStore(job_root)

    with first.writer_lock():
        with pytest.raises(RuntimeError, match="JOB_WRITER_LOCKED"):
            with second.writer_lock():
                pass


def test_linux_process_identity_detects_mismatch() -> None:
    identity = current_process_identity()

    assert process_identity_matches(identity)
    assert not process_identity_matches(
        ProcessIdentity(
            pid=identity.pid,
            pgid=identity.pgid,
            start_token=identity.start_token + 1,
            boot_id=identity.boot_id,
        )
    )
