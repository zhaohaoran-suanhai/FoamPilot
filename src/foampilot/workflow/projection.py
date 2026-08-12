"""One truthful read-only workflow projection for every user interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.artifacts import ArtifactStore
from foampilot.evidence import MetricPoint, MetricsProjection
from foampilot.acceptance import ResultReport
from foampilot.postprocessing import DerivedMetrics

from .events import WorkflowEvent
from .models import WorkflowEventState, WorkflowStage
from .services import CANONICAL_STAGE_ORDER


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageProgress(_FrozenModel):
    completed: int = Field(ge=0)
    total: int = Field(ge=1)


class ResidualProjection(_FrozenModel):
    attempt: int | None = None
    step_id: str
    simulation_time: float | None = None
    field: str
    initial: float
    final: float | None = None


class PendingQuestion(_FrozenModel):
    question_id: str
    field_path: str | None = None
    prompt_zh: str | None = None


class FailureSummary(_FrozenModel):
    code: str
    layer: str | None = None
    detail: str | None = None
    confirmed_causes: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    automatic_repair_reason: str | None = None


class WorkflowProjection(_FrozenModel):
    schema_version: Literal[1] = 1
    current_stage: WorkflowStage | None
    stage_progress: StageProgress
    active_operation: str | None
    latest_solver_time: float | None
    recent_residuals: tuple[ResidualProjection, ...]
    pending_questions: tuple[PendingQuestion, ...]
    failure_summary: FailureSummary | None
    derived_metrics: DerivedMetrics | None = None
    result_report: ResultReport | None = None
    artifact_links: tuple[str, ...]
    warnings: tuple[str, ...]


_TOTAL_STAGES = len(CANONICAL_STAGE_ORDER)


def _result_artifacts(
    run_dir: Path,
    files: set[str],
) -> tuple[DerivedMetrics | None, ResultReport | None, list[str]]:
    warnings: list[str] = []
    metrics: DerivedMetrics | None = None
    report: ResultReport | None = None
    if "derived-metrics.json" in files:
        try:
            metrics = DerivedMetrics.model_validate_json(
                (run_dir / "derived-metrics.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            warnings.append("DERIVED_METRICS_INVALID")
    if "result-report.json" in files:
        try:
            report = ResultReport.model_validate_json(
                (run_dir / "result-report.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            warnings.append("RESULT_REPORT_INVALID")
    if metrics is not None and report is not None:
        if (
            metrics.canonical_sha256() != report.derived_metrics_sha256
            or metrics.run_facts_sha256 != report.run_facts_sha256
            or metrics.observation_plan_sha256
            != report.observation_plan_sha256
        ):
            warnings.append("RESULT_EVIDENCE_HASH_MISMATCH")
            metrics = None
            report = None
    return metrics, report, warnings


def _manifested_files(run_dir: Path) -> tuple[set[str], list[str]]:
    manifest = run_dir / ArtifactStore.manifest_name
    warnings: list[str] = []
    if not manifest.is_file():
        return (
            {
                path.relative_to(run_dir).as_posix()
                for path in run_dir.rglob("*")
                if path.is_file() and not path.is_symlink()
            },
            warnings,
        )
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        files = payload.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("manifest files must be a mapping")
        registered = {
            str(path)
            for path, metadata in files.items()
            if isinstance(metadata, dict) and metadata.get("type") == "file"
        }
        issues = ArtifactStore(run_dir.parent).verify(run_dir)
        blocking = [
            issue
            for issue in issues
            if not issue.startswith("unexpected artifact:")
        ]
        if blocking:
            return set(), ["MANIFEST_INVALID"]
        if issues:
            warnings.append("UNMANIFESTED_ARTIFACTS_IGNORED")
        return registered, warnings
    except (OSError, ValueError, json.JSONDecodeError):
        return set(), ["MANIFEST_INVALID"]


def _events(run_dir: Path) -> tuple[list[WorkflowEvent], list[str]]:
    path = run_dir / "workflow-events.jsonl"
    if not path.is_file():
        return [], ["WORKFLOW_EVENTS_MISSING"]
    result: list[WorkflowEvent] = []
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], ["WORKFLOW_EVENTS_UNREADABLE"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            result.append(WorkflowEvent.model_validate_json(line))
        except ValueError:
            warnings.append(f"WORKFLOW_EVENT_INVALID:{line_number}")
    return result, warnings


def _residuals(metrics: MetricsProjection) -> tuple[ResidualProjection, ...]:
    initial: dict[tuple[int | None, str, float | None, str], MetricPoint] = {}
    final: dict[tuple[int | None, str, float | None, str], float] = {}
    for point in metrics.points:
        if point.series.startswith("residual-final:"):
            field = point.series.removeprefix("residual-final:")
            final[(point.attempt, point.step_id, point.simulation_time, field)] = (
                point.value
            )
        elif point.series.startswith("residual:"):
            field = point.series.removeprefix("residual:")
            initial[(point.attempt, point.step_id, point.simulation_time, field)] = (
                point
            )
    values = [
        ResidualProjection(
            attempt=key[0],
            step_id=key[1],
            simulation_time=key[2],
            field=key[3],
            initial=point.value,
            final=final.get(key),
        )
        for key, point in initial.items()
    ]
    return tuple(values[-200:])


def _questions(run_dir: Path, files: set[str]) -> tuple[PendingQuestion, ...]:
    if "questions.json" not in files:
        return ()
    try:
        payload = json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))
        raw = payload.get("questions", []) if isinstance(payload, dict) else []
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(
        PendingQuestion(
            question_id=str(item["question_id"]),
            field_path=(str(item["field_path"]) if item.get("field_path") else None),
            prompt_zh=(str(item["prompt_zh"]) if item.get("prompt_zh") else None),
        )
        for item in raw
        if isinstance(item, dict) and item.get("question_id")
    )


def _failure(run_dir: Path, files: set[str]) -> FailureSummary | None:
    failure_paths = sorted(
        (path for path in files if path.endswith("failure-report.json")),
        reverse=True,
    )
    if failure_paths:
        try:
            payload = json.loads(
                (run_dir / failure_paths[0]).read_text(encoding="utf-8")
            )
            return FailureSummary(
                code=str(payload["failure_code"]),
                layer=(str(payload["failure_layer"]) if payload.get("failure_layer") else None),
                detail=(str(payload["recommended_actions"][0]) if payload.get("recommended_actions") else None),
                confirmed_causes=tuple(
                    str(item["code"])
                    for item in payload.get("confirmed_causes", [])
                    if isinstance(item, dict) and item.get("code")
                ),
                hypotheses=tuple(
                    str(item["code"])
                    for item in payload.get("hypotheses", [])
                    if isinstance(item, dict) and item.get("code")
                ),
                automatic_repair_reason=(
                    str(payload["automatic_repair"].get("reason"))
                    if isinstance(payload.get("automatic_repair"), dict)
                    else None
                ),
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            pass
    if "summary.json" not in files:
        return None
    try:
        summary = ArtifactStore.read_summary(run_dir)
    except (OSError, ValueError):
        return None
    failure = summary.terminal_blocker or summary.primary_failure
    return (
        FailureSummary(
            code=failure.code,
            layer=failure.domain.value,
            detail=failure.detail,
        )
        if failure is not None
        else None
    )


def _operation(event: WorkflowEvent | None) -> str | None:
    if event is None:
        return None
    if event.stage == WorkflowStage.WAITING_FOR_CONFIRMATION:
        return "awaiting_confirmation"
    if event.stage == WorkflowStage.WAITING_FOR_INFORMATION:
        return "awaiting_information"
    if event.stage == WorkflowStage.RUN_FINALIZED:
        return None
    return event.stage.value.casefold()


def build_workflow_projection(
    run_dir: str | Path,
    *,
    trusted_files: set[str] | None = None,
    trust_warnings: tuple[str, ...] = (),
) -> WorkflowProjection:
    root = Path(run_dir).resolve()
    if trusted_files is None:
        files, warnings = _manifested_files(root)
    else:
        files = set(trusted_files)
        warnings = list(trust_warnings)
    events, event_warnings = _events(root)
    warnings.extend(event_warnings)
    current = events[-1] if events else None
    completed = len(
        {
            item.stage
            for item in events
            if item.state == WorkflowEventState.COMPLETED
            and item.stage != WorkflowStage.RUN_FINALIZED
        }
    )
    metrics = MetricsProjection.from_file(root / "metrics.jsonl")
    if "metrics.jsonl" not in files:
        warnings.append("LEGACY_METRICS_UNAVAILABLE")
        metrics = MetricsProjection()
    else:
        warnings.extend(metrics.warnings)
    latest_time = max(
        (
            point.simulation_time
            for point in metrics.points
            if point.simulation_time is not None
        ),
        default=None,
    )
    derived_metrics, result_report, result_warnings = _result_artifacts(
        root,
        files,
    )
    warnings.extend(result_warnings)
    links = tuple(
        sorted(
            path
            for path in files
            if path in {"summary.json", "questions.json", "failure-report.json"}
            or path.endswith("/run-facts.json")
            or path.endswith("/public-validation.json")
            or path.endswith("/mesh-quality-report.json")
            or path in {"derived-metrics.json", "result-report.json"}
        )
    )
    return WorkflowProjection(
        current_stage=(current.stage if current is not None else None),
        stage_progress=StageProgress(completed=completed, total=_TOTAL_STAGES),
        active_operation=_operation(current),
        latest_solver_time=latest_time,
        recent_residuals=_residuals(metrics),
        pending_questions=_questions(root, files),
        failure_summary=_failure(root, files),
        derived_metrics=derived_metrics,
        result_report=result_report,
        artifact_links=links,
        warnings=tuple(dict.fromkeys(warnings)),
    )


__all__ = [
    "FailureSummary",
    "PendingQuestion",
    "ResidualProjection",
    "StageProgress",
    "WorkflowProjection",
    "build_workflow_projection",
]
