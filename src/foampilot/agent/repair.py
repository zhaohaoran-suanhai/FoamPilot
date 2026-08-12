"""Finite repair controls and the scoped RepairPatch model boundary."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from foampilot.models import (
    ModelBudgetWindow,
    ModelContextArtifact,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.plans import ExecutionPlan
from foampilot.preprocessing import GeometryFacts, MeshQualityReport
from foampilot.tasks import TaskSpec
from foampilot.validation.models import PublicValidationReport

from .failure import NativeFailureClassification
from .repair_patch import RepairPatch
from .repair_scope import RepairScope
from .status import AgentStatusSnapshot
from foampilot.repair import RepairProposal


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairStop(StrictModel):
    stop: bool
    reason: Literal[
        "CONTINUE",
        "REPEATED_FAILURE",
        "NO_OP",
        "UNCHANGED_BYTES",
        "BUDGET_EXHAUSTED",
        "ENVIRONMENT_FAILURE",
    ]


def failure_fingerprint(
    report: PublicValidationReport,
    *,
    log_tail: str = "",
) -> str:
    """Hash normalized public evidence and the relevant failed-log tail."""

    normalized_tail = re.sub(r"\s+", " ", log_tail).strip()
    payload = {
        "report": report.model_dump(mode="json"),
        "log_tail": normalized_tail[-4000:],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def should_stop_repair(
    *,
    fingerprints: list[str],
    attempts_used: int,
    max_attempts: int,
    generated_bytes_changed: bool,
    patch_operation_count: int | None = None,
    environment_failure: bool = False,
) -> RepairStop:
    if environment_failure:
        return RepairStop(stop=True, reason="ENVIRONMENT_FAILURE")
    if attempts_used >= max_attempts:
        return RepairStop(stop=True, reason="BUDGET_EXHAUSTED")
    if len(fingerprints) >= 2 and fingerprints[-1] == fingerprints[-2]:
        return RepairStop(stop=True, reason="REPEATED_FAILURE")
    if patch_operation_count == 0:
        return RepairStop(stop=True, reason="NO_OP")
    if patch_operation_count is not None and not generated_bytes_changed:
        return RepairStop(stop=True, reason="UNCHANGED_BYTES")
    return RepairStop(stop=False, reason="CONTINUE")


def request_repair_proposal(
    *,
    task: TaskSpec,
    plan: ExecutionPlan,
    classification: NativeFailureClassification,
    repair_scope: RepairScope,
    failed_log: str,
    knowledge_text: str,
    skills_text: str,
    status_snapshot: AgentStatusSnapshot,
    status_artifact: ModelContextArtifact,
    geometry_facts: GeometryFacts | None = None,
    mesh_quality_report: MeshQualityReport | None = None,
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> RepairProposal:
    """Request one patch using only deterministic scoped public evidence."""

    commands_by_step = {item.step_id: item for item in plan.commands}
    relevant_commands = [
        commands_by_step[step_id].model_dump(mode="json")
        for step_id in repair_scope.relevant_commands
        if step_id in commands_by_step
    ]
    payload: dict[str, Any] = {
        "task": task.agent_payload(),
        "case_manifest": plan.manifest.model_dump(mode="json"),
        "failure_classification": classification.model_dump(mode="json"),
        "repair_scope": repair_scope.model_dump(mode="json"),
        "relevant_typed_commands": relevant_commands,
        "failed_step_log_tail": failed_log[-6000:],
        "dynamic_public_knowledge": knowledge_text,
        "portable_workflow_skill": skills_text,
        "deterministic_agent_status": status_snapshot.model_dump(mode="json"),
        "geometry_facts": (
            geometry_facts.model_dump(mode="json")
            if geometry_facts is not None
            else None
        ),
        "mesh_quality_report": (
            mesh_quality_report.model_dump(mode="json")
            if mesh_quality_report is not None
            else None
        ),
        "repair_contract": (
            "只依据 failure_classification、RepairScope 与冻结 repair envelope 提交"
            "最小 RepairProposal。必须声明每个 DesignChange，并返回与这些声明严格对应"
            "的完整文件替换；不得返回命令、完整 case 或未声明的语义变化。若 scope 只"
            "提供 block/excerpt/metadata，信息不足时不得猜测。"
        ),
    }
    user_prompt = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if any(protected in user_prompt for protected in task.protected_paths):
        raise ValueError("repair prompt contains a protected path")
    return gateway.generate_structured(
        ModelRequest(
            purpose="repair-openfoam-attempt",
            system_prompt=(
                "提出一个由公开失败证据和 RepairScope 限定的最小 OpenFOAM "
                "RepairProposal。不得访问 tutorial、私有 evaluator 或 golden data。"
            ),
            user_prompt=user_prompt,
            context_artifacts=(status_artifact,),
        ),
        RepairProposal,
        budget=budget,
        trace=trace,
    ).value


def request_repair_patch(
    *,
    task: TaskSpec,
    plan: ExecutionPlan,
    classification: NativeFailureClassification,
    repair_scope: RepairScope,
    failed_log: str,
    knowledge_text: str,
    skills_text: str,
    status_snapshot: AgentStatusSnapshot,
    status_artifact: ModelContextArtifact,
    geometry_facts: GeometryFacts | None = None,
    mesh_quality_report: MeshQualityReport | None = None,
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> RepairPatch:
    """Historical Phase-2 patch bridge retained until canonical migration."""

    commands_by_step = {item.step_id: item for item in plan.commands}
    relevant_commands = [
        commands_by_step[step_id].model_dump(mode="json")
        for step_id in repair_scope.relevant_commands
        if step_id in commands_by_step
    ]
    payload: dict[str, Any] = {
        "task": task.agent_payload(),
        "case_manifest": plan.manifest.model_dump(mode="json"),
        "failure_classification": classification.model_dump(mode="json"),
        "repair_scope": repair_scope.model_dump(mode="json"),
        "relevant_typed_commands": relevant_commands,
        "failed_step_log_tail": failed_log[-6000:],
        "dynamic_public_knowledge": knowledge_text,
        "portable_workflow_skill": skills_text,
        "deterministic_agent_status": status_snapshot.model_dump(mode="json"),
        "geometry_facts": (
            geometry_facts.model_dump(mode="json")
            if geometry_facts is not None
            else None
        ),
        "mesh_quality_report": (
            mesh_quality_report.model_dump(mode="json")
            if mesh_quality_report is not None
            else None
        ),
        "repair_contract": (
            "只依据 failure_classification 与 repair_scope 提交最小 RepairPatch。"
            "文件只能 add/replace；命令只能 insert_before、insert_after、replace、"
            "remove。不要返回未变化的命令或文件。替换文件时必须返回完整文件。"
        ),
    }
    user_prompt = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if any(protected in user_prompt for protected in task.protected_paths):
        raise ValueError("repair prompt contains a protected path")
    return gateway.generate_structured(
        ModelRequest(
            purpose="repair-openfoam-attempt-legacy",
            system_prompt=(
                "提出一个由公开失败证据和 RepairScope 限定的最小 OpenFOAM "
                "RepairPatch。不得访问私有 evaluator 或 golden data。"
            ),
            user_prompt=user_prompt,
            context_artifacts=(status_artifact,),
        ),
        RepairPatch,
        budget=budget,
        trace=trace,
    ).value


__all__ = [
    "RepairStop",
    "failure_fingerprint",
    "request_repair_patch",
    "request_repair_proposal",
    "should_stop_repair",
]
