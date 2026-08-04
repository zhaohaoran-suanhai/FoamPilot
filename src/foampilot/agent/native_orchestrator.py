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
    GatewayRequestError,
    JsonlModelTraceSink,
    LineageBudgetExhausted,
    ModelBudgetLedger,
    ModelGateway,
    ModelStage,
)
from foampilot.models.messages_zh import backend_error_payload_zh
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.routing import (
    CapabilityProfile,
    RoutingError,
    route_capability,
)
from foampilot.runtime import (
    PlanRunResult,
    PlanRunner,
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
    RepairDecision,
    failure_fingerprint,
    request_repair,
    should_stop_repair,
    validate_repair_decision,
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


_NATIVE_STATUSES: set[str] = {
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
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
        "CASE_GENERATION_FAILED": FailureDomain.CASE,
        "STATIC_INSPECTION_FAILED": FailureDomain.INSPECTION,
        "MESH_FAILED": FailureDomain.MESH,
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


def _apply_repair(
    plan: ExecutionPlan,
    decision: RepairDecision,
) -> ExecutionPlan:
    files = {item.path: item for item in plan.files}
    added_paths: list[str] = []
    for changed in decision.changed_files:
        if changed.path not in files:
            added_paths.append(changed.path)
        files[changed.path] = changed
    commands = {item.step_id: item for item in plan.commands}
    for changed in decision.changed_commands:
        commands[changed.step_id] = changed
    return plan.model_copy(
        update={
            "files": [files[item.path] for item in plan.files]
            + [files[path] for path in added_paths],
            "commands": [commands[item.step_id] for item in plan.commands],
        }
    )


class NativeAgent:
    """Author once, execute safely, validate independently, and repair once."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
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
                "client_type": type(self.gateway).__name__,
                "backend_id": getattr(
                    self.gateway,
                    "primary_backend_id",
                    type(self.gateway).__name__,
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
    ) -> AgentContext:
        selected = load_agent_context(
            task,
            capability,
            repair=repair,
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

        parent = Path(parent_run).resolve()
        task = load_parent_task(parent)
        environment = self._environment(self.artifact_store.root)
        capability = self._parent_capability(parent)
        context = self._context(task, capability)
        effective_asset_root = public_asset_root
        parent_assets = parent / "public-assets"
        if effective_asset_root is None and parent_assets.is_dir():
            effective_asset_root = parent_assets
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
        _continuation: ContinuationInput | None = None,
    ) -> NativeAgentOutcome:
        run_dir = self.artifact_store.create_run()
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

        try:
            if _continuation is not None:
                capability = self._parent_capability(
                    _continuation.parent_run
                )
            else:
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
            context = self._context(task, capability)
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

        if (
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
                repair_context = self._context(
                    task,
                    capability,
                    repair=True,
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
                model_calls += 1
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_REPAIR_STARTED,
                    state=WorkflowEventState.STARTED,
                    attempt=parent_attempt,
                )
                decision = request_repair(
                    task=task,
                    plan=parent_plan,
                    report=report,
                    failed_log=log_text,
                    current_files=current_files,
                    knowledge_text=repair_context.knowledge_text,
                    skills_text=repair_context.skills_text,
                    gateway=self.gateway,
                    budget=model_ledger.open_stage(
                        ModelStage.REPAIR,
                        request_timeout_seconds=300,
                        stage_deadline_seconds=240,
                        max_transport_attempts=3,
                    ),
                    trace=model_trace,
                )
                issues = validate_repair_decision(
                    decision,
                    task=task,
                    plan=parent_plan,
                    available_executables=environment.executable_names,
                    current_files=current_files,
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
            _write_json(
                run_dir / "continuation-repair-decision.json",
                decision,
            )
            if issues:
                _write_json(
                    run_dir / "continuation-repair-issues.json",
                    issues,
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
                    message="Repair proposal violates safety policy.",
                    model_calls=model_calls,
                )
            changed = bool(
                decision.changed_commands
                or any(
                    current_files.get(item.path) != item.content
                    for item in decision.changed_files
                )
            )
            decision_stop = should_stop_repair(
                fingerprints=[
                    item.failure_fingerprint
                    for item in attempts
                    if item.failure_fingerprint is not None
                ],
                attempts_used=parent_attempt,
                max_attempts=task.resource_budget.max_attempts,
                generated_bytes_changed=changed,
                decision=decision,
            )
            if decision_stop.stop:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair stopped: {decision_stop.reason}.",
                    model_calls=model_calls,
                )
            repaired = _apply_repair(parent_plan, decision)
            repair_normalization = normalize_execution_plan(
                repaired,
                task,
                environment.executable_names,
            )
            _write_json(
                run_dir / "continuation-repair-normalization.json",
                repair_normalization.records,
            )
            plan = repair_normalization.plan
            _record_event(
                workflow,
                stage=WorkflowStage.REPAIR_APPLIED,
                state=WorkflowEventState.COMPLETED,
                attempt=parent_attempt,
                evidence_paths=["continuation-repair-decision.json"],
            )
        else:
            try:
                model_calls += 1
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_GENERATION_STARTED,
                    state=WorkflowEventState.STARTED,
                )
                plan = author_case_bundle(
                    task,
                    environment,
                    capability,
                    self.gateway,
                    context.knowledge_text,
                    context.skills_text,
                    budget=model_ledger.open_stage(
                        ModelStage.GENERATION,
                        request_timeout_seconds=300,
                        stage_deadline_seconds=360,
                        max_transport_attempts=3,
                    ),
                    trace=model_trace,
                )
            except GatewayRequestError as error:
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
        _write_json(run_dir / "authored-execution-plan.json", plan)
        normalization = normalize_execution_plan(
            plan,
            task,
            environment.executable_names,
        )
        _write_json(
            run_dir / "plan-normalization.json",
            normalization.records,
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
            environment.executable_names,
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

            try:
                if task.public_assets:
                    assert effective_public_asset_root is not None
                    stage_public_assets(
                        task,
                        effective_public_asset_root,
                        case_root,
                    )
                materialize_case(active_plan, task, case_root)
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
                available_executables=environment.executable_names,
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
            if inspection.passed:
                runner = self.runner
                if runner is None:
                    runner = PlanRunner.from_runtime_config(
                        self.runtime_config,
                        environment.executable_names,
                        workspace_root=run_dir,
                    )
                run_result = runner.run(
                    case_dir=case_root,
                    commands=active_plan.commands,
                    budget=task.resource_budget,
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
                report = validate_native_run(
                    task=task,
                    run_result=run_result,
                    case_root=case_root,
                )
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

            current_files = _read_declared_files(case_root, active_plan)
            try:
                repair_context = self._context(
                    task,
                    capability,
                    repair=True,
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
                model_calls += 1
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
                decision = request_repair(
                    task=task,
                    plan=active_plan,
                    report=report,
                    failed_log=log_text,
                    current_files=current_files,
                    knowledge_text=repair_context.knowledge_text,
                    skills_text=repair_context.skills_text,
                    gateway=self.gateway,
                    budget=model_ledger.open_stage(
                        ModelStage.REPAIR,
                        request_timeout_seconds=300,
                        stage_deadline_seconds=240,
                        max_transport_attempts=3,
                    ),
                    trace=model_trace,
                )
                issues = validate_repair_decision(
                    decision,
                    task=task,
                    plan=active_plan,
                    available_executables=environment.executable_names,
                    current_files=current_files,
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
                    primary_failure=_failure_record(
                        status=status,
                        message=(
                            "Native attempt failed before repair "
                            "could be completed."
                        ),
                        attempts=attempts,
                    ),
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
                    primary_failure=_failure_record(
                        status=status,
                        message=(
                            "Native attempt failed before repair "
                            "could be completed."
                        ),
                        attempts=attempts,
                    ),
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
            _write_json(
                attempt_root / "repair-decision.json",
                decision,
            )
            if issues:
                _write_json(
                    attempt_root / "repair-issues.json",
                    issues,
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message="Repair proposal violates safety policy.",
                    model_calls=model_calls,
                )

            changed = bool(
                decision.changed_commands
                or any(
                    current_files.get(item.path) != item.content
                    for item in decision.changed_files
                )
            )
            decision_stop = should_stop_repair(
                fingerprints=fingerprints,
                attempts_used=attempt_number,
                max_attempts=task.resource_budget.max_attempts,
                generated_bytes_changed=changed,
                decision=decision,
            )
            if decision_stop.stop:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair stopped: {decision_stop.reason}.",
                    model_calls=model_calls,
                )

            attempt_summary.changed_files = [
                item.path for item in decision.changed_files
            ]
            repaired = _apply_repair(active_plan, decision)
            repair_normalization = normalize_execution_plan(
                repaired,
                task,
                environment.executable_names,
            )
            _write_json(
                attempt_root / "repair-plan-normalization.json",
                repair_normalization.records,
            )
            active_plan = repair_normalization.plan
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
                    (
                        f"attempt-{attempt_number:02d}/"
                        "repair-decision.json"
                    ),
                    (
                        "checkpoints/active-plan-attempt-"
                        f"{attempt_number + 1:02d}.json"
                    ),
                ],
            )

        raise AssertionError("attempt loop exhausted without terminal result")
