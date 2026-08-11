"""Rebuild performance summaries from immutable run evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from foampilot.models import ModelAttemptTrace
from foampilot.plans import CommandStage, ExecutionPlan
from foampilot.runtime import PlanRunResult
from foampilot.workflow import (
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
)

from .models import (
    ModelPerformance,
    PathKind,
    PerformanceReuse,
    PerformanceStages,
    PerformanceSummary,
    TaskBuilderPerformance,
)


def _seconds(start: datetime, finish: datetime) -> float:
    return max((finish - start).total_seconds(), 0.0)


def _events(run_dir: Path, diagnostics: list[str]) -> list[WorkflowEvent]:
    path = run_dir / "workflow-events.jsonl"
    if not path.is_file():
        diagnostics.append("workflow-events.jsonl is missing")
        return []
    result: list[WorkflowEvent] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            result.append(WorkflowEvent.model_validate_json(line))
        except ValueError:
            diagnostics.append(f"workflow event line {number} is invalid")
    return result


def _first(
    events: list[WorkflowEvent],
    stage: WorkflowStage,
    state: WorkflowEventState | None = None,
) -> WorkflowEvent | None:
    return next(
        (
            item
            for item in events
            if item.stage == stage and (state is None or item.state == state)
        ),
        None,
    )


def _last(
    events: list[WorkflowEvent],
    stage: WorkflowStage,
    state: WorkflowEventState | None = None,
    *,
    attempt: int | None = None,
) -> WorkflowEvent | None:
    matches = [
        item
        for item in events
        if item.stage == stage
        and (state is None or item.state == state)
        and (attempt is None or item.attempt == attempt)
    ]
    return matches[-1] if matches else None


def _single_interval(
    events: list[WorkflowEvent],
    start_stage: WorkflowStage,
    finish_stage: WorkflowStage,
    *,
    start_state: WorkflowEventState = WorkflowEventState.COMPLETED,
    finish_state: WorkflowEventState = WorkflowEventState.COMPLETED,
) -> float | None:
    start = _first(events, start_stage, start_state)
    finish = _first(events, finish_stage, finish_state)
    if start is None or finish is None:
        return None
    return _seconds(start.occurred_at, finish.occurred_at)


def _attempt_intervals(
    events: list[WorkflowEvent],
    start_stage: WorkflowStage,
    finish_stage: WorkflowStage,
) -> float:
    total = 0.0
    finishes = [
        item
        for item in events
        if item.stage == finish_stage
        and item.state == WorkflowEventState.COMPLETED
        and item.attempt is not None
    ]
    for finish in finishes:
        start = _last(
            [item for item in events if item.sequence < finish.sequence],
            start_stage,
            attempt=finish.attempt,
        )
        if start is not None:
            total += _seconds(start.occurred_at, finish.occurred_at)
    return total


def _materialization_seconds(events: list[WorkflowEvent]) -> float:
    total = 0.0
    for finish in (
        item
        for item in events
        if item.stage == WorkflowStage.CASE_MATERIALIZED
        and item.state == WorkflowEventState.COMPLETED
    ):
        candidates = [
            item
            for item in events
            if item.sequence < finish.sequence
            and item.state == WorkflowEventState.COMPLETED
            and item.stage
            in {WorkflowStage.PLAN_READY, WorkflowStage.REPAIR_APPLIED}
        ]
        if candidates:
            total += _seconds(candidates[-1].occurred_at, finish.occurred_at)
    return total


def _validation_seconds(events: list[WorkflowEvent]) -> float:
    total = 0.0
    for finish in (
        item
        for item in events
        if item.stage == WorkflowStage.PUBLIC_VALIDATION_COMPLETE
        and item.state == WorkflowEventState.COMPLETED
    ):
        candidates = [
            item
            for item in events
            if item.sequence < finish.sequence
            and item.attempt == finish.attempt
            and item.state == WorkflowEventState.COMPLETED
            and item.stage
            in {
                WorkflowStage.MESH_QUALITY_COMPLETE,
                WorkflowStage.OPENFOAM_STEP_COMPLETE,
                WorkflowStage.STATIC_INSPECTION_COMPLETE,
            }
        ]
        if candidates:
            total += _seconds(candidates[-1].occurred_at, finish.occurred_at)
    return total


def _repair_seconds(events: list[WorkflowEvent]) -> float | None:
    started = [
        item
        for item in events
        if item.stage == WorkflowStage.MODEL_REPAIR_STARTED
        and item.state == WorkflowEventState.STARTED
    ]
    if not started:
        return 0.0
    total = 0.0
    for start in started:
        finish = next(
            (
                item
                for item in events
                if item.sequence > start.sequence
                and item.stage == WorkflowStage.REPAIR_APPLIED
                and item.attempt == start.attempt
                and item.state == WorkflowEventState.COMPLETED
            ),
            None,
        )
        if finish is None:
            return None
        total += _seconds(start.occurred_at, finish.occurred_at)
    return total


def _native_seconds(
    run_dir: Path,
    diagnostics: list[str],
) -> dict[str, float]:
    result = {
        "mesh": 0.0,
        "initialize": 0.0,
        "solve": 0.0,
        "postprocess": 0.0,
    }
    stage_group = {
        CommandStage.MESH: "mesh",
        CommandStage.CHECK: "mesh",
        CommandStage.DECOMPOSE: "mesh",
        CommandStage.INITIALIZE: "initialize",
        CommandStage.SOLVE: "solve",
        CommandStage.RECONSTRUCT: "postprocess",
        CommandStage.POSTPROCESS: "postprocess",
    }
    for attempt_root in sorted(run_dir.glob("attempt-[0-9][0-9]")):
        plan_path = attempt_root / "execution-plan.json"
        result_path = attempt_root / "run-result.json"
        if not result_path.is_file():
            continue
        if not plan_path.is_file():
            diagnostics.append(f"{attempt_root.name}/execution-plan.json is missing")
            continue
        try:
            plan = ExecutionPlan.model_validate_json(
                plan_path.read_text(encoding="utf-8")
            )
            run_result = PlanRunResult.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
        except ValueError:
            diagnostics.append(f"{attempt_root.name} native timing evidence is invalid")
            continue
        command_stages = {item.step_id: item.stage for item in plan.commands}
        for step in run_result.steps:
            stage = command_stages.get(step.step_id)
            if stage is None:
                diagnostics.append(
                    f"{attempt_root.name} step {step.step_id} has no plan stage"
                )
                continue
            result[stage_group[stage]] += (
                step.elapsed_seconds
                if step.elapsed_seconds is not None
                else _seconds(step.started_at, step.finished_at)
            )
    return result


def _model_performance(
    run_dir: Path,
    diagnostics: list[str],
) -> ModelPerformance:
    path = run_dir / "model-attempts.jsonl"
    if not path.is_file():
        return ModelPerformance()
    traces: list[ModelAttemptTrace] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            traces.append(ModelAttemptTrace.model_validate_json(line))
        except ValueError:
            diagnostics.append(f"model attempt line {number} is invalid")
    groups: dict[str, list[ModelAttemptTrace]] = defaultdict(list)
    for trace in traces:
        groups[trace.logical_request_id].append(trace)
    retry_delay = 0.0
    for attempts in groups.values():
        ordered = sorted(attempts, key=lambda item: item.started_at)
        retry_delay += sum(
            _seconds(previous.finished_at, current.started_at)
            for previous, current in zip(ordered, ordered[1:], strict=False)
        )
    return ModelPerformance(
        logical_requests=len(groups),
        transport_attempts=len(traces),
        retry_delay_seconds=retry_delay,
    )


def build_taskbuilder_performance(
    attempts: list[ModelAttemptTrace],
    *,
    draft_id: str,
    total_seconds: float,
) -> TaskBuilderPerformance:
    """Summarize extraction without mixing it into a solve run."""

    groups: dict[str, list[ModelAttemptTrace]] = defaultdict(list)
    for attempt in attempts:
        groups[attempt.logical_request_id].append(attempt)
    retry_delay = 0.0
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item.started_at)
        retry_delay += sum(
            _seconds(previous.finished_at, current.started_at)
            for previous, current in zip(ordered, ordered[1:], strict=False)
        )
    return TaskBuilderPerformance(
        draft_id=draft_id,
        total_seconds=max(total_seconds, 0.0),
        logical_requests=len(groups),
        transport_attempts=len(attempts),
        retry_delay_seconds=retry_delay,
    )


def build_performance_summary(
    run_dir: str | Path,
    *,
    path_kind: PathKind,
    reuse: PerformanceReuse,
) -> PerformanceSummary:
    """Aggregate only observable evidence; never infer missing durations."""

    directory = Path(run_dir).resolve()
    diagnostics: list[str] = []
    events = _events(directory, diagnostics)
    task = _first(
        events,
        WorkflowStage.TASK_VALIDATED,
        WorkflowEventState.COMPLETED,
    )
    finalized = _last(events, WorkflowStage.RUN_FINALIZED)
    if finalized is None:
        diagnostics.append("RUN_FINALIZED event is missing")
    workflow_seconds = (
        _seconds(task.occurred_at, finalized.occurred_at)
        if task is not None and finalized is not None
        else None
    )
    first_native = _first(
        events,
        WorkflowStage.OPENFOAM_STEP_STARTED,
        WorkflowEventState.STARTED,
    )
    time_to_first = (
        _seconds(task.occurred_at, first_native.occurred_at)
        if task is not None and first_native is not None
        else None
    )

    environment = _single_interval(
        events,
        WorkflowStage.TASK_VALIDATED,
        WorkflowStage.ENVIRONMENT_READY,
    )
    geometry_event = _first(events, WorkflowStage.GEOMETRY_READY)
    geometry = (
        _single_interval(
            events,
            WorkflowStage.ENVIRONMENT_READY,
            WorkflowStage.GEOMETRY_READY,
        )
        if geometry_event is not None
        else 0.0
    )
    routing_start = (
        WorkflowStage.GEOMETRY_READY
        if geometry_event is not None
        else WorkflowStage.ENVIRONMENT_READY
    )
    routing = _single_interval(
        events,
        routing_start,
        WorkflowStage.ROUTING_READY,
    )
    context = _single_interval(
        events,
        WorkflowStage.ROUTING_READY,
        WorkflowStage.CONTEXT_READY,
    )
    generation_started = _first(
        events,
        WorkflowStage.MODEL_GENERATION_STARTED,
        WorkflowEventState.STARTED,
    )
    generation = (
        _single_interval(
            events,
            WorkflowStage.MODEL_GENERATION_STARTED,
            WorkflowStage.PLAN_READY,
            start_state=WorkflowEventState.STARTED,
        )
        if generation_started is not None
        else 0.0
    )
    native = _native_seconds(directory, diagnostics)
    stages = PerformanceStages(
        environment_seconds=environment,
        geometry_seconds=geometry,
        routing_seconds=routing,
        context_seconds=context,
        generation_seconds=generation,
        materialization_seconds=_materialization_seconds(events),
        inspection_seconds=_attempt_intervals(
            events,
            WorkflowStage.CASE_MATERIALIZED,
            WorkflowStage.STATIC_INSPECTION_COMPLETE,
        ),
        mesh_seconds=native["mesh"],
        initialization_seconds=native["initialize"],
        solver_seconds=native["solve"],
        postprocess_seconds=native["postprocess"],
        validation_seconds=_validation_seconds(events),
        repair_model_seconds=_repair_seconds(events),
    )
    return PerformanceSummary(
        path_kind=path_kind,
        workflow_seconds_before_manifest=workflow_seconds,
        time_to_first_openfoam_command_seconds=time_to_first,
        stages=stages,
        model=_model_performance(directory, diagnostics),
        reuse=reuse,
        diagnostics=list(dict.fromkeys(diagnostics)),
    )
