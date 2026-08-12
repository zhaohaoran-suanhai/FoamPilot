"""Optional, non-authoritative model hypotheses for terminal failures."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from foampilot.evidence import RunFacts
from foampilot.models import ModelBudgetWindow, ModelRequest, ModelTraceSink

from .failure import FailureReport, ModelDiagnostic


class _DiagnosticProposal(BaseModel):
    """The model cannot write causes, workflow state, or repair authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=1000)
    suggested_actions: tuple[str, ...] = Field(default=(), max_length=5)


class _StructuredGateway(Protocol):
    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[_DiagnosticProposal],
        *,
        budget: ModelBudgetWindow,
        trace: ModelTraceSink,
    ) -> Any: ...


_PUBLIC_KNOWLEDGE: dict[str, tuple[str, ...]] = {
    "numerical_instability": (
        "Courant growth and residual growth are symptoms, not root causes.",
        "Check dimensions, boundary consistency, time step, and numerical schemes.",
    ),
    "unclassified_native_failure": (
        "Insufficient public evidence is not evidence of one specific cause.",
    ),
}


def _sanitized_payload(
    report: FailureReport,
    facts: RunFacts,
) -> dict[str, object]:
    """Return bounded public facts without file contents, paths, hashes, or argv."""

    residual_tail = [
        {
            "field": item.field,
            "simulation_time": item.simulation_time,
            "initial": item.initial,
            "final": item.final,
        }
        for item in facts.residuals[-12:]
    ]
    return {
        "failure": {
            "layer": report.failure_layer,
            "code": report.failure_code,
            "stage": report.failed_stage,
            "attempt": report.failed_attempt,
            "step_id": report.failed_step_id,
            "observations": [
                {"code": item.code, "detail": item.detail}
                for item in report.observations
            ],
            "existing_hypotheses": [
                {"code": item.code, "basis": item.basis}
                for item in report.hypotheses
            ],
            "repair_status": report.automatic_repair.status,
        },
        "run_facts": {
            "attempt": facts.attempt,
            "steps": [
                {
                    "step_id": item.step_id,
                    "stage": item.stage,
                    "return_code": item.return_code,
                    "timed_out": item.timed_out,
                    "cancelled": item.cancelled,
                    "execution_backend": item.execution_backend,
                }
                for item in facts.raw_steps
            ],
            "latest_simulation_time": (
                facts.solver_progress[-1].simulation_time
                if facts.solver_progress
                else None
            ),
            "residual_tail": residual_tail,
            "native_errors": [
                {"code": item.code, "detail": item.detail}
                for item in facts.native_errors
            ],
        },
        "public_error_knowledge": _PUBLIC_KNOWLEDGE.get(
            report.failure_code, ()
        ),
    }


def append_model_diagnostic(
    report: FailureReport,
    public_evidence: RunFacts,
    gateway: _StructuredGateway | None,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> FailureReport:
    """Append a labeled advisory without changing deterministic failure truth."""

    if gateway is None:
        return report.model_copy(
            update={
                "model_diagnostic": ModelDiagnostic(
                    status="disabled",
                    summary="可选模型失败诊断未启用。",
                )
            }
        )
    payload = _sanitized_payload(report, public_evidence)
    request = ModelRequest(
        purpose="failure_diagnostic",
        system_prompt=(
            "你是终态失败后的只读诊断助手。只提出明确标注为待验证的解释和检查建议；"
            "不得声明已确认根因，不得改变任务终态、失败码或修复权限。"
        ),
        user_prompt=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    try:
        result = gateway.generate_structured(
            request,
            _DiagnosticProposal,
            budget=budget,
            trace=trace,
        )
        proposal = result.value
        diagnostic = ModelDiagnostic(
            status="available",
            summary=proposal.summary,
            suggested_actions=proposal.suggested_actions,
        )
    except Exception:
        diagnostic = ModelDiagnostic(
            status="unavailable",
            summary="模型诊断不可用；确定性失败报告仍然完整有效。",
        )
    return report.model_copy(update={"model_diagnostic": diagnostic})


__all__ = ["append_model_diagnostic"]
