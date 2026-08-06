"""Deterministic, leakage-safe state projected at model decision points."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from hashlib import sha256
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.context import AgentContext
from foampilot.models import ModelBudgetLedger
from foampilot.plans import ExecutionPlan
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec
from foampilot.workflow import (
    FailureRecord,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowStore,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentDecisionStage(StrEnum):
    AUTHOR = "author"
    REPAIR = "repair"


class StatusAttempt(StrictModel):
    current: int = Field(ge=1)
    maximum: int = Field(ge=1)


class StatusCapability(StrictModel):
    solver_family: str | None
    solver: str | None
    regions: list[str] = Field(default_factory=list)


class StatusBudget(StrictModel):
    model_logical_requests_remaining: int = Field(ge=0)
    transport_attempts_remaining: int = Field(ge=0)
    model_seconds_remaining: float = Field(ge=0)
    execution_seconds_remaining: float = Field(ge=0)


class StatusContext(StrictModel):
    knowledge_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    knowledge_sources_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skills_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImmutableConstraints(StrictModel):
    public_assets: list[str] = Field(default_factory=list)
    protected_path_count: int = Field(ge=0)
    protected_paths_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    openfoam_distribution: str
    openfoam_version: str


class AgentStatusSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    source_event_sequence: int = Field(ge=1)
    current_stage: AgentDecisionStage
    last_completed_stage: WorkflowStage | None
    attempt: StatusAttempt
    capability: StatusCapability
    latest_failure: FailureRecord | None
    budget: StatusBudget
    context: StatusContext
    allowed_actions: list[str] = Field(min_length=1)
    immutable_constraints: ImmutableConstraints


class AgentStatusError(ValueError):
    code = "AGENT_STATUS_INCONSISTENT"
    message = "无法从当前运行事实构造一致的 Agent 状态。"
    recovery = "请核对工作流事件、attempt、预算与当前计划后重新运行。"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _solver_families_compatible(
    manifest_family: str,
    capability_family: str,
) -> bool:
    """Accept descriptive manifest suffixes without hiding real conflicts."""

    manifest_tokens = set(re.findall(r"[a-z0-9]+", manifest_family.lower()))
    capability_tokens = set(
        re.findall(r"[a-z0-9]+", capability_family.lower())
    )
    return bool(capability_tokens) and capability_tokens <= manifest_tokens


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _events(workflow: WorkflowStore) -> list[WorkflowEvent]:
    if not workflow.events_path.is_file():
        return []
    return [
        WorkflowEvent.model_validate_json(line)
        for line in workflow.events_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def _validate_fact_sources(
    *,
    decision_stage: AgentDecisionStage,
    events: list[WorkflowEvent],
    task: TaskSpec,
    capability: CapabilityProfile,
    plan: ExecutionPlan | None,
    latest_failure: FailureRecord | None,
    logical_requests_used: int,
    logical_request_limit: int,
    current_attempt: int,
    execution_seconds_used: float,
) -> None:
    if not events:
        raise AgentStatusError("workflow has no events")
    expected_event = {
        AgentDecisionStage.AUTHOR: WorkflowStage.MODEL_GENERATION_STARTED,
        AgentDecisionStage.REPAIR: WorkflowStage.MODEL_REPAIR_STARTED,
    }[decision_stage]
    last = events[-1]
    if last.stage != expected_event or last.state != WorkflowEventState.STARTED:
        raise AgentStatusError(
            f"latest event must be STARTED {expected_event.value}"
        )
    if not 1 <= current_attempt <= task.resource_budget.max_attempts:
        raise AgentStatusError("current attempt is outside the task budget")
    if not 0 <= logical_requests_used <= logical_request_limit:
        raise AgentStatusError("logical model request accounting is invalid")
    if execution_seconds_used < 0:
        raise AgentStatusError("execution time accounting is invalid")
    if decision_stage == AgentDecisionStage.AUTHOR:
        if latest_failure is not None:
            raise AgentStatusError("author stage cannot carry a native failure")
    else:
        if plan is None or latest_failure is None:
            raise AgentStatusError("repair stage requires plan and failure")
        if last.attempt != current_attempt:
            raise AgentStatusError("repair event attempt does not match status")
    if plan is not None:
        manifest = plan.manifest
        if (
            capability.solver_executable is not None
            and manifest.solver_executable
            != capability.solver_executable
        ):
            raise AgentStatusError("plan solver conflicts with capability")
        if (
            capability.solver_family is not None
            and not _solver_families_compatible(
                manifest.solver_family,
                capability.solver_family,
            )
        ):
            raise AgentStatusError(
                "plan solver family conflicts with capability"
            )


def build_agent_status_snapshot(
    *,
    decision_stage: AgentDecisionStage,
    task: TaskSpec,
    capability: CapabilityProfile,
    context: AgentContext,
    workflow: WorkflowStore,
    model_budget: ModelBudgetLedger,
    logical_requests_used: int,
    logical_request_limit: int,
    current_attempt: int,
    execution_seconds_used: float,
    plan: ExecutionPlan | None = None,
    latest_failure: FailureRecord | None = None,
    allowed_actions: list[str] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> AgentStatusSnapshot:
    """Build one compact snapshot solely from already validated facts."""

    events = _events(workflow)
    _validate_fact_sources(
        decision_stage=decision_stage,
        events=events,
        task=task,
        capability=capability,
        plan=plan,
        latest_failure=latest_failure,
        logical_requests_used=logical_requests_used,
        logical_request_limit=logical_request_limit,
        current_attempt=current_attempt,
        execution_seconds_used=execution_seconds_used,
    )
    completed = [
        item.stage
        for item in events
        if item.state == WorkflowEventState.COMPLETED
    ]
    protected_paths = sorted(task.protected_paths)
    regions = (
        [item.name for item in plan.manifest.regions]
        if plan is not None
        else []
    )
    active_allowed_actions = allowed_actions or (
        ["author_case_bundle"]
        if decision_stage == AgentDecisionStage.AUTHOR
        else [
            "add_file",
            "replace_file",
            "insert_command",
            "replace_command",
            "remove_command",
        ]
    )
    return AgentStatusSnapshot(
        source_event_sequence=events[-1].sequence,
        current_stage=decision_stage,
        last_completed_stage=completed[-1] if completed else None,
        attempt=StatusAttempt(
            current=current_attempt,
            maximum=task.resource_budget.max_attempts,
        ),
        capability=StatusCapability(
            solver_family=capability.solver_family,
            solver=capability.solver_executable,
            regions=regions,
        ),
        latest_failure=latest_failure,
        budget=StatusBudget(
            model_logical_requests_remaining=max(
                0,
                logical_request_limit - logical_requests_used,
            ),
            transport_attempts_remaining=(
                model_budget.transport_attempts_remaining
            ),
            model_seconds_remaining=model_budget.total_seconds_remaining(
                now=now
            ),
            execution_seconds_remaining=max(
                0.0,
                task.resource_budget.max_wall_seconds
                - execution_seconds_used,
            ),
        ),
        context=StatusContext(
            knowledge_ids=list(context.selected_knowledge_ids),
            skill_names=list(context.skill_names),
            knowledge_sources_sha256=_canonical_sha256(
                context.selected_source_hashes
            ),
            skills_sha256=sha256(
                context.skills_text.encode("utf-8")
            ).hexdigest(),
        ),
        allowed_actions=active_allowed_actions,
        immutable_constraints=ImmutableConstraints(
            public_assets=[item.path for item in task.public_assets],
            protected_path_count=len(protected_paths),
            protected_paths_sha256=_canonical_sha256(protected_paths),
            openfoam_distribution=task.openfoam_target.distribution,
            openfoam_version=task.openfoam_target.version,
        ),
    )


def status_snapshot_sha256(snapshot: AgentStatusSnapshot) -> str:
    return _canonical_sha256(snapshot.model_dump(mode="json"))


__all__ = [
    "AgentDecisionStage",
    "AgentStatusError",
    "AgentStatusSnapshot",
    "build_agent_status_snapshot",
    "status_snapshot_sha256",
]
