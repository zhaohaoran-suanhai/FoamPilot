"""Production stage adapters for observation and acceptance contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict

from foampilot.acceptance import (
    AcceptanceCompiler,
    AcceptanceEvaluator,
    AcceptancePlan,
    ResultReport,
)
from foampilot.evidence import RunFacts
from foampilot.observations import (
    ObservationPlan,
    ObservationPlanner,
    first_party_observation_registry,
)
from foampilot.postprocessing import (
    DerivedMetrics,
    PostProcessingEngine,
    foundation10_calculators,
)
from foampilot.preprocessing import InputMeshFacts
from foampilot.simulation import CaseDesign, SimulationIntent
from foampilot.workflow import (
    CallableStageService,
    FailureDomain,
    FailureRecord,
    StageOutcome,
    WorkflowContext,
    WorkflowCoordinator,
    WorkflowStage,
    WorkflowStore,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningContracts(_StrictFrozenModel):
    acceptance: AcceptancePlan
    observations: ObservationPlan


class PublicResults(_StrictFrozenModel):
    metrics: DerivedMetrics
    report: ResultReport


class ContractStageError(RuntimeError):
    def __init__(self, failure: FailureRecord) -> None:
        super().__init__(failure.detail)
        self.failure = failure


def _payload(value: object) -> object:
    return (
        value.model_dump(mode="json")
        if isinstance(value, BaseModel)
        else value
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _payload(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


class ContractStagePipeline:
    """Run contract stages without giving the coordinator CFD authority."""

    def __init__(
        self,
        *,
        run_dir: Path,
        task_id: str,
        workflow: WorkflowStore,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.context = WorkflowContext(
            run_id=run_dir.name,
            task_id=task_id,
        )
        self.coordinator = WorkflowCoordinator(
            store=workflow,
            cancellation_requested=cancellation_requested,
        )

    def _advance(
        self,
        *,
        stage: WorkflowStage,
        callback: Callable[[WorkflowContext], StageOutcome],
    ) -> None:
        outcome = self.coordinator.advance(
            self.context,
            CallableStageService(stage=stage, callback=callback),
        )
        if outcome.status != "completed":
            failure = outcome.failure or FailureRecord(
                domain=FailureDomain.WORKFLOW,
                code="STAGE_DID_NOT_COMPLETE",
                detail=outcome.detail or "contract stage did not complete",
            )
            raise ContractStageError(failure)

    def plan_before_authoring(
        self,
        *,
        intent: SimulationIntent,
        design: CaseDesign,
        mesh_facts: tuple[InputMeshFacts, ...],
    ) -> PlanningContracts:
        holder: dict[str, object] = {}

        def compile_acceptance(_: WorkflowContext) -> StageOutcome:
            try:
                plan = AcceptanceCompiler().compile(
                    observation_requests=intent.observation_requests,
                    condition_requests=intent.acceptance_requests,
                )
                _write_json(self.run_dir / "acceptance-plan.json", plan)
                holder["acceptance"] = plan
                return StageOutcome(
                    status="completed",
                    checkpoint_name="acceptance-plan",
                    checkpoint_payload=plan,
                    artifact_paths=("acceptance-plan.json",),
                )
            except (LookupError, OSError, ValueError) as error:
                failure = FailureRecord(
                    domain=FailureDomain.DESIGN,
                    code="ACCEPTANCE_COMPILATION_FAILED",
                    detail=str(error),
                    message="显式验收条件无法编译成安全契约。",
                    recovery="检查可观测量、单位、运算符、阈值和确认来源。",
                )
                return StageOutcome(
                    status="failed",
                    detail=failure.detail,
                    failure=failure,
                )

        self._advance(
            stage=WorkflowStage.ACCEPTANCE_COMPILED,
            callback=compile_acceptance,
        )
        acceptance = holder["acceptance"]
        assert isinstance(acceptance, AcceptancePlan)

        def plan_observations(_: WorkflowContext) -> StageOutcome:
            try:
                plan = ObservationPlanner().compile(
                    intent=intent,
                    design=design,
                    mesh_facts=mesh_facts,
                    registry=first_party_observation_registry(),
                    acceptance_plan=acceptance,
                )
                _write_json(self.run_dir / "observation-plan.json", plan)
                holder["observations"] = plan
                return StageOutcome(
                    status="completed",
                    checkpoint_name="observation-plan",
                    checkpoint_payload=plan,
                    artifact_paths=("observation-plan.json",),
                )
            except (LookupError, OSError, ValueError) as error:
                failure = FailureRecord(
                    domain=FailureDomain.DESIGN,
                    code="OBSERVATION_PLANNING_FAILED",
                    detail=str(error),
                    message="观测范围无法在算例生成前冻结。",
                    recovery="检查网格 patch/zone、物理量和所需时间范围。",
                    evidence_paths=["acceptance-plan.json"],
                )
                return StageOutcome(
                    status="failed",
                    detail=failure.detail,
                    failure=failure,
                )

        self._advance(
            stage=WorkflowStage.OBSERVATION_PLANNED,
            callback=plan_observations,
        )
        observations = holder["observations"]
        assert isinstance(observations, ObservationPlan)
        return PlanningContracts(
            acceptance=acceptance,
            observations=observations,
        )

    def record_case_authored(self, bundle: BaseModel) -> None:
        self._advance(
            stage=WorkflowStage.CASE_AUTHORED,
            callback=lambda _: StageOutcome(
                status="completed",
                checkpoint_name="case-authored",
                checkpoint_payload=bundle,
                artifact_paths=(
                    "case-bundle.json",
                    "design-conformance.json",
                    "observation-plan.json",
                ),
            ),
        )

    def evaluate_after_evidence(
        self,
        *,
        contracts: PlanningContracts,
        run_facts: RunFacts,
        case_root: Path,
        attempt_root: Path,
        attempt_number: int,
    ) -> PublicResults:
        context = self.context.model_copy(update={"attempt": attempt_number})
        holder: dict[str, object] = {}

        def postprocess(_: WorkflowContext) -> StageOutcome:
            try:
                artifacts = {
                    item.observation_id: path
                    for item in contracts.observations.items
                    if (
                        path := case_root
                        / ".foampilot"
                        / "observations"
                        / f"{item.observation_id}.json"
                    ).is_file()
                }
                metrics = PostProcessingEngine(
                    calculators=foundation10_calculators()
                ).derive(
                    contracts.observations,
                    run_facts,
                    case_root,
                    artifacts,
                )
                _write_json(attempt_root / "derived-metrics.json", metrics)
                _write_json(self.run_dir / "derived-metrics.json", metrics)
                holder["metrics"] = metrics
                return StageOutcome(
                    status="completed",
                    checkpoint_name=(
                        f"derived-metrics-attempt-{attempt_number:02d}"
                    ),
                    checkpoint_payload=metrics,
                    artifact_paths=(
                        f"attempt-{attempt_number:02d}/derived-metrics.json",
                    ),
                )
            except (OSError, ValueError) as error:
                failure = FailureRecord(
                    domain=FailureDomain.POSTPROCESS,
                    code="POSTPROCESS_FAILED",
                    detail=str(error),
                    message="求解证据无法转换为结构化物理指标。",
                    recovery="检查 RunFacts、观测输出和对应的一方计算器。",
                )
                return StageOutcome(
                    status="failed",
                    detail=failure.detail,
                    failure=failure,
                )

        outcome = self.coordinator.advance(
            context,
            CallableStageService(
                stage=WorkflowStage.POSTPROCESSED,
                callback=postprocess,
            ),
        )
        if outcome.status != "completed":
            raise ContractStageError(
                outcome.failure
                or FailureRecord(
                    domain=FailureDomain.POSTPROCESS,
                    code="POSTPROCESS_FAILED",
                    detail=outcome.detail or "post-processing failed",
                )
            )
        metrics = holder["metrics"]
        assert isinstance(metrics, DerivedMetrics)

        def evaluate(_: WorkflowContext) -> StageOutcome:
            try:
                report = AcceptanceEvaluator().evaluate(
                    contracts.acceptance,
                    metrics,
                )
                _write_json(attempt_root / "result-report.json", report)
                _write_json(self.run_dir / "result-report.json", report)
                holder["report"] = report
                return StageOutcome(
                    status="completed",
                    checkpoint_name=(
                        f"result-report-attempt-{attempt_number:02d}"
                    ),
                    checkpoint_payload=report,
                    artifact_paths=(
                        f"attempt-{attempt_number:02d}/result-report.json",
                    ),
                )
            except (OSError, ValueError) as error:
                failure = FailureRecord(
                    domain=FailureDomain.VALIDATION,
                    code="ACCEPTANCE_EVALUATION_FAILED",
                    detail=str(error),
                    message="结构化指标无法按冻结验收条件判定。",
                    recovery="检查指标单位、标量类型和验收契约哈希。",
                )
                return StageOutcome(
                    status="failed",
                    detail=failure.detail,
                    failure=failure,
                )

        outcome = self.coordinator.advance(
            context,
            CallableStageService(
                stage=WorkflowStage.ACCEPTANCE_EVALUATED,
                callback=evaluate,
            ),
        )
        if outcome.status != "completed":
            raise ContractStageError(
                outcome.failure
                or FailureRecord(
                    domain=FailureDomain.VALIDATION,
                    code="ACCEPTANCE_EVALUATION_FAILED",
                    detail=outcome.detail or "acceptance evaluation failed",
                )
            )
        report = holder["report"]
        assert isinstance(report, ResultReport)
        return PublicResults(metrics=metrics, report=report)


__all__ = [
    "ContractStageError",
    "ContractStagePipeline",
    "PlanningContracts",
    "PublicResults",
]
