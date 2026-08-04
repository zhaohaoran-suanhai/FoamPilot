"""Qualification classification and deterministic report serialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foampilot.artifacts import ArtifactStore, NativeAgentOutcome

from .models import (
    QualificationAggregates,
    QualificationMetric,
    QualificationReport,
    QualificationResult,
    QualificationStatus,
)
from foampilot.workflow import FailureDomain, WorkflowState


CASE_ORDER = (
    "laminar-cavity",
    "potential-cylinder",
    "rans-pitzdaily",
    "multiphase-dam-break",
    "compressible-shock-tube",
    "buoyant-cavity",
)
_MPI_LAUNCHERS = {"mpirun", "mpiexec", "orterun"}


def _native_executable(command: object) -> str:
    if not isinstance(command, list) or not command:
        return ""
    executable = str(command[0])
    if (
        executable in _MPI_LAUNCHERS
        and len(command) >= 4
        and command[1] in {"-n", "-np"}
    ):
        return str(command[3])
    return executable


def native_case_dir(outcome: NativeAgentOutcome) -> Path | None:
    """Return the case directory from the final bounded attempt."""

    if not outcome.summary.attempts:
        return None
    attempt = outcome.summary.attempts[-1].attempt
    return outcome.run_dir / f"attempt-{attempt:02d}" / "case"


def classify_qualification(
    outcome: NativeAgentOutcome,
    manifest_issues: list[str],
    metrics: list[QualificationMetric],
    *,
    evaluation_level: str = "physics_qualification",
) -> QualificationStatus:
    """Preserve environment and Agent failures before physics comparison."""

    summary = outcome.summary
    if (
        summary.workflow_state == WorkflowState.DEFERRED
        and summary.terminal_blocker is not None
        and summary.terminal_blocker.domain == FailureDomain.BACKEND
    ):
        return "DEFERRED_BACKEND"
    failures = [
        item
        for item in (
            summary.primary_failure,
            summary.terminal_blocker,
        )
        if item is not None
    ]
    if any(
        item.domain == FailureDomain.ENVIRONMENT for item in failures
    ):
        return "BLOCKED_ENVIRONMENT"
    if summary.native_status != "PUBLIC_VALIDATION_PASS":
        return "FAIL_AGENT"
    if manifest_issues:
        return "FAIL_AGENT"
    if evaluation_level == "public_validation":
        return "PASS"
    if not metrics or any(
        metric.required and metric.passed is None
        for metric in metrics
    ):
        return "INVALID_QUALIFICATION"
    if any(metric.required and not metric.passed for metric in metrics):
        return "FAIL_AGENT"
    return "PASS"


def _json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be a mapping: {path}")
    return payload


def _lineage_run_dirs(outcome: NativeAgentOutcome) -> list[Path]:
    """Return current-to-root verified run directories without cycles."""

    store = ArtifactStore(outcome.run_dir.parent)
    directories: list[Path] = []
    seen: set[Path] = set()
    current = outcome.run_dir.resolve()
    summary = outcome.summary
    while current not in seen:
        seen.add(current)
        directories.append(current)
        parent = summary.parent_run
        if parent is None:
            break
        candidate = (store.root / parent.run_id).resolve()
        if (
            not candidate.is_relative_to(store.root)
            or store.verify(candidate)
            or store.manifest_sha256(candidate)
            != parent.manifest_sha256
        ):
            break
        summary = store.read_summary(candidate)
        current = candidate
    return directories


def run_metadata(
    outcome: NativeAgentOutcome,
    *,
    expected_application: str | None = None,
) -> dict[str, Any]:
    """Read model, retrieval, and command metadata from immutable artifacts."""

    logical_model_requests = 0
    transport_attempts = 0
    model_time_seconds = 0.0
    selected: list[str] = []
    commands: list[list[str]] = []
    for lineage_run in _lineage_run_dirs(outcome):
        model_path = lineage_run / "model-configuration.json"
        if model_path.is_file():
            model_payload = _json_file(model_path)
            logical_model_requests += int(
                model_payload.get(
                    "logical_model_requests",
                    model_payload.get("total_model_calls", 0),
                )
            )
            transport_attempts += int(
                model_payload.get("transport_attempts", 0)
            )
            model_time_seconds += float(
                model_payload.get("model_time_seconds", 0)
            )
    context_path = outcome.run_dir / "agent-context.json"
    if context_path.is_file():
        selected = [
            str(item)
            for item in _json_file(context_path).get(
                "selected_knowledge_ids",
                [],
            )
        ]
    plan_path = outcome.run_dir / "execution-plan.json"
    if plan_path.is_file():
        for command in _json_file(plan_path).get("commands", []):
            if not isinstance(command, dict):
                continue
            executable = str(command.get("executable", ""))
            arguments = [str(item) for item in command.get("args", [])]
            ranks = int(command.get("mpi_ranks", 1))
            if ranks > 1:
                arguments = [
                    item for item in arguments if item != "-parallel"
                ]
                commands.append(
                    [
                        "mpirun",
                        "-n",
                        str(ranks),
                        executable,
                        *arguments,
                        "-parallel",
                    ]
                )
            else:
                commands.append([executable, *arguments])
    native_execution_started = False
    mesh_generation_pass: bool | None = None
    check_mesh_pass: bool | None = None
    target_solver_started = False
    solver_normal_completion = False
    openfoam_time_seconds = 0.0
    time_to_first_openfoam_command: float | None = None
    first_workflow_at: datetime | None = None
    workflow_path = outcome.run_dir / "workflow-events.jsonl"
    if workflow_path.is_file():
        for line in workflow_path.read_text(
            encoding="utf-8"
        ).splitlines():
            event = _json_file_from_text(line)
            occurred = event.get("occurred_at")
            if isinstance(occurred, str):
                parsed = datetime.fromisoformat(occurred)
                if first_workflow_at is None or parsed < first_workflow_at:
                    first_workflow_at = parsed

    if outcome.summary.attempts:
        attempt = outcome.summary.attempts[-1].attempt
        run_result_path = (
            outcome.run_dir
            / f"attempt-{attempt:02d}"
            / "run-result.json"
        )
        if run_result_path.is_file():
            run_result = _json_file(run_result_path)
            steps = [
                item
                for item in run_result.get("steps", [])
                if isinstance(item, dict)
            ]
            native_execution_started = bool(steps)
            if steps:
                first_started = steps[0].get("started_at")
                if (
                    isinstance(first_started, str)
                    and first_workflow_at is not None
                ):
                    time_to_first_openfoam_command = max(
                        (
                            datetime.fromisoformat(first_started)
                            - first_workflow_at
                        ).total_seconds(),
                        0,
                    )
            for step in steps:
                command = step.get("command")
                executable = _native_executable(command)
                passed = (
                    step.get("return_code") == 0
                    and not bool(step.get("timed_out", False))
                )
                if executable in {"blockMesh", "gmsh"}:
                    mesh_generation_pass = passed
                if executable == "checkMesh":
                    check_mesh_pass = passed
                if (
                    expected_application is not None
                    and executable == expected_application
                ):
                    target_solver_started = True
                    solver_normal_completion = passed
                started_at = step.get("started_at")
                finished_at = step.get("finished_at")
                if isinstance(started_at, str) and isinstance(
                    finished_at,
                    str,
                ):
                    openfoam_time_seconds += max(
                        (
                            datetime.fromisoformat(finished_at)
                            - datetime.fromisoformat(started_at)
                        ).total_seconds(),
                        0,
                    )
    return {
        "logical_model_requests": logical_model_requests,
        "transport_attempts": transport_attempts,
        "model_time_seconds": model_time_seconds,
        "selected_knowledge_ids": selected,
        "openfoam_commands": commands,
        "generation_success": (
            outcome.run_dir / "execution-plan.json"
        ).is_file(),
        "native_execution_started": native_execution_started,
        "mesh_generation_pass": mesh_generation_pass,
        "check_mesh_pass": check_mesh_pass,
        "target_solver_started": target_solver_started,
        "solver_normal_completion": solver_normal_completion,
        "time_to_first_openfoam_command": (
            time_to_first_openfoam_command
        ),
        "openfoam_time_seconds": openfoam_time_seconds,
    }


def _json_file_from_text(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("JSON line root must be a mapping")
    return payload


def qualification_result(
    *,
    case_id: str,
    outcome: NativeAgentOutcome,
    manifest_issues: list[str],
    metrics: list[QualificationMetric],
    duration_seconds: float,
    message: str,
    expected_application: str | None = None,
    evaluation_level: str = "physics_qualification",
) -> QualificationResult:
    """Convert one native outcome and evaluator evidence into one verdict."""

    metadata = run_metadata(
        outcome,
        expected_application=expected_application,
    )
    verdict = classify_qualification(
        outcome,
        manifest_issues,
        metrics,
        evaluation_level=evaluation_level,
    )
    physics_pass = bool(
        evaluation_level == "physics_qualification"
        and metrics
        and all(
            not metric.required or metric.passed is True
            for metric in metrics
        )
    )
    return QualificationResult(
        case_id=case_id,
        evaluation_level=evaluation_level,
        status=verdict,
        workflow_state=outcome.summary.workflow_state.value,
        native_status=outcome.summary.native_status,
        run_dir=outcome.run_dir,
        attempts=len(outcome.summary.attempts),
        model_calls=metadata["logical_model_requests"],
        logical_model_requests=metadata["logical_model_requests"],
        transport_attempts=metadata["transport_attempts"],
        model_time_seconds=metadata["model_time_seconds"],
        backend_deferred=(verdict == "DEFERRED_BACKEND"),
        generation_success=metadata["generation_success"],
        native_execution_started=metadata[
            "native_execution_started"
        ],
        mesh_generation_pass=metadata["mesh_generation_pass"],
        check_mesh_pass=metadata["check_mesh_pass"],
        target_solver_started=metadata["target_solver_started"],
        solver_normal_completion=metadata[
            "solver_normal_completion"
        ],
        public_validation_pass=(
            outcome.summary.native_status == "PUBLIC_VALIDATION_PASS"
        ),
        physics_qualification_pass=physics_pass,
        time_to_first_openfoam_command=metadata[
            "time_to_first_openfoam_command"
        ],
        openfoam_time_seconds=metadata["openfoam_time_seconds"],
        selected_knowledge_ids=metadata["selected_knowledge_ids"],
        openfoam_commands=metadata["openfoam_commands"],
        manifest_issues=manifest_issues,
        metrics=metrics,
        duration_seconds=duration_seconds,
        message=message,
    )


def build_qualification_report(
    raw_results: list[dict[str, Any]],
    *,
    backend_id: str,
    model_name: str,
    protocol_id: str = "official-six-v1",
    case_order: tuple[str, ...] = CASE_ORDER,
) -> QualificationReport:
    """Build one deterministically ordered qualification report."""

    results = [
        qualification_result(**record)
        for record in raw_results
    ]
    results.sort(key=lambda item: case_order.index(item.case_id))
    statuses: tuple[QualificationStatus, ...] = (
        "PASS",
        "FAIL_AGENT",
        "DEFERRED_BACKEND",
        "BLOCKED_ENVIRONMENT",
        "INVALID_QUALIFICATION",
    )
    return QualificationReport(
        protocol_id=protocol_id,
        created_at=datetime.now(timezone.utc),
        backend_id=backend_id,
        model_name=model_name,
        automatic_failover=False,
        counts={
            status: sum(item.status == status for item in results)
            for status in statuses
        },
        aggregates=QualificationAggregates(
            task_count=len(results),
            logical_model_requests=sum(
                item.logical_model_requests for item in results
            ),
            transport_attempts=sum(
                item.transport_attempts for item in results
            ),
            backend_deferred_count=sum(
                item.backend_deferred for item in results
            ),
            generation_success_count=sum(
                item.generation_success for item in results
            ),
            native_execution_started_count=sum(
                item.native_execution_started for item in results
            ),
            mesh_generation_pass_count=sum(
                item.mesh_generation_pass is True for item in results
            ),
            check_mesh_pass_count=sum(
                item.check_mesh_pass is True for item in results
            ),
            target_solver_started_count=sum(
                item.target_solver_started for item in results
            ),
            solver_normal_completion_count=sum(
                item.solver_normal_completion for item in results
            ),
            public_validation_pass_count=sum(
                item.public_validation_pass for item in results
            ),
            physics_qualification_pass_count=sum(
                item.physics_qualification_pass for item in results
            ),
            model_time_seconds=sum(
                item.model_time_seconds for item in results
            ),
            openfoam_time_seconds=sum(
                item.openfoam_time_seconds for item in results
            ),
        ),
        results=results,
    )


def markdown_report(report: QualificationReport) -> str:
    """Render a compact human-readable qualification report."""

    lines = [
        f"# FoamPilot {report.protocol_id} qualification",
        "",
        f"- Protocol: `{report.protocol_id}`",
        f"- Backend: `{report.backend_id}`",
        f"- Model: `{report.model_name}`",
        "- Automatic failover: `false`",
        f"- Created: `{report.created_at.isoformat()}`",
        (
            "- Counts: "
            + ", ".join(
                f"{name}={value}"
                for name, value in report.counts.items()
            )
        ),
        "",
        (
            "| Case | Level | Verdict | Workflow | Native status | Attempts | "
            "Logical | Transport | Seconds |"
        ),
        "|---|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for result in report.results:
        lines.append(
            f"| {result.case_id} | {result.evaluation_level} | "
            f"{result.status} | "
            f"{result.workflow_state} | {result.native_status} | "
            f"{result.attempts} | {result.logical_model_requests} | "
            f"{result.transport_attempts} | "
            f"{result.duration_seconds:.1f} |"
        )
    for result in report.results:
        lines.extend(
            [
                "",
                f"## {result.case_id}",
                "",
                f"- Verdict: `{result.status}`",
                f"- Native artifact: `{result.run_dir}`",
                (
                    "- Manifest: verified"
                    if not result.manifest_issues
                    else "- Manifest: "
                    + "; ".join(result.manifest_issues)
                ),
                "- Evaluator checks:",
            ]
        )
        lines.extend(
            f"  - `{metric.name}`: "
            f"{'PASS' if metric.passed else 'FAIL'} — {metric.detail}"
            for metric in result.metrics
        )
        if not result.metrics:
            lines.append("  - not evaluated")
        lines.append(f"- Message: {result.message}")
    return "\n".join(lines) + "\n"
