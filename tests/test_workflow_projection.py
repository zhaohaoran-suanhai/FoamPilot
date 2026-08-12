from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from foampilot.artifacts import ArtifactStore, RunSummary
from foampilot.evidence import MetricPoint
from foampilot.workflow import (
    ResumeMetadata,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowState,
    build_workflow_projection,
)


_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run-projection"
    run.mkdir()
    summary = RunSummary(
        task_id="projection-task",
        workflow_state=WorkflowState.DEFERRED,
        last_completed_stage=WorkflowStage.DESIGNING_CASE.value,
        primary_failure={
            "domain": "design",
            "code": "CONFIRMATION_REQUIRED",
            "detail": "confirm viscosity",
            "evidence_paths": ["questions.json"],
        },
        resume=ResumeMetadata(allowed=False, reason="awaiting confirmation"),
        message="confirmation required",
    )
    (run / "summary.json").write_text(
        json.dumps(summary.model_dump(mode="json")), encoding="utf-8"
    )
    events = [
        WorkflowEvent(
            sequence=1,
            stage=WorkflowStage.DESIGNING_CASE,
            state=WorkflowEventState.COMPLETED,
            occurred_at=_NOW,
        ),
        WorkflowEvent(
            sequence=2,
            stage=WorkflowStage.WAITING_FOR_CONFIRMATION,
            state=WorkflowEventState.DEFERRED,
            occurred_at=_NOW,
            detail="CONFIRMATION_REQUIRED",
            evidence_paths=["questions.json"],
        ),
    ]
    (run / "workflow-events.jsonl").write_text(
        "".join(item.model_dump_json() + "\n" for item in events),
        encoding="utf-8",
    )
    points = (
        MetricPoint(
            sequence=1,
            occurred_at=_NOW,
            attempt=1,
            step_id="solve",
            simulation_time=0.5,
            series="residual:p",
            value=0.1,
        ),
        MetricPoint(
            sequence=2,
            occurred_at=_NOW,
            attempt=1,
            step_id="solve",
            simulation_time=0.5,
            series="residual-final:p",
            value=0.01,
        ),
    )
    (run / "metrics.jsonl").write_text(
        "".join(item.model_dump_json() + "\n" for item in points),
        encoding="utf-8",
    )
    (run / "questions.json").write_text(
        json.dumps(
            {
                "state": "CONFIRMATION_REQUIRED",
                "questions": [
                    {
                        "question_id": "confirm-nu",
                        "field_path": "materials.fluid.nu",
                        "prompt_zh": "请确认运动黏度。",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run / "attempt-01").mkdir()
    (run / "attempt-01/run-facts.json").write_text("{}\n", encoding="utf-8")
    ArtifactStore(run.parent).finalize(run)
    return run


def test_projection_combines_workflow_metrics_questions_and_artifacts(
    tmp_path: Path,
) -> None:
    projection = build_workflow_projection(_run(tmp_path))

    assert projection.current_stage == WorkflowStage.WAITING_FOR_CONFIRMATION
    assert projection.stage_progress.completed == 1
    assert projection.stage_progress.total >= 1
    assert projection.active_operation == "awaiting_confirmation"
    assert projection.latest_solver_time == 0.5
    assert projection.recent_residuals[0].field == "p"
    assert projection.recent_residuals[0].initial == 0.1
    assert projection.recent_residuals[0].final == 0.01
    assert projection.pending_questions[0].question_id == "confirm-nu"
    assert projection.failure_summary.code == "CONFIRMATION_REQUIRED"
    assert "questions.json" in projection.artifact_links
    assert "attempt-01/run-facts.json" in projection.artifact_links


def test_corrupt_metrics_warn_without_changing_workflow_state(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    (run / "artifact-manifest.json").unlink()
    (run / "metrics.jsonl").write_text("{bad json}\n", encoding="utf-8")

    projection = build_workflow_projection(run)

    assert projection.current_stage == WorkflowStage.WAITING_FOR_CONFIRMATION
    assert projection.recent_residuals == ()
    assert "METRICS_LINE_INVALID:1" in projection.warnings


def test_legacy_run_without_metrics_is_read_only_with_warning(
    tmp_path: Path,
) -> None:
    run = tmp_path / "legacy-run"
    run.mkdir()
    (run / "workflow-events.jsonl").write_text(
        WorkflowEvent(
            sequence=1,
            stage=WorkflowStage.RUN_FINALIZED,
            state=WorkflowEventState.COMPLETED,
            occurred_at=_NOW,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    projection = build_workflow_projection(run)

    assert projection.current_stage == WorkflowStage.RUN_FINALIZED
    assert projection.recent_residuals == ()
    assert "LEGACY_METRICS_UNAVAILABLE" in projection.warnings


def test_projection_ignores_unmanifested_terminal_failure_report(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    (run / "failure-report.json").write_text(
        json.dumps({"failure_code": "FAKE"}), encoding="utf-8"
    )

    projection = build_workflow_projection(run)

    assert projection.failure_summary.code == "CONFIRMATION_REQUIRED"
    assert "failure-report.json" not in projection.artifact_links
