import json
from pathlib import Path

import pytest

from foampilot.artifacts import (
    ArtifactStore,
    AttemptSummary,
    NativeAgentOutcome,
    RunSummary,
)
from foampilot.qualification.models import (
    QualificationMetric,
    compare_metric,
)
from foampilot.qualification.reporting import (
    build_qualification_report,
    classify_qualification,
    markdown_report,
    native_case_dir,
    run_metadata,
)
from foampilot.performance import PerformanceReuse, PerformanceSummary
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ParentRun,
    ResumeMetadata,
    WorkflowState,
)


def _outcome(
    tmp_path: Path,
    *,
    status: str,
    attempts: list[AttemptSummary] | None = None,
) -> NativeAgentOutcome:
    native_status = (
        status
        if status
        in {
            "STATIC_INSPECTION_FAILED",
            "MESH_FAILED",
            "INITIALIZATION_FAILED",
            "SOLVER_FAILED",
            "POSTPROCESS_FAILED",
            "PUBLIC_VALIDATION_FAILED",
            "PUBLIC_VALIDATION_PASS",
        }
        else None
    )
    completed = status == "PUBLIC_VALIDATION_PASS"
    domains = {
        "BLOCKED_ENVIRONMENT": FailureDomain.ENVIRONMENT,
        "PLAN_INVALID": FailureDomain.PLAN,
        "SOLVER_FAILED": FailureDomain.SOLVER,
    }
    summary = RunSummary(
        task_id="laminar-cavity",
        workflow_state=(
            WorkflowState.COMPLETED
            if completed
            else WorkflowState.FAILED
        ),
        native_status=native_status,
        attempts=attempts or [],
        primary_failure=(
            None
            if completed
            else FailureRecord(
                domain=domains.get(status, FailureDomain.LEGACY),
                code=status,
                detail="test",
            )
        ),
        resume=ResumeMetadata(
            allowed=False,
            reason="test outcome is not resumable",
        ),
        message="test",
    )
    return NativeAgentOutcome(
        run_dir=tmp_path / "run-1",
        summary=summary,
    )


def test_metric_comparison_supports_bounds_and_relative_vectors() -> None:
    assert compare_metric(
        observed=0.005,
        reference=0.001,
        tolerance=0.01,
        mode="absolute_upper_bound",
    ).passed
    relative = compare_metric(
        observed=[1.0, 2.1, 3.0],
        reference=[1.0, 2.0, 3.0],
        tolerance=0.05,
        mode="relative_l2",
    )
    assert relative.passed
    assert relative.error is not None
    assert 0 < relative.error < 0.05


def test_missing_observation_is_not_converted_to_zero() -> None:
    result = compare_metric(
        observed=None,
        reference=1.0,
        tolerance=0.1,
        mode="relative_absolute",
    )

    assert result.passed is None
    assert "missing" in result.detail


def test_native_case_dir_uses_the_final_attempt(tmp_path: Path) -> None:
    outcome = _outcome(
        tmp_path,
        status="PUBLIC_VALIDATION_PASS",
        attempts=[
            AttemptSummary(attempt=1, status="SOLVER_FAILED"),
            AttemptSummary(attempt=2, status="PUBLIC_VALIDATION_PASS"),
        ],
    )

    assert native_case_dir(outcome) == (
        outcome.run_dir / "attempt-02" / "case"
    )


def test_classification_preserves_failure_layers(tmp_path: Path) -> None:
    blocked = _outcome(tmp_path, status="BLOCKED_ENVIRONMENT")
    failed = _outcome(tmp_path, status="SOLVER_FAILED")
    passed = _outcome(
        tmp_path,
        status="PUBLIC_VALIDATION_PASS",
        attempts=[
            AttemptSummary(attempt=1, status="PUBLIC_VALIDATION_PASS")
        ],
    )

    assert classify_qualification(blocked, [], []) == "BLOCKED_ENVIRONMENT"
    assert classify_qualification(failed, [], []) == "FAIL_AGENT"
    assert classify_qualification(passed, ["hash mismatch"], []) == "FAIL_AGENT"
    assert (
        classify_qualification(
            passed,
            [],
            [
                QualificationMetric(
                    name="physics",
                    passed=False,
                    required=True,
                    detail="failed",
                )
            ],
        )
        == "FAIL_AGENT"
    )
    assert (
        classify_qualification(
            passed,
            [],
            [],
            evaluation_level="public_validation",
        )
        == "PASS"
    )


def test_classification_distinguishes_backend_deferred(
    tmp_path: Path,
) -> None:
    summary = RunSummary(
        task_id="laminar-cavity",
        workflow_state=WorkflowState.DEFERRED,
        native_status="SOLVER_FAILED",
        attempts=[
            AttemptSummary(attempt=1, status="SOLVER_FAILED")
        ],
        primary_failure=FailureRecord(
            domain=FailureDomain.SOLVER,
            code="SOLVER_FAILED",
            detail="solver failed",
        ),
        terminal_blocker=FailureRecord(
            domain=FailureDomain.BACKEND,
            code="OVERLOADED",
            retryable=True,
            detail="backend overloaded",
        ),
        resume=ResumeMetadata(
            allowed=True,
            from_stage="MODEL_REPAIR_STARTED",
            reason="retryable backend failure",
        ),
        message="repair deferred",
    )
    outcome = NativeAgentOutcome(
        run_dir=tmp_path / "run-deferred",
        summary=summary,
    )

    assert classify_qualification(outcome, [], []) == "DEFERRED_BACKEND"


def test_report_preserves_protocol_order_and_mpi_rendering(
    tmp_path: Path,
) -> None:
    outcome = _outcome(tmp_path, status="PLAN_INVALID")
    outcome.run_dir.mkdir(parents=True)
    (outcome.run_dir / "execution-plan.json").write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "executable": "buoyantFoam",
                        "args": ["-caseOption", "-parallel"],
                        "mpi_ranks": 15,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_qualification_report(
        [
            {
                "case_id": "buoyant-cavity",
                "outcome": outcome,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 1.0,
                "message": "failed",
            }
        ],
        backend_id="test-backend",
        model_name="gpt-test",
    )

    assert report.results[0].case_id == "buoyant-cavity"
    assert report.results[0].openfoam_commands == [
        [
            "mpirun",
            "-n",
            "15",
            "buoyantFoam",
            "-caseOption",
            "-parallel",
        ]
    ]
    assert report.counts["FAIL_AGENT"] == 1


def test_report_aggregates_cold_and_warm_performance_evidence(
    tmp_path: Path,
) -> None:
    cold = _outcome(tmp_path / "cold", status="PLAN_INVALID")
    warm = _outcome(tmp_path / "warm", status="PLAN_INVALID")
    for outcome, kind, pre_solve, workflow, build in (
        (cold, "cold", 12.0, 20.0, 0.5),
        (warm, "warm_plan", 2.0, 8.0, 0.25),
    ):
        outcome.run_dir.mkdir(parents=True)
        (outcome.run_dir / "performance-summary.json").write_text(
            PerformanceSummary(
                path_kind=kind,
                workflow_seconds_before_manifest=workflow,
                time_to_first_openfoam_command_seconds=pre_solve,
                reuse=PerformanceReuse(
                    plan="hit" if kind == "warm_plan" else "disabled"
                ),
            ).model_dump_json(),
            encoding="utf-8",
        )
        (outcome.run_dir / "artifact-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "build_seconds": build,
                    "files": {},
                }
            ),
            encoding="utf-8",
        )

    report = build_qualification_report(
        [
            {
                "case_id": "laminar-cavity",
                "outcome": cold,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 20.5,
                "message": "cold",
            },
            {
                "case_id": "buoyant-cavity",
                "outcome": warm,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 8.25,
                "message": "warm",
            },
        ],
        backend_id="test",
        model_name="test",
    )

    assert report.results[0].path_kind == "cold"
    assert report.results[1].path_kind == "warm_plan"
    assert report.results[1].end_to_end_latency_seconds == 8.25
    assert report.aggregates.cold_path_pre_solve.count == 1
    assert report.aggregates.cold_path_pre_solve.p50_seconds == 12
    assert report.aggregates.warm_path_pre_solve.p95_seconds == 2
    assert report.aggregates.end_to_end_latency.count == 2
    assert report.aggregates.plan_reuse_hit_count == 1


def test_report_marks_missing_performance_evidence_without_guessing(
    tmp_path: Path,
) -> None:
    outcome = _outcome(tmp_path, status="PLAN_INVALID")

    report = build_qualification_report(
        [
            {
                "case_id": "laminar-cavity",
                "outcome": outcome,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 1.0,
                "message": "missing performance",
            }
        ],
        backend_id="test",
        model_name="test",
    )

    result = report.results[0]
    assert result.path_kind is None
    assert result.pre_solve_latency_seconds is None
    assert result.end_to_end_latency_seconds is None
    assert result.performance_evidence_complete is False
    assert report.aggregates.cold_path_pre_solve.count == 0


def test_run_metadata_does_not_mark_diagnostic_performance_complete(
    tmp_path: Path,
) -> None:
    outcome = _outcome(tmp_path, status="PLAN_INVALID")
    outcome.run_dir.mkdir(parents=True)
    (outcome.run_dir / "performance-summary.json").write_text(
        PerformanceSummary(
            path_kind="cold",
            workflow_seconds_before_manifest=2,
            diagnostics=["workflow event line 2 is invalid"],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (outcome.run_dir / "artifact-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "build_seconds": 0.1,
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    metadata = run_metadata(outcome)

    assert metadata["end_to_end_latency_seconds"] == 2.1
    assert metadata["performance_evidence_complete"] is False


def test_run_metadata_recognizes_target_solver_inside_mpi_launcher(
    tmp_path: Path,
) -> None:
    outcome = _outcome(
        tmp_path,
        status="PUBLIC_VALIDATION_PASS",
        attempts=[
            AttemptSummary(
                attempt=1,
                status="PUBLIC_VALIDATION_PASS",
            )
        ],
    )
    attempt = outcome.run_dir / "attempt-01"
    attempt.mkdir(parents=True)
    (attempt / "run-result.json").write_text(
        json.dumps(
            {
                "case_dir": "case",
                "failed_step_id": None,
                "timed_out": False,
                "steps": [
                    {
                        "step_id": "solve",
                        "command": [
                            "mpirun",
                            "-n",
                            "4",
                            "simpleFoam",
                            "-parallel",
                        ],
                        "return_code": 0,
                        "timed_out": False,
                        "started_at": "2026-07-31T00:00:00Z",
                        "finished_at": "2026-07-31T00:00:01Z",
                        "stdout_path": "stdout.log",
                        "stderr_path": "stderr.log",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    metadata = run_metadata(
        outcome,
        expected_application="simpleFoam",
    )

    assert metadata["target_solver_started"] is True
    assert metadata["solver_normal_completion"] is True


def test_run_metadata_accumulates_parent_child_model_usage(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    parent_dir = store.create_run()
    parent_summary = RunSummary(
        task_id="laminar-cavity",
        workflow_state=WorkflowState.DEFERRED,
        terminal_blocker=FailureRecord(
            domain=FailureDomain.BACKEND,
            code="OVERLOADED",
            retryable=True,
            detail="deferred",
        ),
        resume=ResumeMetadata(
            allowed=True,
            from_stage="MODEL_GENERATION_STARTED",
            reason="retryable",
        ),
        message="deferred",
    )
    (parent_dir / "summary.json").write_text(
        parent_summary.model_dump_json() + "\n",
        encoding="utf-8",
    )
    (parent_dir / "model-configuration.json").write_text(
        json.dumps(
            {
                "logical_model_requests": 1,
                "transport_attempts": 3,
                "model_time_seconds": 12.5,
            }
        ),
        encoding="utf-8",
    )
    store.finalize(parent_dir)

    child_dir = store.create_run()
    child_summary = RunSummary(
        task_id="laminar-cavity",
        workflow_state=WorkflowState.COMPLETED,
        native_status="PUBLIC_VALIDATION_PASS",
        parent_run=ParentRun(
            run_id=parent_dir.name,
            manifest_sha256=store.manifest_sha256(parent_dir),
        ),
        resume=ResumeMetadata(allowed=False, reason="completed"),
        message="passed",
    )
    (child_dir / "model-configuration.json").write_text(
        json.dumps(
            {
                "logical_model_requests": 1,
                "transport_attempts": 1,
                "model_time_seconds": 2.5,
            }
        ),
        encoding="utf-8",
    )
    outcome = NativeAgentOutcome(
        run_dir=child_dir,
        summary=child_summary,
    )

    metadata = run_metadata(outcome)

    assert metadata["logical_model_requests"] == 2
    assert metadata["transport_attempts"] == 4
    assert metadata["model_time_seconds"] == 15


def test_report_accepts_generic_protocol_and_case_order(
    tmp_path: Path,
) -> None:
    first = _outcome(tmp_path / "first", status="PLAN_INVALID")
    second = _outcome(tmp_path / "second", status="PLAN_INVALID")
    report = build_qualification_report(
        [
            {
                "case_id": "first",
                "outcome": first,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 1.0,
                "message": "failed",
            },
            {
                "case_id": "second",
                "outcome": second,
                "manifest_issues": [],
                "metrics": [],
                "duration_seconds": 1.0,
                "message": "failed",
            },
        ],
        backend_id="test-backend",
        model_name="gpt-test",
        protocol_id="custom-suite-v1",
        case_order=("second", "first"),
    )

    assert report.protocol_id == "custom-suite-v1"
    assert [result.case_id for result in report.results] == [
        "second",
        "first",
    ]
    assert markdown_report(report).startswith(
        "# FoamPilot custom-suite-v1 qualification\n"
    )
