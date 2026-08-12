from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from foampilot.agent.status import (
    AgentDecisionStage,
    AgentStatusError,
    build_agent_status_snapshot,
    status_snapshot_sha256,
)
from foampilot.context import AgentContext
from foampilot.models import ModelBudgetLedger
from foampilot.routing import CapabilityProfile
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowStore,
)

from tests.test_execution_plan import task as task_fixture
from tests.test_execution_plan import valid_plan


def _context() -> AgentContext:
    return AgentContext(
        knowledge_text="public knowledge",
        skills_text="portable skill",
        knowledge_slots={"solver_contract": "of10.ico.contract"},
        missing_slots=(),
        selected_knowledge_ids=("of10.ico.contract",),
        selected_source_hashes={"of10.ico.contract": "a" * 64},
        skill_names=("openfoam-author-native-case",),
    )


def _capability(
    *,
    solver_family: str = "incompressible_transient",
) -> CapabilityProfile:
    return CapabilityProfile(
        physics_family="incompressible_flow",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        turbulence="laminar",
        solver_family=solver_family,
        solver_executable="icoFoam",
        mesh_family="block_mesh",
        parallel_expected=False,
        confidence="high",
        evidence=[],
    )


def _record(
    store: WorkflowStore,
    stage: WorkflowStage,
    state: WorkflowEventState,
    *,
    attempt: int | None = None,
) -> None:
    store.record(
        WorkflowEvent(
            sequence=store.next_sequence,
            stage=stage,
            state=state,
            occurred_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            attempt=attempt,
        )
    )


def test_author_status_is_deterministic_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    task = task_fixture.__wrapped__().model_copy(
        update={
            "protected_paths": [
                "/private/golden/case",
                "/private/evaluator/rules.py",
            ]
        }
    )
    store = WorkflowStore(run_dir=tmp_path)
    _record(store, WorkflowStage.CONTEXT_READY, WorkflowEventState.COMPLETED)
    _record(
        store,
        WorkflowStage.MODEL_GENERATION_STARTED,
        WorkflowEventState.STARTED,
    )
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        transport_attempts_used=2,
        now=lambda: 100.0,
    )

    snapshot = build_agent_status_snapshot(
        decision_stage=AgentDecisionStage.AUTHOR,
        task=task,
        capability=_capability(),
        context=_context(),
        workflow=store,
        model_budget=ledger,
        logical_requests_used=0,
        logical_request_limit=2,
        current_attempt=1,
        execution_seconds_used=0.0,
        now=lambda: 120.0,
    )

    assert snapshot.source_event_sequence == 2
    assert snapshot.current_stage == "author"
    assert snapshot.last_completed_stage == "CONTEXT_READY"
    assert snapshot.attempt.current == 1
    assert snapshot.budget.model_logical_requests_remaining == 2
    assert snapshot.budget.transport_attempts_remaining == 5
    assert snapshot.budget.execution_seconds_remaining == pytest.approx(
        task.resource_budget.max_wall_seconds
    )
    assert snapshot.context.knowledge_ids == ["of10.ico.contract"]
    assert snapshot.capability.regions == []
    assert snapshot.immutable_constraints.protected_path_count == 2

    serialized = snapshot.model_dump_json()
    assert "/private/golden/case" not in serialized
    assert "/private/evaluator/rules.py" not in serialized
    expected_digest = sha256(
        json.dumps(
            sorted(task.protected_paths),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert snapshot.immutable_constraints.protected_paths_sha256 == expected_digest
    assert status_snapshot_sha256(snapshot) == status_snapshot_sha256(
        snapshot.model_copy(deep=True)
    )


def test_repair_status_uses_plan_regions_failure_and_remaining_budget(
    tmp_path: Path,
) -> None:
    task = task_fixture.__wrapped__()
    plan = valid_plan()
    store = WorkflowStore(run_dir=tmp_path)
    _record(
        store,
        WorkflowStage.RUN_ASSESSED,
        WorkflowEventState.COMPLETED,
        attempt=1,
    )
    _record(
        store,
        WorkflowStage.MODEL_REPAIR_STARTED,
        WorkflowEventState.STARTED,
        attempt=1,
    )
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        transport_attempts_used=3,
        now=lambda: 10.0,
    )
    failure = FailureRecord(
        domain=FailureDomain.SOLVER,
        code="missing_dictionary_keyword",
        step_id="solve-a",
        detail="keyword div(phi,U) is undefined",
    )

    snapshot = build_agent_status_snapshot(
        decision_stage=AgentDecisionStage.REPAIR,
        task=task,
        capability=_capability(
            solver_family=plan.manifest.solver_family,
        ),
        context=_context(),
        workflow=store,
        model_budget=ledger,
        logical_requests_used=1,
        logical_request_limit=2,
        current_attempt=1,
        execution_seconds_used=12.5,
        plan=plan,
        latest_failure=failure,
        now=lambda: 20.0,
    )

    assert snapshot.current_stage == "repair"
    assert snapshot.latest_failure == failure
    assert snapshot.capability.regions == ["default"]
    assert snapshot.allowed_actions == [
        "add_file",
        "replace_file",
        "insert_command",
        "replace_command",
        "remove_command",
    ]
    assert snapshot.budget.model_logical_requests_remaining == 1
    assert snapshot.budget.transport_attempts_remaining == 4
    assert snapshot.budget.execution_seconds_remaining == pytest.approx(
        task.resource_budget.max_wall_seconds - 12.5
    )


def test_repair_status_accepts_descriptive_solver_family_extension(
    tmp_path: Path,
) -> None:
    task = task_fixture.__wrapped__()
    plan = valid_plan()
    plan.manifest.solver_family = "incompressible-transient PIMPLE"
    store = WorkflowStore(run_dir=tmp_path)
    _record(
        store,
        WorkflowStage.RUN_ASSESSED,
        WorkflowEventState.COMPLETED,
        attempt=1,
    )
    _record(
        store,
        WorkflowStage.MODEL_REPAIR_STARTED,
        WorkflowEventState.STARTED,
        attempt=1,
    )
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        now=lambda: 10.0,
    )

    snapshot = build_agent_status_snapshot(
        decision_stage=AgentDecisionStage.REPAIR,
        task=task,
        capability=_capability(solver_family="incompressible-transient"),
        context=_context(),
        workflow=store,
        model_budget=ledger,
        logical_requests_used=1,
        logical_request_limit=2,
        current_attempt=1,
        execution_seconds_used=12.5,
        plan=plan,
        latest_failure=FailureRecord(
            domain=FailureDomain.SOLVER,
            code="solver_failed",
            detail="failed",
        ),
        now=lambda: 20.0,
    )

    assert snapshot.capability.solver_family == "incompressible-transient"


@pytest.mark.parametrize(
    ("decision_stage", "event_stage", "failure", "plan_present"),
    [
        (
            AgentDecisionStage.AUTHOR,
            WorkflowStage.MODEL_REPAIR_STARTED,
            None,
            False,
        ),
        (
            AgentDecisionStage.REPAIR,
            WorkflowStage.MODEL_REPAIR_STARTED,
            None,
            True,
        ),
        (
            AgentDecisionStage.REPAIR,
            WorkflowStage.MODEL_REPAIR_STARTED,
            FailureRecord(
                domain=FailureDomain.SOLVER,
                code="solver_failed",
                detail="failed",
            ),
            False,
        ),
    ],
)
def test_status_rejects_inconsistent_fact_sources(
    tmp_path: Path,
    decision_stage: AgentDecisionStage,
    event_stage: WorkflowStage,
    failure: FailureRecord | None,
    plan_present: bool,
) -> None:
    store = WorkflowStore(run_dir=tmp_path)
    _record(store, event_stage, WorkflowEventState.STARTED, attempt=1)
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=60,
        lineage_transport_attempt_limit=7,
        now=lambda: 0.0,
    )

    with pytest.raises(AgentStatusError) as captured:
        build_agent_status_snapshot(
            decision_stage=decision_stage,
            task=task_fixture.__wrapped__(),
            capability=_capability(),
            context=_context(),
            workflow=store,
            model_budget=ledger,
            logical_requests_used=0,
            logical_request_limit=2,
            current_attempt=1,
            execution_seconds_used=0.0,
            plan=valid_plan() if plan_present else None,
            latest_failure=failure,
            now=lambda: 1.0,
        )

    assert captured.value.code == "AGENT_STATUS_INCONSISTENT"
    assert captured.value.message == "无法从当前运行事实构造一致的 Agent 状态。"
    assert captured.value.recovery
