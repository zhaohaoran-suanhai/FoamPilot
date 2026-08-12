from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from foampilot.workflow import (
    CANONICAL_STAGE_ORDER,
    FailureDomain,
    FailureRecord,
    StageOutcome,
    WorkflowContext,
    WorkflowCoordinator,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowStore,
)


class _Service:
    def __init__(self, stage: WorkflowStage, calls: list[WorkflowStage]) -> None:
        self.stage = stage
        self.calls = calls
        self.outcome = StageOutcome(
            status="completed",
            checkpoint_name=stage.value.lower(),
            checkpoint_payload={"stage": stage.value},
            detail=f"{stage.value} complete",
        )

    def run(self, context: WorkflowContext) -> StageOutcome:
        self.calls.append(self.stage)
        return self.outcome


def _services(calls: list[WorkflowStage]) -> list[_Service]:
    return [_Service(stage, calls) for stage in CANONICAL_STAGE_ORDER]


def _events(run_dir: Path) -> list[WorkflowEvent]:
    return [
        WorkflowEvent.model_validate_json(line)
        for line in (run_dir / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_coordinator_runs_declared_stages_and_checkpoints_in_order(
    tmp_path: Path,
) -> None:
    calls: list[WorkflowStage] = []
    store = WorkflowStore(run_dir=tmp_path)
    coordinator = WorkflowCoordinator(store=store)

    outcome = coordinator.run(
        WorkflowContext(run_id="run-1", task_id="task-1"),
        _services(calls),
    )

    assert calls == list(CANONICAL_STAGE_ORDER)
    assert outcome.summary.workflow_state == "COMPLETED"
    assert outcome.summary.current_stage == WorkflowStage.RUN_FINALIZED
    assert (tmp_path / "summary.json").is_file()
    for stage in CANONICAL_STAGE_ORDER:
        assert (tmp_path / "checkpoints" / f"{stage.value.lower()}.json").is_file()
    events = _events(tmp_path)
    assert events[-1].stage == WorkflowStage.RUN_FINALIZED
    assert events[-1].state == WorkflowEventState.COMPLETED


def test_checkpoint_exists_before_completed_transition(tmp_path: Path) -> None:
    seen: list[bool] = []

    def listener(event: WorkflowEvent) -> None:
        if (
            event.state == WorkflowEventState.COMPLETED
            and event.stage != WorkflowStage.RUN_FINALIZED
        ):
            seen.append(
                (
                    tmp_path
                    / "checkpoints"
                    / f"{event.stage.value.lower()}.json"
                ).is_file()
            )

    calls: list[WorkflowStage] = []
    coordinator = WorkflowCoordinator(
        store=WorkflowStore(run_dir=tmp_path, event_listener=listener)
    )
    coordinator.run(
        WorkflowContext(run_id="run-1", task_id="task-1"),
        _services(calls),
    )

    assert seen and all(seen)


@pytest.mark.parametrize("stop_index", range(len(CANONICAL_STAGE_ORDER)))
def test_cancellation_is_observed_before_every_stage(
    tmp_path: Path,
    stop_index: int,
) -> None:
    calls: list[WorkflowStage] = []

    def cancelled() -> bool:
        return len(calls) >= stop_index

    outcome = WorkflowCoordinator(
        store=WorkflowStore(run_dir=tmp_path),
        cancellation_requested=cancelled,
    ).run(
        WorkflowContext(run_id="run-1", task_id="task-1"),
        _services(calls),
    )

    assert outcome.summary.workflow_state == "CANCELLED"
    assert outcome.summary.failure is not None
    assert outcome.summary.failure.code == "USER_CANCELLED"
    assert _events(tmp_path)[-1].state == WorkflowEventState.CANCELLED


def test_deferred_and_failed_outcomes_are_terminal_and_truthful(
    tmp_path: Path,
) -> None:
    calls: list[WorkflowStage] = []
    services = _services(calls)
    services[2].outcome = StageOutcome(
        status="deferred",
        detail="missing inlet velocity",
        failure=FailureRecord(
            domain=FailureDomain.DESIGN,
            code="INFORMATION_REQUIRED",
            detail="missing inlet velocity",
        ),
    )

    outcome = WorkflowCoordinator(
        store=WorkflowStore(run_dir=tmp_path)
    ).run(WorkflowContext(run_id="run-1", task_id="task-1"), services)

    assert outcome.summary.workflow_state == "DEFERRED"
    assert outcome.summary.failure.code == "INFORMATION_REQUIRED"
    assert calls == list(CANONICAL_STAGE_ORDER[:3])


def test_service_exception_is_normalized_without_domain_inference(
    tmp_path: Path,
) -> None:
    calls: list[WorkflowStage] = []
    services = _services(calls)

    def fail(context: WorkflowContext) -> StageOutcome:
        raise OSError("disk read failed")

    services[1].run = fail  # type: ignore[method-assign]
    outcome = WorkflowCoordinator(
        store=WorkflowStore(run_dir=tmp_path)
    ).run(WorkflowContext(run_id="run-1", task_id="task-1"), services)

    assert outcome.summary.workflow_state == "FAILED"
    assert outcome.summary.failure.code == "STAGE_SERVICE_FAILED"
    assert outcome.summary.failure.domain == FailureDomain.WORKFLOW
    assert "OSError" in outcome.summary.failure.detail


def test_service_order_mismatch_is_rejected_before_work(tmp_path: Path) -> None:
    calls: list[WorkflowStage] = []
    services = _services(calls)
    services[0], services[1] = services[1], services[0]

    with pytest.raises(ValueError, match="canonical stage order"):
        WorkflowCoordinator(store=WorkflowStore(run_dir=tmp_path)).run(
            WorkflowContext(run_id="run-1", task_id="task-1"),
            services,
        )

    assert calls == []


def test_repair_budget_is_checked_before_stage_execution(tmp_path: Path) -> None:
    calls: list[WorkflowStage] = []
    outcome = WorkflowCoordinator(
        store=WorkflowStore(run_dir=tmp_path)
    ).run(
        WorkflowContext(
            run_id="run-1",
            task_id="task-1",
            repair_cycles_used=2,
            max_repair_cycles=1,
        ),
        _services(calls),
    )

    assert calls == []
    assert outcome.summary.workflow_state == "FAILED"
    assert outcome.summary.failure.code == "REPAIR_BUDGET_EXHAUSTED"


def test_coordinator_source_contains_no_domain_tokens() -> None:
    source = (
        Path(__file__).parents[1]
        / "src/foampilot/workflow/coordinator.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "checkMesh",
        "pisoFoam",
        "residual",
        "polyMesh",
        "Courant",
    ):
        assert forbidden not in source
