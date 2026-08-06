"""Lean state machine from public task to verified native OpenFOAM run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel
import yaml

from foampilot.artifacts import (
    ArtifactStore,
    AttemptSummary,
    NativeAgentOutcome,
    NativeAgentStatus,
    NativeStatus,
    RunSummary,
)
from foampilot.environment import (
    EnvironmentSnapshot,
    discover_environment,
)
from foampilot.inspection import InspectionReport, inspect_native_case
from foampilot.knowledge import load_knowledge_corpus
from foampilot.models import (
    BackendFailureKind,
    GatewayRequestError,
    JsonlModelTraceSink,
    LineageBudgetExhausted,
    ModelBudgetLedger,
    ModelGateway,
    ModelContextArtifact,
    ModelStage,
)
from foampilot.models.messages_zh import backend_error_payload_zh
from foampilot.plans import (
    ExecutionPlan,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.performance import (
    DerivedCache,
    PlanReuseError,
    PerformanceReuse,
    build_performance_summary,
    geometry_cache_key,
    load_verified_plan_source,
    mesh_cache_key,
    prepare_repair_reuse,
)
from foampilot.preprocessing import (
    GeometryFacts,
    GeometryProbeError,
    MeshQualityReport,
    build_mesh_quality_report,
    probe_geometry,
)
from foampilot.routing import (
    CapabilityProfile,
    RoutingError,
    route_capability,
)
from foampilot.runtime import (
    PlanRunResult,
    PlanRunner,
    ReusedStepResult,
    RuntimeConfig,
)
from foampilot.tasks import TaskSpec, stage_public_assets
from foampilot.validation.models import (
    PublicValidationCheck,
    PublicValidationReport,
)
from foampilot.validation.native import validate_native_run
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ParentRun,
    ResumeMetadata,
    ResumeCompatibilityError,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowState,
    WorkflowStore,
)
from foampilot.workflow.lineage import (
    ContinuationInput,
    build_resume_fingerprint,
    load_parent_plan,
    load_parent_task,
    prepare_continuation,
)

from .context import AgentContext, load_agent_context
from .generation import author_case_bundle, materialize_case
from .repair import (
    failure_fingerprint,
    request_repair_patch,
    should_stop_repair,
)
from .failure import (
    FailureClassificationError,
    classify_native_failure,
)
from .repair_patch import (
    RepairChangeSet,
    RepairPatchError,
    apply_repair_patch,
)
from .repair_scope import RepairScopeError, build_repair_scope
from .status import (
    AgentDecisionStage,
    AgentStatusError,
    build_agent_status_snapshot,
)


# Complex native cases can legitimately require more than five minutes of
# local Codex CLI authoring. Keep a finite bound, but leave enough headroom for
# one complete response instead of discarding it at the former 300 s limit.
GENERATION_REQUEST_TIMEOUT_SECONDS = 420
GENERATION_STAGE_DEADLINE_SECONDS = 480


def _run_result_seconds(run: PlanRunResult) -> float:
    return sum(
        max((step.finished_at - step.started_at).total_seconds(), 0.0)
        for step in run.steps
    )


def _recorded_execution_seconds(run_dir: Path) -> float:
    total = 0.0
    for path in sorted(run_dir.glob("attempt-*/run-result.json")):
        try:
            total += _run_result_seconds(
                PlanRunResult.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError):
            continue
    return total


def _lineage_logical_requests(run_dir: Path, store_root: Path) -> int:
    total = 0
    current: Path | None = run_dir
    seen: set[Path] = set()
    while current is not None and current not in seen:
        seen.add(current)
        model_path = current / "model-configuration.json"
        if model_path.is_file():
            try:
                total += int(
                    _read_json(model_path).get("logical_model_requests", 0)
                )
            except (OSError, ValueError, TypeError):
                pass
        continuation_path = current / "continuation.json"
        if not continuation_path.is_file():
            break
        try:
            payload = _read_json(continuation_path)
            parent = payload.get("parent_run")
            parent_id = (
                str(parent.get("run_id"))
                if isinstance(parent, dict)
                else ""
            )
        except (OSError, ValueError):
            break
        candidate = (store_root / parent_id).resolve()
        if not parent_id or not candidate.is_relative_to(store_root):
            break
        current = candidate
    return total


def _write_status_artifact(
    *,
    run_dir: Path,
    name: str,
    snapshot: BaseModel,
) -> ModelContextArtifact:
    path = run_dir / name
    _write_json(path, snapshot)
    return ModelContextArtifact(
        path=name,
        sha256=sha256(path.read_bytes()).hexdigest(),
    )


def _agent_status_failure(error: AgentStatusError) -> FailureRecord:
    return FailureRecord(
        domain=FailureDomain.WORKFLOW,
        code=error.code,
        detail=error.detail,
        message=error.message,
        recovery=error.recovery,
    )


def _json_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _json_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _json_payload(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be a mapping: {path}")
    return payload


def _performance_context(
    run_dir: Path,
) -> tuple[str, PerformanceReuse, list[str]]:
    path = run_dir / "performance-context.json"
    if not path.is_file():
        return "cold", PerformanceReuse(), []
    try:
        payload = _read_json(path)
        path_kind = str(payload.get("path_kind", "cold"))
        reuse_payload = payload.get("reuse", {})
        diagnostics_payload = payload.get("diagnostics", [])
        reuse = PerformanceReuse.model_validate(
            reuse_payload if isinstance(reuse_payload, dict) else {}
        )
        diagnostics = (
            [str(item) for item in diagnostics_payload]
            if isinstance(diagnostics_payload, list)
            else []
        )
        return path_kind, reuse, diagnostics
    except (OSError, ValueError):
        return (
            "cold",
            PerformanceReuse(),
            ["PERFORMANCE_EVIDENCE_INCOMPLETE: invalid performance context"],
        )


def _update_performance_context(
    run_dir: Path,
    *,
    path_kind: str | None = None,
    plan: str | None = None,
    geometry: str | None = None,
    mesh: str | None = None,
    repair_start_stage: str | None = None,
    diagnostic: str | None = None,
) -> None:
    current_kind, current_reuse, diagnostics = _performance_context(run_dir)
    updates: dict[str, object] = {}
    if plan is not None:
        updates["plan"] = plan
    if geometry is not None:
        updates["geometry"] = geometry
    if mesh is not None:
        updates["mesh"] = mesh
    if repair_start_stage is not None:
        updates["repair_start_stage"] = repair_start_stage
    reuse = current_reuse.model_copy(update=updates)
    if diagnostic is not None:
        diagnostics = list(dict.fromkeys([*diagnostics, diagnostic]))
    _write_json(
        run_dir / "performance-context.json",
        {
            "schema_version": 1,
            "path_kind": path_kind or current_kind,
            "reuse": reuse,
            "diagnostics": diagnostics,
        },
    )


_NATIVE_STATUSES: set[str] = {
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "MESH_QUALITY_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
    "PUBLIC_VALIDATION_PASS",
}


def _failure_record(
    *,
    status: str,
    message: str,
    attempts: list[AttemptSummary],
) -> FailureRecord | None:
    if status == "PUBLIC_VALIDATION_PASS":
        return None
    domains = {
        "REQUEST_INCOMPLETE": FailureDomain.TASK,
        "ROUTING_UNRESOLVED": FailureDomain.TASK,
        "BLOCKED_ENVIRONMENT": FailureDomain.ENVIRONMENT,
        "PLAN_INVALID": FailureDomain.PLAN,
        "GENERATION_INVALID": FailureDomain.PLAN,
        "PLAN_REUSE_REJECTED": FailureDomain.PLAN,
        "CASE_GENERATION_FAILED": FailureDomain.CASE,
        "STATIC_INSPECTION_FAILED": FailureDomain.INSPECTION,
        "MESH_FAILED": FailureDomain.MESH,
        "MESH_QUALITY_FAILED": FailureDomain.MESH,
        "INITIALIZATION_FAILED": FailureDomain.INITIALIZATION,
        "SOLVER_FAILED": FailureDomain.SOLVER,
        "POSTPROCESS_FAILED": FailureDomain.POSTPROCESS,
        "PUBLIC_VALIDATION_FAILED": FailureDomain.VALIDATION,
    }
    return FailureRecord(
        domain=domains.get(status, FailureDomain.LEGACY),
        code=status,
        step_id=(
            attempts[-1].failed_step_id if attempts else None
        ),
        detail=message,
    )


def _backend_blocker(error: GatewayRequestError) -> FailureRecord:
    payload = backend_error_payload_zh(error.failure)
    return FailureRecord(
        domain=FailureDomain.BACKEND,
        code=error.failure.kind.value,
        retryable=error.failure.retryable,
        detail=error.failure.detail,
        message=str(payload["message"]),
        recovery=str(payload["recovery"]),
    )


def _record_event(
    workflow: WorkflowStore,
    *,
    stage: WorkflowStage,
    state: WorkflowEventState,
    attempt: int | None = None,
    step_id: str | None = None,
    detail: str = "",
    evidence_paths: list[str] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    workflow.record(
        WorkflowEvent(
            sequence=workflow.next_sequence,
            stage=stage,
            state=state,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            attempt=attempt,
            step_id=step_id,
            detail=detail,
            evidence_paths=evidence_paths or [],
        )
    )


def _last_completed_stage(workflow: WorkflowStore) -> str | None:
    if not workflow.events_path.is_file():
        return None
    result: str | None = None
    for line in workflow.events_path.read_text(
        encoding="utf-8"
    ).splitlines():
        event = WorkflowEvent.model_validate_json(line)
        if event.state == WorkflowEventState.COMPLETED:
            result = event.stage.value
    return result


def _read_declared_files(
    case_root: Path,
    plan: ExecutionPlan,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for generated in plan.files:
        path = case_root / generated.path
        if path.is_file():
            values[generated.path] = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
    return values


def _run_log(run: PlanRunResult) -> str:
    parts: list[str] = []
    for step in run.steps:
        for path in (step.stdout_path, step.stderr_path):
            if path.is_file():
                parts.append(
                    path.read_text(encoding="utf-8", errors="replace")
                )
    return "\n".join(parts)


def _generation_trace(
    case_root: Path,
    plan: ExecutionPlan,
    *,
    attempt: int,
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for generated in plan.files:
        path = case_root / generated.path
        if not path.is_file():
            continue
        payload = path.read_bytes()
        files.append(
            {
                "path": generated.path,
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
            }
        )
    return {
        "schema_version": 2,
        "attempt": attempt,
        "source": (
            "single_model_case_bundle"
            if attempt == 1
            else "evidence_scoped_repair"
        ),
        "files": files,
    }


def _status_for_report(
    report: PublicValidationReport,
) -> NativeAgentStatus:
    if report.passed:
        return "PUBLIC_VALIDATION_PASS"
    if report.failure_layer == "ENVIRONMENT_BLOCKED":
        return "BLOCKED_ENVIRONMENT"
    if report.failure_layer is None:
        return "PUBLIC_VALIDATION_FAILED"
    return report.failure_layer


def _with_mesh_quality(
    report: PublicValidationReport,
    quality: MeshQualityReport,
) -> PublicValidationReport:
    check = PublicValidationCheck(
        name="mesh-quality-intent",
        passed=quality.passed,
        detail=(
            "Mesh observations satisfy the declared MeshIntent."
            if quality.passed
            else "Mesh observations do not satisfy the declared MeshIntent: "
            + ", ".join(quality.failed_requirements)
        ),
        observed={
            "check_mesh_passed": quality.check_mesh_passed,
            "cells": quality.cells,
            "max_non_orthogonality": quality.max_non_orthogonality,
            "max_skewness": quality.max_skewness,
            "failed_requirements": ",".join(quality.failed_requirements),
        },
        limits={},
    )
    failure_layer = report.failure_layer
    if (
        not quality.passed
        and failure_layer in {None, "PUBLIC_VALIDATION_FAILED"}
    ):
        failure_layer = "MESH_QUALITY_FAILED"
    return report.model_copy(
        update={
            "checks": [*report.checks, check],
            "failure_layer": failure_layer,
        }
    )


def _inspection_validation_report(
    inspection: InspectionReport,
) -> PublicValidationReport:
    return PublicValidationReport(
        checks=[
            PublicValidationCheck(
                name=f"static:{issue.code}",
                passed=False,
                detail=(
                    f"{issue.code} at {issue.path or '<case>'}: "
                    f"{issue.detail}"
                ),
                observed={
                    "code": issue.code,
                    "path": issue.path,
                },
            )
            for issue in inspection.issues
        ],
        failure_layer="STATIC_INSPECTION_FAILED",
    )


class NativeAgent:
    """Author once, execute safely, validate independently, and repair once."""

    def __init__(
        self,
        *,
        gateway: ModelGateway | None,
        runtime_config: RuntimeConfig,
        artifact_store: ArtifactStore,
        environment_snapshot: EnvironmentSnapshot | None = None,
        runner: PlanRunner | Any | None = None,
        knowledge_text: str | None = None,
        skills_text: str | None = None,
        workflow_event_listener: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.runtime_config = runtime_config
        self.artifact_store = artifact_store
        self.environment_snapshot = environment_snapshot
        self.runner = runner
        self.knowledge_text = knowledge_text
        self.skills_text = skills_text
        self.workflow_event_listener = workflow_event_listener

    def _finish(
        self,
        *,
        run_dir: Path,
        task: TaskSpec,
        status: str,
        attempts: list[AttemptSummary],
        message: str,
        model_calls: int,
        workflow_state: WorkflowState | None = None,
        primary_failure: FailureRecord | None = None,
        terminal_blocker: FailureRecord | None = None,
        resume: ResumeMetadata | None = None,
    ) -> NativeAgentOutcome:
        workflow = WorkflowStore(
            run_dir=run_dir,
            event_listener=self.workflow_event_listener,
        )
        active_workflow_state = workflow_state or (
            WorkflowState.COMPLETED
            if status == "PUBLIC_VALIDATION_PASS"
            else WorkflowState.FAILED
        )
        if primary_failure is None and (
            active_workflow_state != WorkflowState.DEFERRED
            or status in _NATIVE_STATUSES
        ):
            primary_failure = _failure_record(
                status=status,
                message=message,
                attempts=attempts,
            )
        native_status: NativeStatus | None = (
            status if status in _NATIVE_STATUSES else None
        )
        last_completed_stage = _last_completed_stage(workflow)
        final_event_state = {
            WorkflowState.COMPLETED: WorkflowEventState.COMPLETED,
            WorkflowState.FAILED: WorkflowEventState.FAILED,
            WorkflowState.DEFERRED: WorkflowEventState.DEFERRED,
        }[active_workflow_state]
        _record_event(
            workflow,
            stage=WorkflowStage.RUN_FINALIZED,
            state=final_event_state,
            detail=message,
        )
        if final_event_state == WorkflowEventState.COMPLETED:
            last_completed_stage = WorkflowStage.RUN_FINALIZED.value
        trace_path = run_dir / "model-attempts.jsonl"
        traces: list[dict[str, object]] = []
        if trace_path.is_file():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    traces.append(payload)
        generation_calls = 0
        repair_calls = 0
        if workflow.events_path.is_file():
            for line in workflow.events_path.read_text(
                encoding="utf-8"
            ).splitlines():
                event = WorkflowEvent.model_validate_json(line)
                if event.state != WorkflowEventState.STARTED:
                    continue
                if event.stage == WorkflowStage.MODEL_GENERATION_STARTED:
                    generation_calls += 1
                elif event.stage == WorkflowStage.MODEL_REPAIR_STARTED:
                    repair_calls += 1
        _write_json(
            run_dir / "model-configuration.json",
            {
                "schema_version": 3,
                "client_type": (
                    type(self.gateway).__name__
                    if self.gateway is not None
                    else None
                ),
                "backend_id": getattr(
                    self.gateway,
                    "primary_backend_id",
                    None,
                ),
                "model": getattr(self.gateway, "primary_model", None),
                "backend_policy_sha256": getattr(
                    self.gateway,
                    "policy_sha256",
                    None,
                ),
                "automatic_failover": bool(
                    getattr(self.gateway, "automatic_failover", False)
                ),
                "backend_ids_used": sorted(
                    {
                        str(item["backend_id"])
                        for item in traces
                        if item.get("backend_id") is not None
                    }
                ),
                "case_bundle_calls": generation_calls,
                "repair_calls": repair_calls,
                "total_model_calls": model_calls,
                "logical_model_requests": model_calls,
                "transport_attempts": len(traces),
                "model_time_seconds": sum(
                    float(item.get("elapsed_seconds", 0))
                    for item in traces
                ),
            },
        )
        parent_run: ParentRun | None = None
        continuation_path = run_dir / "continuation.json"
        if continuation_path.is_file():
            continuation_payload = _read_json(continuation_path)
            raw_parent = continuation_payload.get("parent_run")
            if isinstance(raw_parent, dict):
                parent_run = ParentRun.model_validate(raw_parent)
        summary = RunSummary(
            task_id=task.task_id,
            workflow_state=active_workflow_state,
            native_status=native_status,
            last_completed_stage=last_completed_stage,
            attempts=attempts,
            primary_failure=primary_failure,
            terminal_blocker=terminal_blocker,
            resume=resume
            or ResumeMetadata(
                allowed=False,
                reason="run has no retryable interrupted model stage",
            ),
            parent_run=parent_run,
            message=message,
        )
        workflow.finish(summary)
        path_kind, performance_reuse, performance_diagnostics = (
            _performance_context(run_dir)
        )
        performance = build_performance_summary(
            run_dir,
            path_kind=path_kind,
            reuse=performance_reuse,
        )
        if performance_diagnostics:
            performance = performance.model_copy(
                update={
                    "diagnostics": list(
                        dict.fromkeys(
                            [
                                *performance.diagnostics,
                                *performance_diagnostics,
                            ]
                        )
                    )
                }
            )
        _write_json(
            run_dir / "performance-summary.json",
            performance,
        )
        self.artifact_store.finalize(run_dir)
        return NativeAgentOutcome(
            run_dir=run_dir,
            summary=summary,
        )

    def _environment(self, run_dir: Path) -> EnvironmentSnapshot:
        if self.environment_snapshot is not None:
            return self.environment_snapshot
        return discover_environment(self.runtime_config, run_dir)

    def _context(
        self,
        task: TaskSpec,
        capability: CapabilityProfile,
        *,
        repair: bool = False,
        repair_evidence: str = "",
        geometry_facts: GeometryFacts | None = None,
    ) -> AgentContext:
        selected = load_agent_context(
            task,
            capability,
            repair=repair,
            repair_evidence=repair_evidence,
            geometry_facts=geometry_facts,
        )
        return selected.model_copy(
            update={
                "knowledge_text": (
                    selected.knowledge_text
                    if self.knowledge_text is None
                    else self.knowledge_text
                ),
                "skills_text": (
                    selected.skills_text
                    if self.skills_text is None
                    else self.skills_text
                ),
            }
        )

    @staticmethod
    def _parent_capability(parent_run: Path) -> CapabilityProfile:
        path = parent_run / "capability-profile.json"
        if not path.is_file():
            raise ResumeCompatibilityError("capability_profile")
        try:
            return CapabilityProfile.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise ResumeCompatibilityError(
                "capability_profile"
            ) from error

    def resume(
        self,
        parent_run: str | Path,
        *,
        public_asset_root: str | Path | None = None,
    ) -> NativeAgentOutcome:
        """Create an immutable child run from a retryable model stage."""

        if self.gateway is None:
            raise ValueError("strict resume requires a model gateway")

        parent = Path(parent_run).resolve()
        task = load_parent_task(parent)
        environment = self._environment(self.artifact_store.root)
        capability = self._parent_capability(parent)
        effective_asset_root = public_asset_root
        parent_assets = parent / "public-assets"
        if effective_asset_root is None and parent_assets.is_dir():
            effective_asset_root = parent_assets
        geometry_facts = probe_geometry(
            task,
            Path(effective_asset_root or parent),
        )
        context = self._context(
            task,
            capability,
            geometry_facts=geometry_facts,
        )
        current = build_resume_fingerprint(
            task=task,
            environment=environment,
            model=self.gateway.primary_model,
            backend_id=self.gateway.primary_backend_id,
            backend_policy_sha256=self.gateway.policy_sha256,
            knowledge_ids=context.selected_knowledge_ids,
            knowledge_text=context.knowledge_text,
            skill_ids=context.skill_names,
            skills_text=context.skills_text,
            public_asset_root=effective_asset_root,
        )
        continuation = prepare_continuation(
            parent_run=parent,
            artifact_store=self.artifact_store,
            current=current,
        )
        return self.solve(
            task,
            public_asset_root=effective_asset_root,
            _continuation=continuation,
        )

    def solve(
        self,
        task: TaskSpec,
        *,
        public_asset_root: str | Path | None = None,
        reuse_verified_plan: str | Path | None = None,
        derived_cache: str | Path | None = None,
        _continuation: ContinuationInput | None = None,
    ) -> NativeAgentOutcome:
        if reuse_verified_plan is not None and _continuation is not None:
            raise ValueError(
                "verified plan reuse and strict continuation are mutually exclusive"
            )
        run_dir = self.artifact_store.create_run()
        derived_cache_store = (
            DerivedCache(derived_cache)
            if derived_cache is not None
            else None
        )
        _write_json(
            run_dir / "performance-context.json",
            {
                "schema_version": 1,
                "path_kind": (
                    "warm_plan"
                    if reuse_verified_plan is not None
                    else "cold"
                ),
                "reuse": PerformanceReuse(
                    plan=(
                        "miss"
                        if reuse_verified_plan is not None
                        else "disabled"
                    ),
                    geometry=(
                        "miss"
                        if derived_cache_store is not None
                        and task.geometry is not None
                        else "disabled"
                    ),
                    mesh=(
                        "miss"
                        if derived_cache_store is not None
                        else "disabled"
                    ),
                ),
                "diagnostics": [],
            },
        )
        workflow = WorkflowStore(
            run_dir=run_dir,
            event_listener=self.workflow_event_listener,
        )
        attempts: list[AttemptSummary] = (
            list(_continuation.parent_summary.attempts)
            if _continuation is not None
            and _continuation.from_stage
            == WorkflowStage.MODEL_REPAIR_STARTED
            else []
        )
        model_calls = 0
        model_ledger = ModelBudgetLedger.start(
            total_model_deadline_seconds=600,
            lineage_transport_attempt_limit=7,
            transport_attempts_used=(
                _continuation.transport_attempts_used
                if _continuation is not None
                else 0
            ),
        )
        lineage_logical_requests_before_run = (
            _lineage_logical_requests(
                _continuation.parent_run,
                self.artifact_store.root.resolve(),
            )
            if _continuation is not None
            else 0
        )
        execution_seconds_used = (
            _recorded_execution_seconds(_continuation.parent_run)
            if _continuation is not None
            else 0.0
        )
        logical_request_limit = task.resource_budget.max_attempts + 4
        model_trace = JsonlModelTraceSink(
            run_dir / "model-attempts.jsonl"
        )
        (run_dir / "task.yaml").write_text(
            yaml.safe_dump(
                task.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        if _continuation is not None:
            _write_json(
                run_dir / "continuation.json",
                {
                    "schema_version": 1,
                    "parent_run": {
                        "run_id": _continuation.parent_run.name,
                        "manifest_sha256": (
                            _continuation.parent_manifest_sha256
                        ),
                    },
                    "from_stage": _continuation.from_stage.value,
                    "continuation_index_for_stage": (
                        _continuation.continuation_index_for_stage
                    ),
                    "transport_attempts_used_before_child": (
                        _continuation.transport_attempts_used
                    ),
                    "environment_warnings": (
                        _continuation.environment_warnings
                    ),
                },
            )
        _record_event(
            workflow,
            stage=WorkflowStage.TASK_VALIDATED,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["task.yaml"],
        )

        try:
            environment = self._environment(run_dir)
        except (OSError, RuntimeError) as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="BLOCKED_ENVIRONMENT",
                attempts=attempts,
                message=f"Environment discovery failed: {error}",
                model_calls=model_calls,
            )
        _write_json(run_dir / "environment.json", environment)
        if (
            environment.distribution != task.openfoam_target.distribution
            or environment.version != task.openfoam_target.version
            or not environment.workspace_writable
        ):
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="BLOCKED_ENVIRONMENT",
                attempts=attempts,
                message="The discovered runtime does not satisfy the task target.",
                model_calls=model_calls,
            )
        _record_event(
            workflow,
            stage=WorkflowStage.ENVIRONMENT_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["environment.json"],
        )

        effective_public_asset_root: str | Path | None = public_asset_root
        if task.public_assets:
            if public_asset_root is None:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message="public_asset_root is required for public assets",
                    model_calls=model_calls,
                )
            public_snapshot = run_dir / "public-assets"
            try:
                stage_public_assets(
                    task,
                    public_asset_root,
                    public_snapshot,
                )
            except (OSError, ValueError) as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=f"Public asset staging failed: {error}",
                    model_calls=model_calls,
                )
            effective_public_asset_root = public_snapshot

        geometry_facts: GeometryFacts | None = None
        if task.geometry is not None:
            try:
                geometry_key: str | None = None
                if derived_cache_store is not None:
                    geometry_key = geometry_cache_key(
                        task,
                        Path(effective_public_asset_root or run_dir),
                    )
                    geometry_lookup = derived_cache_store.load_geometry(
                        geometry_key
                    )
                    _write_json(
                        run_dir / "geometry-cache.json",
                        {
                            "schema_version": 1,
                            "status": geometry_lookup.status,
                            "cache_key": geometry_lookup.key,
                            "reason_code": geometry_lookup.reason_code,
                        },
                    )
                    if geometry_lookup.status == "hit":
                        geometry_facts = geometry_lookup.value
                        _update_performance_context(
                            run_dir,
                            geometry="hit",
                        )
                    elif geometry_lookup.reason_code == "DERIVED_CACHE_INVALID":
                        _update_performance_context(
                            run_dir,
                            geometry="miss",
                            diagnostic="DERIVED_CACHE_INVALID: geometry",
                        )
                if geometry_facts is None:
                    geometry_facts = probe_geometry(
                        task,
                        Path(effective_public_asset_root or run_dir),
                    )
                    if (
                        derived_cache_store is not None
                        and geometry_key is not None
                        and geometry_facts is not None
                    ):
                        derived_cache_store.store_geometry(
                            geometry_key,
                            geometry_facts,
                        )
            except GeometryProbeError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=f"公开几何探测失败: {error}",
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.TASK,
                        code=error.code,
                        detail=str(error),
                        message="公开几何输入无法安全、确定地解释。",
                    ),
                )
            assert geometry_facts is not None
            _write_json(run_dir / "geometry-facts.json", geometry_facts)
            _record_event(
                workflow,
                stage=WorkflowStage.GEOMETRY_READY,
                state=WorkflowEventState.COMPLETED,
                evidence_paths=["geometry-facts.json"],
            )

        verified_source = None
        if reuse_verified_plan is not None:
            try:
                verified_source = load_verified_plan_source(
                    reuse_verified_plan,
                    task=task,
                    environment=environment,
                    public_asset_root=effective_public_asset_root,
                )
            except PlanReuseError as error:
                _write_json(
                    run_dir / "plan-reuse.json",
                    {
                        "schema_version": 1,
                        "status": "rejected",
                        "source_run": str(Path(reuse_verified_plan).resolve()),
                        "reason_code": error.reason_code,
                        "detail": error.detail,
                    },
                )
                _update_performance_context(
                    run_dir,
                    path_kind="warm_plan",
                    plan="miss",
                    diagnostic=(
                        f"PLAN_REUSE_REJECTED: {error.reason_code}"
                    ),
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="PLAN_REUSE_REJECTED",
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.PLAN,
                        code="PLAN_REUSE_REJECTED",
                        detail=str(error),
                        message="显式复用的 ExecutionPlan 不满足严格兼容条件。",
                        recovery="请选择匹配的已验证 run，或不带复用参数重新生成。",
                    ),
                )
            _write_json(run_dir / "plan-reuse.json", verified_source.record())
            _update_performance_context(
                run_dir,
                path_kind="warm_plan",
                plan="hit",
            )

        try:
            if verified_source is not None:
                capability = verified_source.capability
            elif _continuation is not None:
                capability = self._parent_capability(
                    _continuation.parent_run
                )
            else:
                if self.gateway is None:
                    raise ValueError(
                        "live authoring requires a model gateway"
                    )
                knowledge_root = (
                    Path(__file__).resolve().parents[1]
                    / "knowledge/openfoam10"
                )
                capability = route_capability(
                    task,
                    environment,
                    load_knowledge_corpus(knowledge_root),
                    gateway=self.gateway,
                    budget=model_ledger.open_stage(
                        ModelStage.ROUTING,
                        request_timeout_seconds=60,
                        stage_deadline_seconds=60,
                        max_transport_attempts=1,
                    ),
                    trace=model_trace,
                )
        except RoutingError as error:
            if error.model_route_used:
                model_calls += 1
            _write_json(
                run_dir / "capability-profile.json",
                error.profile,
            )
            return self._finish(
                run_dir=run_dir,
                task=task,
                status=error.code,
                attempts=attempts,
                message=str(error),
                model_calls=model_calls,
            )
        except GatewayRequestError as error:
            model_calls += 1
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="ROUTING_UNRESOLVED",
                attempts=attempts,
                message=f"Capability routing is unavailable: {error}",
                model_calls=model_calls,
                workflow_state=WorkflowState.DEFERRED,
                terminal_blocker=_backend_blocker(error),
                resume=ResumeMetadata(
                    allowed=False,
                    reason=(
                        "routing has no frozen capability checkpoint; "
                        "rerun when the backend is available"
                    ),
                ),
            )
        except LineageBudgetExhausted as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="ROUTING_UNRESOLVED",
                attempts=attempts,
                message=str(error),
                model_calls=model_calls,
                primary_failure=FailureRecord(
                    domain=FailureDomain.BACKEND,
                    code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                    detail=str(error),
                ),
            )
        except (OSError, ValueError) as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="ROUTING_UNRESOLVED",
                attempts=attempts,
                message=f"Capability routing is invalid: {error}",
                model_calls=model_calls,
            )
        _write_json(
            run_dir / "capability-profile.json",
            capability,
        )
        _record_event(
            workflow,
            stage=WorkflowStage.ROUTING_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["capability-profile.json"],
        )

        try:
            context = self._context(
                task,
                capability,
                geometry_facts=geometry_facts,
            )
        except (OSError, ValueError) as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="PLAN_INVALID",
                attempts=attempts,
                message=f"Dynamic Agent context is invalid: {error}",
                model_calls=model_calls,
            )
        _write_json(
            run_dir / "agent-context.json",
            {
                "knowledge_slots": context.knowledge_slots,
                "missing_slots": context.missing_slots,
                "selected_knowledge_ids": context.selected_knowledge_ids,
                "selected_source_hashes": context.selected_source_hashes,
                "skill_names": context.skill_names,
            },
        )
        _record_event(
            workflow,
            stage=WorkflowStage.CONTEXT_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["agent-context.json"],
        )

        if self.gateway is not None:
            fingerprint = build_resume_fingerprint(
                task=task,
                environment=environment,
                model=self.gateway.primary_model,
                backend_id=self.gateway.primary_backend_id,
                backend_policy_sha256=self.gateway.policy_sha256,
                knowledge_ids=context.selected_knowledge_ids,
                knowledge_text=context.knowledge_text,
                skill_ids=context.skill_names,
                skills_text=context.skills_text,
                public_asset_root=effective_public_asset_root,
            )
            _write_json(
                run_dir / "resume-compatibility.json",
                fingerprint,
            )

        continuation_index = (
            _continuation.continuation_index_for_stage
            if _continuation is not None
            else 0
        )
        pending_repair_changes: RepairChangeSet | None = None
        pending_repair_source_attempt: Path | None = None

        if verified_source is not None:
            plan = verified_source.plan
        elif (
            _continuation is not None
            and _continuation.from_stage
            == WorkflowStage.MODEL_REPAIR_STARTED
        ):
            parent_plan = load_parent_plan(_continuation)
            assert _continuation.public_validation_path is not None
            assert _continuation.active_plan_path is not None
            report = PublicValidationReport.model_validate_json(
                _continuation.public_validation_path.read_text(
                    encoding="utf-8"
                )
            )
            parent_case = _continuation.active_plan_path.parent / "case"
            parent_mesh_quality_path = (
                _continuation.active_plan_path.parent
                / "mesh-quality-report.json"
            )
            parent_mesh_quality = (
                MeshQualityReport.model_validate_json(
                    parent_mesh_quality_path.read_text(encoding="utf-8")
                )
                if parent_mesh_quality_path.is_file()
                else None
            )
            current_files = _read_declared_files(
                parent_case,
                parent_plan,
            )
            log_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in _continuation.failed_log_paths
            )
            if not log_text:
                log_text = report.feedback()
            parent_attempt = attempts[-1].attempt
            try:
                parent_inspection_path = (
                    _continuation.active_plan_path.parent
                    / "static-inspection.json"
                )
                parent_inspection = (
                    InspectionReport.model_validate_json(
                        parent_inspection_path.read_text(encoding="utf-8")
                    )
                    if parent_inspection_path.is_file()
                    else None
                )
                classification = classify_native_failure(
                    report=report,
                    plan=parent_plan,
                    log_tail=log_text,
                    inspection=(
                        parent_inspection
                        if parent_inspection is not None
                        and not parent_inspection.passed
                        else None
                    ),
                    prior_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                )
                classification_name = (
                    f"failure-classification-attempt-{parent_attempt:02d}.json"
                )
                _write_json(run_dir / classification_name, classification)
                repair_context = self._context(
                    task,
                    capability,
                    repair=True,
                    repair_evidence=(
                        report.feedback() + "\n" + log_text
                    ),
                    geometry_facts=geometry_facts,
                )
                _write_json(
                    run_dir / "repair-agent-context.json",
                    {
                        "knowledge_slots": (
                            repair_context.knowledge_slots
                        ),
                        "missing_slots": repair_context.missing_slots,
                        "selected_knowledge_ids": (
                            repair_context.selected_knowledge_ids
                        ),
                        "selected_source_hashes": (
                            repair_context.selected_source_hashes
                        ),
                        "skill_names": repair_context.skill_names,
                    },
                )
                repair_scope = build_repair_scope(
                    classification=classification,
                    task=task,
                    plan=parent_plan,
                    current_files=current_files,
                    knowledge_ids=(
                        repair_context.selected_knowledge_ids
                    ),
                )
                scope_name = (
                    f"repair-scope-attempt-{parent_attempt:02d}.json"
                )
                _write_json(run_dir / scope_name, repair_scope)
                _record_event(
                    workflow,
                    stage=WorkflowStage.REPAIR_SCOPE_READY,
                    state=WorkflowEventState.COMPLETED,
                    attempt=parent_attempt,
                    evidence_paths=[classification_name, scope_name],
                )
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_REPAIR_STARTED,
                    state=WorkflowEventState.STARTED,
                    attempt=parent_attempt,
                )
                continuation_failure = FailureRecord(
                    domain=classification.domain,
                    code=classification.code,
                    step_id=classification.failed_step_id,
                    detail=report.feedback(),
                    evidence_paths=[classification_name, scope_name],
                )
                repair_status = build_agent_status_snapshot(
                    decision_stage=AgentDecisionStage.REPAIR,
                    task=task,
                    capability=capability,
                    context=repair_context,
                    workflow=workflow,
                    model_budget=model_ledger,
                    logical_requests_used=(
                        lineage_logical_requests_before_run + model_calls
                    ),
                    logical_request_limit=logical_request_limit,
                    current_attempt=parent_attempt,
                    execution_seconds_used=execution_seconds_used,
                    plan=parent_plan,
                    latest_failure=continuation_failure,
                    allowed_actions=list(
                        dict.fromkeys(
                            operation.replace("insert_command_before", "insert_command")
                            .replace("insert_command_after", "insert_command")
                            for operation in repair_scope.allowed_operations
                        )
                    ),
                )
                repair_status_artifact = _write_status_artifact(
                    run_dir=run_dir,
                    name=f"agent-status-repair-{parent_attempt:02d}.json",
                    snapshot=repair_status,
                )
                model_calls += 1
                patch = request_repair_patch(
                    task=task,
                    plan=parent_plan,
                    classification=classification,
                    repair_scope=repair_scope,
                    failed_log=log_text,
                    knowledge_text=repair_context.knowledge_text,
                    skills_text=repair_context.skills_text,
                    geometry_facts=geometry_facts,
                    mesh_quality_report=parent_mesh_quality,
                    status_snapshot=repair_status,
                    status_artifact=repair_status_artifact,
                    gateway=self.gateway,
                    budget=model_ledger.open_stage(
                        ModelStage.REPAIR,
                        request_timeout_seconds=300,
                        stage_deadline_seconds=240,
                        max_transport_attempts=3,
                    ),
                    trace=model_trace,
                )
                patch_result = apply_repair_patch(
                    patch,
                    scope=repair_scope,
                    task=task,
                    plan=parent_plan,
                    available_executables=(
                        environment.available_executable_names
                    ),
                    current_files=current_files,
                )
            except (
                FailureClassificationError,
                RepairScopeError,
                RepairPatchError,
            ) as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.WORKFLOW,
                        code=error.code,
                        detail=error.detail,
                        message=error.message,
                        recovery=error.recovery,
                    ),
                )
            except AgentStatusError as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_agent_status_failure(error),
                )
            except GatewayRequestError as error:
                can_resume = (
                    error.failure.retryable
                    and continuation_index < 2
                    and model_ledger.transport_attempts_used < 7
                )
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                    workflow_state=WorkflowState.DEFERRED,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                    terminal_blocker=_backend_blocker(error),
                    resume=ResumeMetadata(
                        allowed=can_resume,
                        from_stage=(
                            WorkflowStage.MODEL_REPAIR_STARTED
                            if can_resume
                            else None
                        ),
                        reason=(
                            "retryable backend failure during repair"
                            if can_resume
                            else "continuation or transport budget exhausted"
                        ),
                    ),
                )
            except LineageBudgetExhausted as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.BACKEND,
                        code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                        detail=str(error),
                    ),
                )
            except Exception as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair proposal failed: {error}",
                    model_calls=model_calls,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                )
            patch_name = f"repair-patch-attempt-{parent_attempt:02d}.json"
            _write_json(run_dir / patch_name, patch)
            _write_json(
                run_dir
                / f"repair-patch-normalization-attempt-{parent_attempt:02d}.json",
                patch_result.normalizations,
            )
            plan = patch_result.plan
            pending_repair_changes = patch_result.changes
            pending_repair_source_attempt = (
                _continuation.active_plan_path.parent
            )
            _record_event(
                workflow,
                stage=WorkflowStage.REPAIR_APPLIED,
                state=WorkflowEventState.COMPLETED,
                attempt=parent_attempt,
                evidence_paths=[patch_name],
            )
        else:
            try:
                if self.gateway is None:
                    raise ValueError(
                        "live authoring requires a model gateway"
                    )
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_GENERATION_STARTED,
                    state=WorkflowEventState.STARTED,
                )
                author_status = build_agent_status_snapshot(
                    decision_stage=AgentDecisionStage.AUTHOR,
                    task=task,
                    capability=capability,
                    context=context,
                    workflow=workflow,
                    model_budget=model_ledger,
                    logical_requests_used=(
                        lineage_logical_requests_before_run + model_calls
                    ),
                    logical_request_limit=logical_request_limit,
                    current_attempt=1,
                    execution_seconds_used=execution_seconds_used,
                )
                author_status_artifact = _write_status_artifact(
                    run_dir=run_dir,
                    name="agent-status-author-01.json",
                    snapshot=author_status,
                )
                model_calls += 1
                plan = author_case_bundle(
                    task,
                    environment,
                    capability,
                    self.gateway,
                    context.knowledge_text,
                    context.skills_text,
                    geometry_facts=geometry_facts,
                    status_snapshot=author_status,
                    status_artifact=author_status_artifact,
                    budget=model_ledger.open_stage(
                        ModelStage.GENERATION,
                        request_timeout_seconds=(
                            GENERATION_REQUEST_TIMEOUT_SECONDS
                        ),
                        stage_deadline_seconds=(
                            GENERATION_STAGE_DEADLINE_SECONDS
                        ),
                        max_transport_attempts=3,
                    ),
                    trace=model_trace,
                )
            except AgentStatusError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_agent_status_failure(error),
                )
            except GatewayRequestError as error:
                if error.failure.kind == BackendFailureKind.SCHEMA_INVALID:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="GENERATION_INVALID",
                        attempts=attempts,
                        message=(
                            "模型输出未能形成有效的 ExecutionPlan："
                            f"{error.failure.detail}"
                        ),
                        model_calls=model_calls,
                        workflow_state=WorkflowState.FAILED,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.PLAN,
                            code="GENERATION_INVALID",
                            detail=error.failure.detail,
                            message="模型输出的执行计划结构无效。",
                            recovery=(
                                "调整生成上下文或模型后重新生成；"
                                "该错误不是模型服务不可用。"
                            ),
                        ),
                        resume=ResumeMetadata(
                            allowed=False,
                            reason=(
                                "invalid generated plan is not a "
                                "transport continuation"
                            ),
                        ),
                    )
                can_resume = (
                    error.failure.retryable
                    and continuation_index < 2
                    and model_ledger.transport_attempts_used < 7
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="DEFERRED",
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                    workflow_state=WorkflowState.DEFERRED,
                    terminal_blocker=_backend_blocker(error),
                    resume=ResumeMetadata(
                        allowed=can_resume,
                        from_stage=(
                            WorkflowStage.MODEL_GENERATION_STARTED
                            if can_resume
                            else None
                        ),
                        reason=(
                            "retryable backend failure during generation"
                            if can_resume
                            else "continuation or transport budget exhausted"
                        ),
                    ),
                )
            except LineageBudgetExhausted as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.BACKEND,
                        code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                        detail=str(error),
                    ),
                )
            except Exception as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=f"Case-bundle authoring failed: {error}",
                    model_calls=model_calls,
                )
        if verified_source is None:
            _write_json(run_dir / "authored-execution-plan.json", plan)
        normalization = normalize_execution_plan(
            plan,
            task,
            environment.available_executable_names,
        )
        _write_json(
            run_dir / "plan-normalization.json",
            [
                *normalization.records,
                *normalization.stage_records,
            ],
        )
        plan = normalization.plan
        next_attempt = len(attempts) + 1
        _write_json(run_dir / "execution-plan.json", plan)
        active_checkpoint = workflow.checkpoint(
            f"active-plan-attempt-{next_attempt:02d}",
            plan,
        )
        _record_event(
            workflow,
            stage=WorkflowStage.PLAN_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=[
                "execution-plan.json",
                active_checkpoint.relative_to(run_dir).as_posix(),
            ],
        )

        plan_issues = validate_execution_plan(
            plan,
            task,
            environment.available_executable_names,
        )
        if plan_issues:
            _write_json(run_dir / "plan-issues.json", plan_issues)
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="PLAN_INVALID",
                attempts=attempts,
                message="The model-authored bundle violates safety policy.",
                model_calls=model_calls,
            )

        active_plan = plan
        fingerprints: list[str] = [
            item.failure_fingerprint
            for item in attempts
            if item.failure_fingerprint is not None
        ]
        for attempt_number in range(
            next_attempt,
            task.resource_budget.max_attempts + 1,
        ):
            attempt_root = run_dir / f"attempt-{attempt_number:02d}"
            case_root = attempt_root / "case"
            case_root.mkdir(parents=True)
            _write_json(attempt_root / "execution-plan.json", active_plan)
            repair_preparation = None

            try:
                if task.public_assets:
                    assert effective_public_asset_root is not None
                    stage_public_assets(
                        task,
                        effective_public_asset_root,
                        case_root,
                    )
                materialize_case(active_plan, task, case_root)
                if (
                    pending_repair_changes is not None
                    and pending_repair_source_attempt is not None
                ):
                    repair_preparation = prepare_repair_reuse(
                        parent_attempt=pending_repair_source_attempt,
                        next_case_root=case_root,
                        plan=active_plan,
                        changes=pending_repair_changes,
                    )
                    _write_json(
                        attempt_root / "execution-reuse.json",
                        repair_preparation.record,
                    )
                    if repair_preparation.record.get("applied") is True:
                        _update_performance_context(
                            run_dir,
                            path_kind="repair_reuse",
                            repair_start_stage=(
                                repair_preparation.decision.earliest_rerun_stage
                            ),
                        )
                    else:
                        _update_performance_context(
                            run_dir,
                            repair_start_stage="mesh",
                            diagnostic=(
                                "REPAIR_REUSE_UNSAFE: "
                                + ",".join(
                                    repair_preparation.decision.reason_codes
                                )
                            ),
                        )
                    pending_repair_changes = None
                    pending_repair_source_attempt = None
            except Exception as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=f"Case materialization failed: {error}",
                    model_calls=model_calls,
                )
            _record_event(
                workflow,
                stage=WorkflowStage.CASE_MATERIALIZED,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    f"attempt-{attempt_number:02d}/case",
                ],
            )
            _write_json(
                attempt_root / "generation-trace.json",
                _generation_trace(
                    case_root,
                    active_plan,
                    attempt=attempt_number,
                ),
            )

            inspection = inspect_native_case(
                case_root=case_root,
                task=task,
                plan=active_plan,
                available_executables=(
                    environment.available_executable_names
                ),
            )
            _write_json(
                attempt_root / "static-inspection.json",
                inspection,
            )
            _record_event(
                workflow,
                stage=WorkflowStage.STATIC_INSPECTION_COMPLETE,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    (
                        f"attempt-{attempt_number:02d}/"
                        "static-inspection.json"
                    )
                ],
            )
            mesh_quality: MeshQualityReport | None = None
            if inspection.passed:
                commands_to_execute = (
                    list(repair_preparation.commands_to_execute)
                    if repair_preparation is not None
                    else list(active_plan.commands)
                )
                reused_steps: list[ReusedStepResult] = (
                    list(repair_preparation.reused_steps)
                    if repair_preparation is not None
                    else []
                )
                mesh_key_result = None
                mesh_lookup = None
                if (
                    derived_cache_store is not None
                    and attempt_number == next_attempt
                    and repair_preparation is None
                ):
                    mesh_key_result = mesh_cache_key(
                        task,
                        geometry_facts=geometry_facts,
                        plan=active_plan,
                        environment=environment,
                        public_asset_root=Path(
                            effective_public_asset_root or run_dir
                        ),
                    )
                    check_mesh_present = any(
                        command.stage == "check"
                        and command.executable == "checkMesh"
                        for command in active_plan.commands
                    )
                    if not mesh_key_result.cacheable:
                        _write_json(
                            attempt_root / "mesh-cache.json",
                            {
                                "schema_version": 1,
                                "status": "miss",
                                "cache_key": None,
                                "reason_code": mesh_key_result.reason_code,
                            },
                        )
                        _update_performance_context(
                            run_dir,
                            mesh="miss",
                            diagnostic=(
                                f"DERIVED_CACHE_MISS: "
                                f"{mesh_key_result.reason_code}"
                            ),
                        )
                    elif not check_mesh_present:
                        _write_json(
                            attempt_root / "mesh-cache.json",
                            {
                                "schema_version": 1,
                                "status": "miss",
                                "cache_key": mesh_key_result.key,
                                "reason_code": (
                                    "MESH_REUSE_REQUIRES_CHECKMESH"
                                ),
                            },
                        )
                        _update_performance_context(
                            run_dir,
                            mesh="miss",
                            diagnostic=(
                                "DERIVED_CACHE_MISS: "
                                "MESH_REUSE_REQUIRES_CHECKMESH"
                            ),
                        )
                    else:
                        assert mesh_key_result.key is not None
                        mesh_lookup = derived_cache_store.restore_mesh(
                            mesh_key_result.key,
                            case_root=case_root,
                        )
                        _write_json(
                            attempt_root / "mesh-cache.json",
                            {
                                "schema_version": 1,
                                "status": mesh_lookup.status,
                                "cache_key": mesh_lookup.key,
                                "reason_code": mesh_lookup.reason_code,
                                "source": mesh_lookup.value,
                            },
                        )
                        if mesh_lookup.status == "hit":
                            mesh_commands = [
                                command
                                for command in active_plan.commands
                                if command.stage == "mesh"
                            ]
                            commands_to_execute = [
                                command
                                for command in active_plan.commands
                                if command.stage != "mesh"
                            ]
                            source = mesh_lookup.value or {}
                            source_id = (
                                f"{source.get('source_run_id', 'cache')}:"
                                f"attempt-{int(source.get('source_attempt', 0)):02d}"
                            )
                            reused_steps = [
                                ReusedStepResult(
                                    step_id=command.step_id,
                                    stage=command.stage.value,
                                    executable=command.executable,
                                    source_kind="derived_cache",
                                    source_id=source_id,
                                    reason_codes=[
                                        "EXACT_MESH_CACHE_KEY_MATCH"
                                    ],
                                )
                                for command in mesh_commands
                            ]
                            _write_json(
                                attempt_root / "execution-reuse.json",
                                {
                                    "schema_version": 1,
                                    "source_kind": "derived_cache",
                                    "source_hash": mesh_lookup.key,
                                    "reused_step_ids": [
                                        item.step_id for item in reused_steps
                                    ],
                                    "commands_to_execute": [
                                        item.step_id
                                        for item in commands_to_execute
                                    ],
                                    "reason_codes": [
                                        "EXACT_MESH_CACHE_KEY_MATCH"
                                    ],
                                },
                            )
                            _update_performance_context(
                                run_dir,
                                path_kind="warm_mesh",
                                mesh="hit",
                            )
                        elif (
                            mesh_lookup.reason_code
                            == "DERIVED_CACHE_INVALID"
                        ):
                            _update_performance_context(
                                run_dir,
                                mesh="miss",
                                diagnostic="DERIVED_CACHE_INVALID: mesh",
                            )
                runner = self.runner
                if runner is None:
                    runner = PlanRunner.from_runtime_config(
                        self.runtime_config,
                        environment.available_executable_names,
                        workspace_root=run_dir,
                    )
                run_result = runner.run(
                    case_dir=case_root,
                    commands=commands_to_execute,
                    budget=task.resource_budget,
                )
                execution_seconds_used += _run_result_seconds(run_result)
                if reused_steps:
                    run_result = run_result.model_copy(
                        update={"reused_steps": reused_steps}
                    )
                _write_json(attempt_root / "run-result.json", run_result)
                for step in run_result.steps:
                    _record_event(
                        workflow,
                        stage=WorkflowStage.OPENFOAM_STEP_STARTED,
                        state=WorkflowEventState.STARTED,
                        attempt=attempt_number,
                        step_id=step.step_id,
                        detail="typed OpenFOAM command started",
                        occurred_at=step.started_at,
                    )
                    _record_event(
                        workflow,
                        stage=WorkflowStage.OPENFOAM_STEP_COMPLETE,
                        state=WorkflowEventState.COMPLETED,
                        attempt=attempt_number,
                        step_id=step.step_id,
                        detail=(
                            f"return_code={step.return_code}; "
                            f"timed_out={step.timed_out}"
                        ),
                        evidence_paths=[
                            step.stdout_path.relative_to(
                                run_dir
                            ).as_posix(),
                            step.stderr_path.relative_to(
                                run_dir
                            ).as_posix(),
                        ],
                        occurred_at=step.finished_at,
                    )
                mesh_quality = build_mesh_quality_report(
                    run_result,
                    task.mesh,
                )
                _write_json(
                    attempt_root / "mesh-quality-report.json",
                    mesh_quality,
                )
                if (
                    derived_cache_store is not None
                    and mesh_key_result is not None
                    and mesh_key_result.cacheable
                    and mesh_key_result.key is not None
                    and (
                        mesh_lookup is None
                        or mesh_lookup.status != "hit"
                    )
                ):
                    stored = derived_cache_store.store_mesh(
                        mesh_key_result.key,
                        case_root=case_root,
                        mesh_quality=mesh_quality,
                        plan=active_plan,
                        source_run_id=run_dir.name,
                        source_attempt=attempt_number,
                    )
                    if stored:
                        cache_record_path = attempt_root / "mesh-cache.json"
                        cache_record = (
                            _read_json(cache_record_path)
                            if cache_record_path.is_file()
                            else {"schema_version": 1}
                        )
                        cache_record["stored"] = True
                        _write_json(cache_record_path, cache_record)
                _record_event(
                    workflow,
                    stage=WorkflowStage.MESH_QUALITY_COMPLETE,
                    state=WorkflowEventState.COMPLETED,
                    attempt=attempt_number,
                    evidence_paths=[
                        (
                            f"attempt-{attempt_number:02d}/"
                            "mesh-quality-report.json"
                        )
                    ],
                )
                report = validate_native_run(
                    task=task,
                    run_result=run_result,
                    case_root=case_root,
                )
                if task.mesh is not None:
                    report = _with_mesh_quality(report, mesh_quality)
                log_text = _run_log(run_result)
            else:
                report = _inspection_validation_report(inspection)
                log_text = "\n".join(
                    check.detail for check in report.checks
                )
            _write_json(
                attempt_root / "public-validation.json",
                report,
            )
            validation_checkpoint = workflow.checkpoint(
                f"public-validation-attempt-{attempt_number:02d}",
                report,
            )
            _record_event(
                workflow,
                stage=WorkflowStage.PUBLIC_VALIDATION_COMPLETE,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    (
                        f"attempt-{attempt_number:02d}/"
                        "public-validation.json"
                    ),
                    validation_checkpoint.relative_to(
                        run_dir
                    ).as_posix(),
                ],
            )
            status = _status_for_report(report)
            if report.passed:
                attempts.append(
                    AttemptSummary(
                        attempt=attempt_number,
                        status=status,
                    )
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message="All evaluator-owned public checks pass.",
                    model_calls=model_calls,
                )

            fingerprint = failure_fingerprint(
                report,
                log_tail=log_text,
            )
            assert isinstance(fingerprint, str)
            fingerprints.append(fingerprint)
            attempt_summary = AttemptSummary(
                attempt=attempt_number,
                status=status,
                failed_step_id=report.failed_step_id,
                failure_fingerprint=fingerprint,
            )
            attempts.append(attempt_summary)

            try:
                classification = classify_native_failure(
                    report=report,
                    plan=active_plan,
                    log_tail=log_text,
                    inspection=(inspection if not inspection.passed else None),
                )
            except FailureClassificationError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_failure_record(
                        status=status,
                        message=report.feedback(),
                        attempts=attempts,
                    ),
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.WORKFLOW,
                        code=error.code,
                        detail=error.detail,
                        message=error.message,
                        recovery=error.recovery,
                    ),
                )
            classification_name = (
                f"failure-classification-attempt-{attempt_number:02d}.json"
            )
            _write_json(run_dir / classification_name, classification)
            classified_failure = FailureRecord(
                domain=classification.domain,
                code=classification.code,
                step_id=classification.failed_step_id,
                detail=report.feedback(),
                evidence_paths=[classification_name],
            )

            stop = should_stop_repair(
                fingerprints=fingerprints,
                attempts_used=attempt_number,
                max_attempts=task.resource_budget.max_attempts,
                generated_bytes_changed=True,
                environment_failure=(status == "BLOCKED_ENVIRONMENT"),
            )
            if stop.stop:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair stopped: {stop.reason}.",
                    model_calls=model_calls,
                )

            if verified_source is not None:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=(
                        "Verified-plan execution failed; warm plan reuse "
                        "does not invoke a repair model."
                    ),
                    model_calls=model_calls,
                )

            current_files = _read_declared_files(case_root, active_plan)
            try:
                if self.gateway is None:
                    raise ValueError("repair requires a model gateway")
                repair_context = self._context(
                    task,
                    capability,
                    repair=True,
                    repair_evidence=(
                        report.feedback() + "\n" + log_text
                    ),
                    geometry_facts=geometry_facts,
                )
                _write_json(
                    attempt_root / "repair-agent-context.json",
                    {
                        "knowledge_slots": (
                            repair_context.knowledge_slots
                        ),
                        "missing_slots": repair_context.missing_slots,
                        "selected_knowledge_ids": (
                            repair_context.selected_knowledge_ids
                        ),
                        "selected_source_hashes": (
                            repair_context.selected_source_hashes
                        ),
                        "skill_names": repair_context.skill_names,
                    },
                )
                repair_scope = build_repair_scope(
                    classification=classification,
                    task=task,
                    plan=active_plan,
                    current_files=current_files,
                    knowledge_ids=(
                        repair_context.selected_knowledge_ids
                    ),
                )
                scope_name = (
                    f"repair-scope-attempt-{attempt_number:02d}.json"
                )
                _write_json(run_dir / scope_name, repair_scope)
                _record_event(
                    workflow,
                    stage=WorkflowStage.REPAIR_SCOPE_READY,
                    state=WorkflowEventState.COMPLETED,
                    attempt=attempt_number,
                    evidence_paths=[classification_name, scope_name],
                )
                workflow.checkpoint(
                    f"repair-evidence-attempt-{attempt_number:02d}",
                    {
                        "failed_step_id": report.failed_step_id,
                        "failure_fingerprint": fingerprint,
                        "public_validation_path": (
                            f"attempt-{attempt_number:02d}/"
                            "public-validation.json"
                        ),
                        "log_paths": [
                            path.relative_to(run_dir).as_posix()
                            for step in (
                                run_result.steps
                                if inspection.passed
                                else []
                            )
                            for path in (
                                step.stdout_path,
                                step.stderr_path,
                            )
                        ],
                    },
                )
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_REPAIR_STARTED,
                    state=WorkflowEventState.STARTED,
                    attempt=attempt_number,
                )
                repair_status = build_agent_status_snapshot(
                    decision_stage=AgentDecisionStage.REPAIR,
                    task=task,
                    capability=capability,
                    context=repair_context,
                    workflow=workflow,
                    model_budget=model_ledger,
                    logical_requests_used=(
                        lineage_logical_requests_before_run + model_calls
                    ),
                    logical_request_limit=logical_request_limit,
                    current_attempt=attempt_number,
                    execution_seconds_used=execution_seconds_used,
                    plan=active_plan,
                    latest_failure=classified_failure,
                    allowed_actions=list(
                        dict.fromkeys(
                            operation.replace("insert_command_before", "insert_command")
                            .replace("insert_command_after", "insert_command")
                            for operation in repair_scope.allowed_operations
                        )
                    ),
                )
                repair_status_artifact = _write_status_artifact(
                    run_dir=run_dir,
                    name=f"agent-status-repair-{attempt_number:02d}.json",
                    snapshot=repair_status,
                )
                model_calls += 1
                patch = request_repair_patch(
                    task=task,
                    plan=active_plan,
                    classification=classification,
                    repair_scope=repair_scope,
                    failed_log=log_text,
                    knowledge_text=repair_context.knowledge_text,
                    skills_text=repair_context.skills_text,
                    geometry_facts=geometry_facts,
                    mesh_quality_report=mesh_quality,
                    status_snapshot=repair_status,
                    status_artifact=repair_status_artifact,
                    gateway=self.gateway,
                    budget=model_ledger.open_stage(
                        ModelStage.REPAIR,
                        request_timeout_seconds=300,
                        stage_deadline_seconds=240,
                        max_transport_attempts=3,
                    ),
                    trace=model_trace,
                )
                patch_result = apply_repair_patch(
                    patch,
                    scope=repair_scope,
                    task=task,
                    plan=active_plan,
                    available_executables=(
                        environment.available_executable_names
                    ),
                    current_files=current_files,
                )
            except (RepairScopeError, RepairPatchError) as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=classified_failure,
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.WORKFLOW,
                        code=error.code,
                        detail=error.detail,
                        message=error.message,
                        recovery=error.recovery,
                    ),
                )
            except AgentStatusError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_agent_status_failure(error),
                )
            except GatewayRequestError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                    workflow_state=WorkflowState.DEFERRED,
                    primary_failure=classified_failure,
                    terminal_blocker=_backend_blocker(error),
                    resume=ResumeMetadata(
                        allowed=error.failure.retryable,
                        from_stage=(
                            WorkflowStage.MODEL_REPAIR_STARTED
                            if error.failure.retryable
                            else None
                        ),
                        reason=(
                            "retryable backend failure during repair"
                            if error.failure.retryable
                            else "backend failure is not retryable"
                        ),
                    ),
                )
            except LineageBudgetExhausted as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=classified_failure,
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.BACKEND,
                        code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                        detail=str(error),
                    ),
                )
            except Exception as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair proposal failed: {error}",
                    model_calls=model_calls,
                )
            patch_name = f"repair-patch-attempt-{attempt_number:02d}.json"
            _write_json(run_dir / patch_name, patch)
            _write_json(
                run_dir
                / f"repair-patch-normalization-attempt-{attempt_number:02d}.json",
                patch_result.normalizations,
            )
            attempt_summary.changed_files = (
                patch_result.changes.changed_file_paths
            )
            active_plan = patch_result.plan
            pending_repair_changes = patch_result.changes
            pending_repair_source_attempt = attempt_root
            workflow.checkpoint(
                f"active-plan-attempt-{attempt_number + 1:02d}",
                active_plan,
            )
            _record_event(
                workflow,
                stage=WorkflowStage.REPAIR_APPLIED,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    patch_name,
                    (
                        "checkpoints/active-plan-attempt-"
                        f"{attempt_number + 1:02d}.json"
                    ),
                ],
            )

        raise AssertionError("attempt loop exhausted without terminal result")
