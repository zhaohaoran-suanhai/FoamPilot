from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from foampilot.artifacts import ArtifactStore
from foampilot.artifacts.models import AttemptSummary, RunSummary
from foampilot.improvement.analysis import (
    create_learning_candidate,
    directory_sha256,
    infer_root_cause,
)
from foampilot.improvement.models import RootCause
from foampilot.qualification.models import (
    QualificationReport,
    QualificationResult,
)
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ResumeMetadata,
    WorkflowState,
)


def _finalized_run(
    tmp_path: Path,
) -> tuple[Path, QualificationReport]:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    summary = RunSummary(
        task_id="multiphase-dam-break",
        workflow_state=WorkflowState.FAILED,
        native_status="SOLVER_FAILED",
        attempts=[
            AttemptSummary(
                attempt=1,
                status="SOLVER_FAILED",
                failed_step_id="solve",
                failure_fingerprint="FOAM FATAL IO ERROR",
            )
        ],
        primary_failure=FailureRecord(
            domain=FailureDomain.SOLVER,
            code="SOLVER_FAILED",
            step_id="solve",
            detail="Solver rejected the generated numerical dictionary.",
        ),
        resume=ResumeMetadata(
            allowed=False,
            reason="failure is not resumable",
        ),
        message="Solver rejected the generated numerical dictionary.",
    )
    (run_dir / "summary.json").write_text(
        summary.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store.finalize(run_dir)
    result = QualificationResult(
        case_id="multiphase-dam-break",
        status="FAIL_AGENT",
        native_status="SOLVER_FAILED",
        run_dir=run_dir,
        attempts=1,
        model_calls=2,
        duration_seconds=2.0,
        message="Synthetic frozen qualification result.",
    )
    report = QualificationReport(
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        model_name="gpt-test",
        counts={
            "PASS": 0,
            "FAIL_AGENT": 1,
            "BLOCKED_ENVIRONMENT": 0,
            "INVALID_QUALIFICATION": 0,
        },
        results=[result],
    )
    return run_dir, report


def test_tampered_run_cannot_create_candidate(tmp_path: Path) -> None:
    run_dir, report = _finalized_run(tmp_path)
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact manifest"):
        create_learning_candidate(
            run_dir=run_dir,
            qualification_report=report,
            candidate_id="candidate-1",
            generalized_lesson="Use the correct transient time-step cap.",
            proposed_target="knowledge",
        )


def test_official_example_requires_matching_final_report(
    tmp_path: Path,
) -> None:
    run_dir, report = _finalized_run(tmp_path)
    tutorial = tmp_path / "tutorial"
    tutorial.mkdir()
    (tutorial / "controlDict").write_text("application interFoam;\n")
    unrelated = report.model_copy(
        update={
            "results": [
                report.results[0].model_copy(
                    update={"run_dir": tmp_path / "other-run"}
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="qualification report"):
        create_learning_candidate(
            run_dir=run_dir,
            qualification_report=unrelated,
            official_example=tutorial,
            extracted_principles=["A general solver-family rule."],
            leakage_families=["candidate-family"],
            candidate_id="candidate-1",
            generalized_lesson="Use the solver-family rule.",
            proposed_target="knowledge",
        )


def test_directory_hash_is_stable_across_creation_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "system").mkdir(parents=True)
    (first / "constant").mkdir()
    (first / "system" / "controlDict").write_text("application icoFoam;\n")
    (first / "constant" / "transportProperties").write_text("nu 1e-5;\n")
    (second / "constant").mkdir(parents=True)
    (second / "system").mkdir()
    (second / "constant" / "transportProperties").write_text("nu 1e-5;\n")
    (second / "system" / "controlDict").write_text("application icoFoam;\n")

    assert directory_sha256(first) == directory_sha256(second)


def test_directory_hash_rejects_symlinks(tmp_path: Path) -> None:
    tutorial = tmp_path / "tutorial"
    tutorial.mkdir()
    target = tmp_path / "outside"
    target.write_text("hidden", encoding="utf-8")
    (tutorial / "linked").symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        directory_sha256(tutorial)


def test_directory_hash_rejects_symlinked_directories(
    tmp_path: Path,
) -> None:
    tutorial = tmp_path / "tutorial"
    tutorial.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden").write_text("hidden", encoding="utf-8")
    (tutorial / "linked-directory").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="symlink"):
        directory_sha256(tutorial)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("BLOCKED_ENVIRONMENT", RootCause.ENVIRONMENT),
        ("PLAN_INVALID", RootCause.CASE_GENERATION),
        ("STATIC_INSPECTION_FAILED", RootCause.VERSION_CONTRACT),
        ("MESH_FAILED", RootCause.MESH),
        ("INITIALIZATION_FAILED", RootCause.INITIALIZATION),
        ("SOLVER_FAILED", RootCause.NUMERICS),
        ("PUBLIC_VALIDATION_FAILED", RootCause.VALIDATION),
    ],
)
def test_infer_root_cause_is_conservative(
    status: str,
    expected: RootCause,
) -> None:
    assert infer_root_cause(status) == expected


def test_success_status_does_not_create_improvement_cause() -> None:
    with pytest.raises(ValueError, match="does not support"):
        infer_root_cause("PUBLIC_VALIDATION_PASS")


def test_candidate_collects_frozen_public_evidence_without_writing_run(
    tmp_path: Path,
) -> None:
    run_dir, report = _finalized_run(tmp_path)
    tutorial = tmp_path / "tutorial"
    (tutorial / "system").mkdir(parents=True)
    (tutorial / "system" / "controlDict").write_text(
        "application interFoam;\n",
        encoding="utf-8",
    )
    before = sorted(path.relative_to(run_dir) for path in run_dir.rglob("*"))

    candidate = create_learning_candidate(
        run_dir=run_dir,
        qualification_report=report,
        official_example=tutorial,
        extracted_principles=[
            "Transient VOF solvers require bounded time-step control."
        ],
        leakage_families=["multiphase/interFoam"],
        candidate_id="candidate-1",
        generalized_lesson="Apply solver-family time-step bounds.",
        proposed_target="knowledge",
        development_cases=["multiphase-dam-break"],
        promotion_criteria=["source_improves"],
    )

    after = sorted(path.relative_to(run_dir) for path in run_dir.rglob("*"))
    assert before == after
    assert candidate.root_cause == RootCause.NUMERICS
    assert candidate.public_evidence.failure_fingerprints == [
        "FOAM FATAL IO ERROR"
    ]
    assert candidate.public_evidence.failed_steps == ["solve"]
    assert candidate.official_example.used
    assert candidate.official_example.source_sha256 == directory_sha256(
        tutorial
    )
