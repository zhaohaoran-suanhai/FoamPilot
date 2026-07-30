"""Qualification classification and deterministic report serialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foampilot.artifacts import NativeAgentOutcome

from .models import (
    QualificationMetric,
    QualificationReport,
    QualificationResult,
    QualificationStatus,
)


CASE_ORDER = (
    "laminar-cavity",
    "potential-cylinder",
    "rans-pitzdaily",
    "multiphase-dam-break",
    "compressible-shock-tube",
    "buoyant-cavity",
)


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
) -> QualificationStatus:
    """Preserve environment and Agent failures before physics comparison."""

    if outcome.status == "BLOCKED_ENVIRONMENT":
        return "BLOCKED_ENVIRONMENT"
    if outcome.status != "PUBLIC_VALIDATION_PASS":
        return "FAIL_AGENT"
    if manifest_issues:
        return "FAIL_AGENT"
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


def run_metadata(
    outcome: NativeAgentOutcome,
) -> tuple[int, list[str], list[list[str]]]:
    """Read model, retrieval, and command metadata from immutable artifacts."""

    model_calls = 0
    selected: list[str] = []
    commands: list[list[str]] = []
    model_path = outcome.run_dir / "model-configuration.json"
    if model_path.is_file():
        model_calls = int(
            _json_file(model_path).get("total_model_calls", 0)
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
    return model_calls, selected, commands


def qualification_result(
    *,
    case_id: str,
    outcome: NativeAgentOutcome,
    manifest_issues: list[str],
    metrics: list[QualificationMetric],
    duration_seconds: float,
    message: str,
) -> QualificationResult:
    """Convert one native outcome and evaluator evidence into one verdict."""

    model_calls, selected, commands = run_metadata(outcome)
    return QualificationResult(
        case_id=case_id,
        status=classify_qualification(
            outcome,
            manifest_issues,
            metrics,
        ),
        native_status=outcome.status,
        run_dir=outcome.run_dir,
        attempts=len(outcome.summary.attempts),
        model_calls=model_calls,
        selected_knowledge_ids=selected,
        openfoam_commands=commands,
        manifest_issues=manifest_issues,
        metrics=metrics,
        duration_seconds=duration_seconds,
        message=message,
    )


def build_qualification_report(
    raw_results: list[dict[str, Any]],
    *,
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
        "BLOCKED_ENVIRONMENT",
        "INVALID_QUALIFICATION",
    )
    return QualificationReport(
        protocol_id=protocol_id,
        created_at=datetime.now(timezone.utc),
        model_name=model_name,
        counts={
            status: sum(item.status == status for item in results)
            for status in statuses
        },
        results=results,
    )


def markdown_report(report: QualificationReport) -> str:
    """Render a compact human-readable qualification report."""

    lines = [
        f"# FoamPilot {report.protocol_id} qualification",
        "",
        f"- Protocol: `{report.protocol_id}`",
        f"- Model: `{report.model_name}`",
        f"- Created: `{report.created_at.isoformat()}`",
        (
            "- Counts: "
            + ", ".join(
                f"{name}={value}"
                for name, value in report.counts.items()
            )
        ),
        "",
        "| Case | Verdict | Native status | Attempts | Calls | Seconds |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for result in report.results:
        lines.append(
            f"| {result.case_id} | {result.status} | "
            f"{result.native_status} | {result.attempts} | "
            f"{result.model_calls} | {result.duration_seconds:.1f} |"
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
