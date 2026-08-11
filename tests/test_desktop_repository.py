from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from foampilot.artifacts import ArtifactStore
from foampilot.desktop.repository import (
    RunCollectionError,
    RunOpenError,
    RunRepository,
)
from foampilot.workflow import WorkflowEvent, WorkflowStage


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def _summary() -> dict[str, object]:
    return {
        "schema_version": 2,
        "task_id": "desktop-fixture",
        "workflow_state": "COMPLETED",
        "native_status": "PUBLIC_VALIDATION_PASS",
        "last_completed_stage": "RUN_FINALIZED",
        "attempts": [
            {
                "attempt": 1,
                "status": "PUBLIC_VALIDATION_PASS",
                "failed_step_id": None,
                "failure_fingerprint": None,
                "changed_files": [],
            }
        ],
        "primary_failure": None,
        "terminal_blocker": None,
        "resume": {
            "allowed": False,
            "from_stage": None,
            "reason": "completed run",
        },
        "parent_run": None,
        "message": "All public checks pass.",
    }


def _event(
    sequence: int,
    stage: WorkflowStage,
    *,
    step_id: str | None = None,
) -> WorkflowEvent:
    return WorkflowEvent.completed(
        stage=stage,
        sequence=sequence,
        occurred_at=NOW,
        attempt=1,
        step_id=step_id,
    )


def _finalized_run(tmp_path: Path) -> Path:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    (run_dir / "summary.json").write_text(
        json.dumps(_summary()),
        encoding="utf-8",
    )
    (run_dir / "workflow-events.jsonl").write_text(
        "\n".join(
            (
                _event(1, WorkflowStage.TASK_VALIDATED).model_dump_json(),
                _event(
                    2,
                    WorkflowStage.OPENFOAM_STEP_COMPLETE,
                    step_id="solve-pimplefoam",
                ).model_dump_json(),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    log = (
        run_dir
        / "attempt-01/case/.foampilot/logs/solve.stdout.log"
    )
    log.parent.mkdir(parents=True)
    log.write_text("Time = 0.03\nEnd\n", encoding="utf-8")
    field = run_dir / "attempt-01/case/0.03/U"
    field.parent.mkdir(parents=True)
    field.write_text("internalField uniform (1 0 0);\n", encoding="utf-8")
    store.finalize(run_dir)
    return run_dir


def test_open_finalized_run_builds_verified_snapshot(tmp_path: Path) -> None:
    run_dir = _finalized_run(tmp_path)

    snapshot = RunRepository().open(run_dir)

    assert snapshot.summary is not None
    assert snapshot.summary.status == "PUBLIC_VALIDATION_PASS"
    assert snapshot.manifest_state == "verified"
    assert snapshot.manifest_issues == ()
    assert [item.sequence for item in snapshot.timeline] == [1, 2]
    assert snapshot.timeline[1].step_id == "solve-pimplefoam"
    assert "attempt-01/case/.foampilot/logs/solve.stdout.log" in {
        item.path for item in snapshot.files
    }
    assert {item.category for item in snapshot.files} >= {
        "case",
        "log",
        "workflow",
    }


def test_open_active_run_tolerates_malformed_event(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-active"
    run_dir.mkdir()
    (run_dir / "workflow-events.jsonl").write_text(
        _event(1, WorkflowStage.TASK_VALIDATED).model_dump_json()
        + "\n{not-json}\n",
        encoding="utf-8",
    )

    snapshot = RunRepository().open(run_dir)

    assert snapshot.summary is None
    assert snapshot.manifest_state == "pending"
    assert [item.sequence for item in snapshot.timeline] == [1]
    assert snapshot.warnings == (
        "workflow-events.jsonl line 2 is invalid",
    )


def test_open_rejects_batch_root_with_sorted_child_runs(
    tmp_path: Path,
) -> None:
    batch_root = tmp_path / "batch"
    run_b = batch_root / "run-20260811-b"
    run_a = batch_root / "run-20260811-a"
    run_b.mkdir(parents=True)
    run_a.mkdir()

    with pytest.raises(RunCollectionError) as captured:
        RunRepository().open(batch_root)

    assert captured.value.children == (
        run_a.resolve(),
        run_b.resolve(),
    )
    assert "concrete child run" in str(captured.value)


def test_open_projects_public_knowledge_and_skill_references(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-context"
    run_dir.mkdir()
    source_hash = "96538945ea4170a24ef356863904a745fa68b586e489b33ac0b6cbd8f8954641"
    (run_dir / "agent-context.json").write_text(
        json.dumps(
            {
                "knowledge_slots": {
                    "solver_family_contract": (
                        "of10.solver.icofoam-contract"
                    ),
                    "mesh_pattern": None,
                },
                "missing_slots": ["mesh_pattern"],
                "selected_knowledge_ids": [
                    "of10.solver.icofoam-contract"
                ],
                "selected_source_hashes": {
                    "of10.solver.icofoam-contract": source_hash
                },
                "skill_names": ["openfoam-author-native-case"],
            }
        ),
        encoding="utf-8",
    )

    snapshot = RunRepository().open(run_dir)

    assert len(snapshot.context_references) == 1
    reference = snapshot.context_references[0]
    assert reference.stage == "author"
    assert reference.attempt is None
    assert reference.slot == "solver_family_contract"
    assert reference.entry_id == "of10.solver.icofoam-contract"
    assert reference.title == "icoFoam 瞬态不可压缩层流契约"
    assert reference.knowledge_type == "solver_guide"
    assert reference.source_locator == (
        "OpenFOAM-10/applications/solvers/incompressible/icoFoam/icoFoam.C"
    )
    assert reference.source_sha256 == source_hash
    assert [item.name for item in snapshot.skill_references] == [
        "openfoam-author-native-case"
    ]


def test_open_keeps_unknown_historical_context_id_visible(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-context"
    run_dir.mkdir()
    (run_dir / "agent-context.json").write_text(
        json.dumps(
            {
                "knowledge_slots": {"solver_family_contract": "of10.old.id"},
                "missing_slots": [],
                "selected_knowledge_ids": ["of10.old.id"],
                "selected_source_hashes": {"of10.old.id": "a" * 64},
                "skill_names": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = RunRepository().open(run_dir)

    assert snapshot.context_references[0].entry_id == "of10.old.id"
    assert snapshot.context_references[0].title is None
    assert snapshot.context_references[0].source_sha256 == "a" * 64


def test_open_projects_residual_samples_from_attempt_logs(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-active"
    log = run_dir / "attempt-02/case/.foampilot/logs/solve.stdout.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "Time = 0.5\n"
        "smoothSolver: Solving for Ux, Initial residual = 0.2, "
        "Final residual = 0.01, No Iterations 2\n",
        encoding="utf-8",
    )

    snapshot = RunRepository().open(run_dir)

    assert len(snapshot.residual_samples) == 1
    sample = snapshot.residual_samples[0]
    assert sample.attempt == 2
    assert sample.field == "Ux"
    assert sample.simulation_time == 0.5
    assert sample.source_log == (
        "attempt-02/case/.foampilot/logs/solve.stdout.log"
    )


def test_manifest_mismatch_is_visible_without_hiding_files(
    tmp_path: Path,
) -> None:
    run_dir = _finalized_run(tmp_path)
    log = run_dir / "attempt-01/case/.foampilot/logs/solve.stdout.log"
    log.write_text("mutated\n", encoding="utf-8")

    snapshot = RunRepository().open(run_dir)

    assert snapshot.manifest_state == "invalid"
    assert snapshot.manifest_issues == (
        "hash mismatch: attempt-01/case/.foampilot/logs/solve.stdout.log",
    )
    assert RunRepository().read_text(
        snapshot,
        "attempt-01/case/.foampilot/logs/solve.stdout.log",
    ) == "mutated\n"


def test_read_text_is_confined_to_opened_run(tmp_path: Path) -> None:
    run_dir = _finalized_run(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    linked = run_dir / "attempt-01/case/linked-secret"
    linked.symlink_to(outside)
    snapshot = RunRepository().open(run_dir)
    repository = RunRepository()

    with pytest.raises(RunOpenError, match="outside opened run"):
        repository.read_text(snapshot, "../secret.txt")
    with pytest.raises(RunOpenError, match="symbolic links"):
        repository.read_text(snapshot, "attempt-01/case/linked-secret")
    with pytest.raises(RunOpenError, match="not registered"):
        repository.read_text(snapshot, "missing.log")


def test_read_text_enforces_size_limit(tmp_path: Path) -> None:
    run_dir = _finalized_run(tmp_path)
    snapshot = RunRepository().open(run_dir)

    with pytest.raises(RunOpenError, match="exceeds display limit"):
        RunRepository().read_text(
            snapshot,
            "attempt-01/case/.foampilot/logs/solve.stdout.log",
            max_bytes=4,
        )


def test_open_rejects_symlink_run_directory(tmp_path: Path) -> None:
    run_dir = _finalized_run(tmp_path)
    link = tmp_path / "run-link"
    link.symlink_to(run_dir, target_is_directory=True)

    with pytest.raises(RunOpenError, match="symbolic link"):
        RunRepository().open(link)


@pytest.mark.parametrize(
    "control_name",
    ("summary.json", "artifact-manifest.json"),
)
def test_open_rejects_symlink_control_artifact(
    tmp_path: Path,
    control_name: str,
) -> None:
    run_dir = _finalized_run(tmp_path)
    control = run_dir / control_name
    outside = tmp_path / f"outside-{control_name}"
    control.replace(outside)
    control.symlink_to(outside)

    with pytest.raises(RunOpenError, match="symbolic link"):
        RunRepository().open(run_dir)
