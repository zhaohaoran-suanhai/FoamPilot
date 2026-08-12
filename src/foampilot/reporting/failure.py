"""Evidence-layered deterministic native failure reports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foampilot.agent.failure import NativeFailureClassification
from foampilot.evidence import RunFacts
from foampilot.repair import RepairDecision


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FailureObservation(StrictFrozenModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1)
    step_id: str | None = None
    evidence_paths: tuple[str, ...] = Field(min_length=1)


class ConfirmedCause(StrictFrozenModel):
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    evidence_paths: tuple[str, ...] = Field(min_length=1)


class FailureHypothesis(StrictFrozenModel):
    label: Literal["hypothesis"] = "hypothesis"
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    basis: tuple[str, ...] = Field(min_length=1)


class RepairDisposition(StrictFrozenModel):
    status: Literal[
        "not_requested",
        "authorized",
        "disabled",
        "confirmation_required",
        "rejected",
    ]
    reason: str
    reason_codes: tuple[str, ...] = ()


class ModelDiagnostic(StrictFrozenModel):
    label: Literal["hypothesis"] = "hypothesis"
    status: Literal["available", "unavailable", "disabled"]
    summary: str
    suggested_actions: tuple[str, ...] = ()


class FailureReport(StrictFrozenModel):
    schema_version: Literal[1] = 1
    failure_layer: str
    failure_code: str
    failed_stage: str
    failed_attempt: int | None
    failed_step_id: str | None
    observations: tuple[FailureObservation, ...]
    confirmed_causes: tuple[ConfirmedCause, ...]
    hypotheses: tuple[FailureHypothesis, ...]
    automatic_repair: RepairDisposition
    completed_progress: tuple[str, ...]
    preserved_artifacts: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    model_diagnostic: ModelDiagnostic | None = None

    @model_validator(mode="after")
    def validate_cause_evidence(self) -> "FailureReport":
        if any(not item.evidence_paths for item in self.confirmed_causes):
            raise ValueError("confirmed causes require evidence")
        return self


_MECHANICALLY_CONFIRMED = frozenset(
    {
        "missing_dictionary_keyword",
        "missing_case_file",
        "missing_registry_object",
        "dimension_mismatch",
        "unknown_function_object_type",
    }
)


def _log_paths(facts: RunFacts, step_id: str | None = None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path
            for step in facts.raw_steps
            if step_id is None or step.step_id == step_id
            for path in (step.stdout_path, step.stderr_path)
        )
    )


def _observations(facts: RunFacts) -> tuple[FailureObservation, ...]:
    result: list[FailureObservation] = []
    for step in facts.raw_steps:
        paths = (step.stdout_path, step.stderr_path)
        if step.cancelled:
            result.append(
                FailureObservation(
                    code="COMMAND_CANCELLED",
                    detail=f"步骤 {step.step_id} 收到取消请求。",
                    step_id=step.step_id,
                    evidence_paths=paths,
                )
            )
        if step.timed_out:
            result.append(
                FailureObservation(
                    code="COMMAND_TIMED_OUT",
                    detail=f"步骤 {step.step_id} 超时。",
                    step_id=step.step_id,
                    evidence_paths=paths,
                )
            )
        if step.return_code not in (0, None):
            result.append(
                FailureObservation(
                    code="COMMAND_RETURNED_NONZERO",
                    detail=(
                        f"步骤 {step.step_id} 返回码为 {step.return_code}。"
                    ),
                    step_id=step.step_id,
                    evidence_paths=paths,
                )
            )
    for error in facts.native_errors:
        result.append(
            FailureObservation(
                code=error.code,
                detail=error.detail,
                step_id=error.step_id,
                evidence_paths=_log_paths(facts, error.step_id),
            )
        )
    for progress in facts.solver_progress:
        if progress.completed_normally is False:
            result.append(
                FailureObservation(
                    code="NORMAL_END_MISSING",
                    detail=(
                        f"步骤 {progress.step_id} 在模拟时间 "
                        f"{progress.simulation_time:g} 未观察到正常 End。"
                    ),
                    step_id=progress.step_id,
                    evidence_paths=_log_paths(facts, progress.step_id),
                )
            )
    if len(facts.courant) >= 2 and (
        facts.courant[-1].maximum > facts.courant[0].maximum
        and facts.courant[-1].maximum > 1
    ):
        result.append(
            FailureObservation(
                code="COURANT_GROWTH",
                detail=(
                    "最大 Courant 数从 "
                    f"{facts.courant[0].maximum:g} 增长到 "
                    f"{facts.courant[-1].maximum:g}。"
                ),
                step_id=facts.courant[-1].step_id,
                evidence_paths=_log_paths(facts, facts.courant[-1].step_id),
            )
        )
    if len(facts.residuals) >= 2:
        first_by_field: dict[tuple[str | None, str], float] = {}
        for residual in facts.residuals:
            key = (residual.region, residual.field)
            first_by_field.setdefault(key, residual.initial)
        growing = [
            residual
            for residual in facts.residuals
            if residual.initial > first_by_field[(residual.region, residual.field)]
            and residual.initial > 1
        ]
        if growing:
            latest = growing[-1]
            result.append(
                FailureObservation(
                    code="RESIDUAL_GROWTH",
                    detail=(
                        f"字段 {latest.field} 的初始残差增长到 "
                        f"{latest.initial:g}。"
                    ),
                    step_id=latest.step_id,
                    evidence_paths=_log_paths(facts, latest.step_id),
                )
            )
    if len(facts.continuity) >= 2:
        first = facts.continuity[0].cumulative
        last = facts.continuity[-1].cumulative
        if first is not None and last is not None and abs(last) > max(abs(first), 1):
            result.append(
                FailureObservation(
                    code="CONTINUITY_ERROR_GROWTH",
                    detail=f"累计连续性误差增长到 {last:g}。",
                    step_id=facts.continuity[-1].step_id,
                    evidence_paths=_log_paths(
                        facts, facts.continuity[-1].step_id
                    ),
                )
            )
    unique: list[FailureObservation] = []
    seen: set[tuple[str, str | None, str]] = set()
    for item in result:
        key = (item.code, item.step_id, item.detail)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _repair_disposition(
    decision: RepairDecision | None,
) -> RepairDisposition:
    if decision is None:
        return RepairDisposition(
            status="not_requested",
            reason="not_requested",
        )
    reasons = decision.reason_codes
    if "AUTOMATIC_NUMERICAL_REPAIR_DISABLED" in reasons:
        return RepairDisposition(
            status="disabled",
            reason="disabled_by_user",
            reason_codes=reasons,
        )
    if decision.state in {"MECHANICAL_PATCH", "AUTHORIZED_NUMERICAL_PATCH"}:
        return RepairDisposition(
            status="authorized",
            reason="authorized_by_policy",
            reason_codes=reasons,
        )
    if decision.state == "CONFIRMATION_REQUIRED":
        return RepairDisposition(
            status="confirmation_required",
            reason="user_confirmation_required",
            reason_codes=reasons,
        )
    return RepairDisposition(
        status="rejected",
        reason="outside_automatic_repair_policy",
        reason_codes=reasons,
    )


def build_failure_report(
    run_facts: RunFacts,
    classification: NativeFailureClassification,
    repair_decision: RepairDecision | None,
    progress: tuple[str, ...] = (),
    artifacts: tuple[str, ...] = (),
) -> FailureReport:
    """Build one deterministic report without promoting inference to cause."""

    observations = _observations(run_facts)
    evidence_paths = tuple(
        dict.fromkeys(
            (
                "attempt-"
                f"{run_facts.attempt:02d}/run-facts.json",
                *(
                    path
                    for observation in observations
                    for path in observation.evidence_paths
                ),
            )
        )
    )
    confirmed: tuple[ConfirmedCause, ...] = ()
    hypotheses: tuple[FailureHypothesis, ...] = ()
    if (
        classification.confidence == "high"
        and classification.code in _MECHANICALLY_CONFIRMED
    ):
        confirmed = (
            ConfirmedCause(
                code=classification.code,
                detail=(
                    "确定性分类与直接结构/日志证据一致："
                    f"{classification.code}。"
                ),
                evidence_paths=evidence_paths,
            ),
        )
    else:
        basis = tuple(item.code for item in observations) or (
            "NO_DIRECT_CAUSE_EVIDENCE",
        )
        hypotheses = (
            FailureHypothesis(
                code=classification.code,
                detail=(
                    "这是基于当前公开证据的待验证解释，不是已确认根因。"
                ),
                basis=basis,
            ),
        )
    disposition = _repair_disposition(repair_decision)
    actions: list[str] = []
    if disposition.status == "disabled":
        actions.append(
            "自动数值修复已由用户关闭；请检查冻结证据后以新任务 rerun。"
        )
    elif disposition.status == "confirmation_required":
        actions.append("该变更需要用户逐项确认后创建新的 child/rerun。")
    elif disposition.status == "rejected":
        actions.append("建议补充权威输入或重新设计，不要绕过自动修复边界。")
    elif disposition.status == "authorized":
        actions.append("只执行已授权的最小修改，并重新运行完整公开门禁。")
    else:
        actions.append("检查直接观察与保留日志，再决定是否需要新的设计。")
    if hypotheses:
        actions.append("先验证推测原因，不能把模型或规则建议当作已确认根因。")
    return FailureReport(
        failure_layer=classification.domain.value,
        failure_code=classification.code,
        failed_stage=classification.failed_stage or "unknown",
        failed_attempt=run_facts.attempt,
        failed_step_id=classification.failed_step_id,
        observations=observations,
        confirmed_causes=confirmed,
        hypotheses=hypotheses,
        automatic_repair=disposition,
        completed_progress=progress,
        preserved_artifacts=artifacts,
        recommended_actions=tuple(actions),
        evidence_paths=evidence_paths,
    )


__all__ = [
    "ConfirmedCause",
    "FailureHypothesis",
    "FailureObservation",
    "FailureReport",
    "ModelDiagnostic",
    "RepairDisposition",
    "build_failure_report",
]
