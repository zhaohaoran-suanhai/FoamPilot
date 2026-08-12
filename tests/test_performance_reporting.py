from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from foampilot.performance import (
    PerformanceReuse,
    build_performance_summary,
)
from foampilot.workflow import (
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
)


def _at(seconds: float) -> datetime:
    return datetime(2026, 8, 5, tzinfo=timezone.utc) + timedelta(
        seconds=seconds
    )


def _event(
    sequence: int,
    stage: WorkflowStage,
    state: WorkflowEventState,
    seconds: float,
    *,
    attempt: int | None = None,
    step_id: str | None = None,
) -> WorkflowEvent:
    return WorkflowEvent(
        sequence=sequence,
        stage=stage,
        state=state,
        occurred_at=_at(seconds),
        attempt=attempt,
        step_id=step_id,
    )


def test_performance_summary_is_recomputed_from_run_evidence(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    attempt = run_dir / "attempt-01"
    attempt.mkdir(parents=True)
    events = [
        _event(1, WorkflowStage.TASK_VALIDATED, WorkflowEventState.COMPLETED, 0),
        _event(2, WorkflowStage.ENVIRONMENT_READY, WorkflowEventState.COMPLETED, 1),
        _event(3, WorkflowStage.ROUTING_READY, WorkflowEventState.COMPLETED, 2),
        _event(4, WorkflowStage.CONTEXT_READY, WorkflowEventState.COMPLETED, 2.5),
        _event(
            5,
            WorkflowStage.MODEL_GENERATION_STARTED,
            WorkflowEventState.STARTED,
            3,
        ),
        _event(6, WorkflowStage.PLAN_READY, WorkflowEventState.COMPLETED, 8),
        _event(
            7,
            WorkflowStage.CASE_MATERIALIZED,
            WorkflowEventState.COMPLETED,
            8.5,
            attempt=1,
        ),
        _event(
            8,
            WorkflowStage.STATIC_INSPECTION_COMPLETE,
            WorkflowEventState.COMPLETED,
            9,
            attempt=1,
        ),
        _event(
            9,
            WorkflowStage.OPENFOAM_STEP_STARTED,
            WorkflowEventState.STARTED,
            10,
            attempt=1,
            step_id="mesh",
        ),
        _event(
            10,
            WorkflowStage.OPENFOAM_STEP_COMPLETE,
            WorkflowEventState.COMPLETED,
            12,
            attempt=1,
            step_id="mesh",
        ),
        _event(
            11,
            WorkflowStage.OPENFOAM_STEP_STARTED,
            WorkflowEventState.STARTED,
            12.5,
            attempt=1,
            step_id="solve",
        ),
        _event(
            12,
            WorkflowStage.OPENFOAM_STEP_COMPLETE,
            WorkflowEventState.COMPLETED,
            17.5,
            attempt=1,
            step_id="solve",
        ),
        _event(
            13,
            WorkflowStage.MESH_QUALITY_COMPLETE,
            WorkflowEventState.COMPLETED,
            18,
            attempt=1,
        ),
        _event(
            14,
            WorkflowStage.PUBLIC_VALIDATION_COMPLETE,
            WorkflowEventState.COMPLETED,
            19,
            attempt=1,
        ),
        _event(15, WorkflowStage.RUN_FINALIZED, WorkflowEventState.COMPLETED, 20),
    ]
    (run_dir / "workflow-events.jsonl").write_text(
        "".join(item.model_dump_json() + "\n" for item in events),
        encoding="utf-8",
    )
    plan = {
        "schema_version": 4,
        "compiled_from_design_sha256": "a" * 64,
        "compiler_identities": {"test.fixture": "1.0.0/protocol-1"},
        "manifest": {
            "solver_executable": "icoFoam",
            "solver_family": "incompressible_transient",
            "regime": "transient",
            "physics_family": "incompressible",
            "mesh_family": "blockMesh",
            "dimensionality": "2d",
            "regions": [{"name": "region0", "kind": "fluid", "path_prefix": ""}],
            "fields": [],
            "patches": [],
            "models": {},
        },
        "files": [{"path": "system/controlDict", "content": "application icoFoam;"}],
        "commands": [
            {
                "step_id": "mesh",
                "stage": "mesh",
                "executable": "blockMesh",
                "args": [],
                "mpi_ranks": 1,
                "timeout_seconds": 30,
            },
            {
                "step_id": "solve",
                "stage": "solve",
                "executable": "icoFoam",
                "args": [],
                "mpi_ranks": 1,
                "timeout_seconds": 30,
            },
        ],
    }
    (attempt / "execution-plan.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )
    (attempt / "run-result.json").write_text(
        json.dumps(
            {
                "case_dir": str(attempt / "case"),
                "steps": [
                    {
                        "step_id": "mesh",
                        "command": ["blockMesh"],
                        "return_code": 0,
                        "started_at": _at(10).isoformat(),
                        "finished_at": _at(12).isoformat(),
                        "elapsed_seconds": 2.0,
                        "timed_out": False,
                        "stdout_path": str(attempt / "mesh.out"),
                        "stderr_path": str(attempt / "mesh.err"),
                        "execution_backend": "host",
                        "backend_fallback_reason": None,
                    },
                    {
                        "step_id": "solve",
                        "command": ["icoFoam"],
                        "return_code": 0,
                        "started_at": _at(12.5).isoformat(),
                        "finished_at": _at(17.5).isoformat(),
                        "elapsed_seconds": 5.0,
                        "timed_out": False,
                        "stdout_path": str(attempt / "solve.out"),
                        "stderr_path": str(attempt / "solve.err"),
                        "execution_backend": "host",
                        "backend_fallback_reason": None,
                    },
                ],
                "failed_step_id": None,
                "timed_out": False,
            }
        ),
        encoding="utf-8",
    )
    traces = [
        {
            "schema_version": 2,
            "purpose": "author-native-openfoam-case",
            "backend_id": "test",
            "model": "test-model",
            "request_hash": "a" * 64,
            "logical_request_id": "request-1",
            "transport_attempt": 1,
            "backend_ordinal": 1,
            "backend_attempt": 1,
            "switch_reason": None,
            "started_at": _at(3).isoformat(),
            "finished_at": _at(4).isoformat(),
            "elapsed_seconds": 1,
            "request_bytes": 10,
            "output_bytes": 0,
            "status_code": 503,
            "request_id": None,
            "error_code": "OVERLOADED",
            "retryable": True,
            "partial_output_bytes": 0,
            "deadline_reason": None,
        },
        {
            "schema_version": 2,
            "purpose": "author-native-openfoam-case",
            "backend_id": "test",
            "model": "test-model",
            "request_hash": "a" * 64,
            "logical_request_id": "request-1",
            "transport_attempt": 2,
            "backend_ordinal": 1,
            "backend_attempt": 2,
            "switch_reason": None,
            "started_at": _at(6).isoformat(),
            "finished_at": _at(8).isoformat(),
            "elapsed_seconds": 2,
            "request_bytes": 10,
            "output_bytes": 100,
            "status_code": 200,
            "request_id": None,
            "error_code": None,
            "retryable": None,
            "partial_output_bytes": 0,
            "deadline_reason": None,
        },
    ]
    (run_dir / "model-attempts.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in traces),
        encoding="utf-8",
    )

    summary = build_performance_summary(
        run_dir,
        path_kind="cold",
        reuse=PerformanceReuse(),
    )

    assert summary.workflow_seconds_before_manifest == 20
    assert summary.time_to_first_openfoam_command_seconds == 10
    assert summary.stages.environment_seconds == 1
    assert summary.stages.geometry_seconds == 0
    assert summary.stages.routing_seconds == 1
    assert summary.stages.context_seconds == 0.5
    assert summary.stages.generation_seconds == 5
    assert summary.stages.materialization_seconds == 0.5
    assert summary.stages.inspection_seconds == 0.5
    assert summary.stages.mesh_seconds == 2
    assert summary.stages.solver_seconds == 5
    assert summary.stages.validation_seconds == 1
    assert summary.model.logical_requests == 1
    assert summary.model.transport_attempts == 2
    assert summary.model.retry_delay_seconds == 2


def test_performance_summary_uses_null_for_incomplete_evidence(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "workflow-events.jsonl").write_text(
        _event(
            1,
            WorkflowStage.TASK_VALIDATED,
            WorkflowEventState.COMPLETED,
            0,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    summary = build_performance_summary(
        run_dir,
        path_kind="cold",
        reuse=PerformanceReuse(),
    )

    assert summary.workflow_seconds_before_manifest is None
    assert summary.stages.environment_seconds is None
    assert summary.stages.generation_seconds == 0
    assert "RUN_FINALIZED event is missing" in summary.diagnostics
