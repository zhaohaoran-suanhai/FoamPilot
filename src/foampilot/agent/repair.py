"""Evidence-scoped repairs for native typed execution plans."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.tasks import TaskSpec
from foampilot.preprocessing import GeometryFacts, MeshQualityReport
from foampilot.validation.models import (
    PublicValidationReport,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairDecision(StrictModel):
    because: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    cause: str = Field(min_length=1)
    changed_files: list[GeneratedFile] = Field(default_factory=list)
    changed_commands: list[NativeCommand] = Field(default_factory=list)
    expected_check: str = Field(min_length=1)
    stable_control: str = Field(min_length=1)


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


class RepairIssue(StrictModel):
    code: str
    detail: str


_MESH_FAILURES = {"MESH_FAILED", "MESH_QUALITY_FAILED"}
_MESH_DICTIONARIES = {
    "system/blockMeshDict",
    "system/snappyHexMeshDict",
    "system/surfaceFeatureExtractDict",
    "system/meshQualityDict",
    "system/topoSetDict",
    "system/decomposeParDict",
}


def _is_mesh_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (
        path in _MESH_DICTIONARIES
        or parsed.suffix in {".geo", ".msh"}
        or parsed.name in {"blockMeshDict", "snappyHexMeshDict"}
    )


def _is_initial_field(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2:
        return False
    try:
        return float(parts[0]) == 0.0
    except ValueError:
        return False


def _named_block_span(text: str, name: str) -> tuple[int, int] | None:
    match = re.search(rf"\b{re.escape(name)}\b", text)
    if match is None:
        return None
    opening = text.find("{", match.end())
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return opening, index
    return None


def _boundary_only_change(previous: str, revised: str) -> bool:
    old_span = _named_block_span(previous, "boundaryField")
    new_span = _named_block_span(revised, "boundaryField")
    if old_span is None or new_span is None:
        return False
    old_without = previous[: old_span[0]] + previous[old_span[1] + 1 :]
    new_without = revised[: new_span[0]] + revised[new_span[1] + 1 :]
    return old_without == new_without


def _mesh_prompt_files(current_files: dict[str, str]) -> dict[str, str]:
    return {
        path: content
        for path, content in current_files.items()
        if _is_mesh_path(path) or _is_initial_field(path)
    }


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
    decision: RepairDecision | None = None,
    environment_failure: bool = False,
) -> RepairStop:
    if environment_failure:
        return RepairStop(stop=True, reason="ENVIRONMENT_FAILURE")
    if attempts_used >= max_attempts:
        return RepairStop(stop=True, reason="BUDGET_EXHAUSTED")
    if (
        len(fingerprints) >= 2
        and fingerprints[-1] == fingerprints[-2]
    ):
        return RepairStop(stop=True, reason="REPEATED_FAILURE")
    if (
        decision is not None
        and not decision.changed_files
        and not decision.changed_commands
    ):
        return RepairStop(stop=True, reason="NO_OP")
    if decision is not None and not generated_bytes_changed:
        return RepairStop(stop=True, reason="UNCHANGED_BYTES")
    return RepairStop(stop=False, reason="CONTINUE")


def validate_repair_decision(
    decision: RepairDecision,
    *,
    task: TaskSpec,
    plan: ExecutionPlan,
    report: PublicValidationReport | None = None,
    available_executables: set[str],
    current_files: dict[str, str],
) -> list[RepairIssue]:
    issues: list[RepairIssue] = []
    public_assets = {item.path for item in task.public_assets}
    changed_paths = [item.path for item in decision.changed_files]
    if len(changed_paths) != len(set(changed_paths)):
        issues.append(
            RepairIssue(
                code="DUPLICATE_REPAIR_FILE",
                detail="repair file paths must be unique",
            )
        )
    mesh_scoped = (
        report is not None and report.failure_layer in _MESH_FAILURES
    )
    mesh_paths_changed = any(_is_mesh_path(path) for path in changed_paths)
    patch_evidence = " ".join(
        (
            decision.cause,
            *decision.evidence,
            decision.stable_control,
        )
    ).lower()
    for generated in decision.changed_files:
        if generated.path in public_assets:
            issues.append(
                RepairIssue(
                    code="PUBLIC_ASSET_REPAIR",
                    detail="repair must not change a public asset",
                )
            )
        if current_files.get(generated.path) == generated.content:
            issues.append(
                RepairIssue(
                    code="NO_OP_REPAIR_FILE",
                    detail=f"repair leaves {generated.path} unchanged",
                )
            )
        if any(
            protected in generated.content
            for protected in task.protected_paths
        ):
            issues.append(
                RepairIssue(
                    code="PROTECTED_REPAIR_REFERENCE",
                    detail="repair content references a protected path",
                )
            )
        if mesh_scoped and not _is_mesh_path(generated.path):
            if not _is_initial_field(generated.path):
                issues.append(
                    RepairIssue(
                        code="MESH_REPAIR_UNRELATED_FILE",
                        detail=(
                            "mesh-scoped repair cannot change unrelated file "
                            f"{generated.path}"
                        ),
                    )
                )
            elif not (
                mesh_paths_changed
                and any(term in patch_evidence for term in ("patch", "boundary"))
                and (previous := current_files.get(generated.path)) is not None
                and _boundary_only_change(previous, generated.content)
            ):
                issues.append(
                    RepairIssue(
                        code="MESH_REPAIR_FIELD_SCOPE",
                        detail=(
                            "initial fields may change only inside boundaryField "
                            "for evidenced patch synchronization"
                        ),
                    )
                )

    command_by_step = {item.step_id: item for item in plan.commands}
    changed_steps = [item.step_id for item in decision.changed_commands]
    if len(changed_steps) != len(set(changed_steps)):
        issues.append(
            RepairIssue(
                code="DUPLICATE_REPAIR_STEP",
                detail="repair command step IDs must be unique",
            )
        )
    revised_commands = list(plan.commands)
    index_by_step = {
        command.step_id: index
        for index, command in enumerate(revised_commands)
    }
    for command in decision.changed_commands:
        if command.step_id not in command_by_step:
            issues.append(
                RepairIssue(
                    code="UNKNOWN_REPAIR_STEP",
                    detail=f"repair changes unknown step {command.step_id}",
                )
            )
            continue
        if mesh_scoped and command.stage not in {"mesh", "check"}:
            issues.append(
                RepairIssue(
                    code="MESH_REPAIR_UNRELATED_COMMAND",
                    detail=(
                        "mesh-scoped repair cannot change command "
                        f"{command.step_id} at stage {command.stage}"
                    ),
                )
            )
        if command == command_by_step[command.step_id]:
            issues.append(
                RepairIssue(
                    code="NO_OP_REPAIR_COMMAND",
                    detail=f"repair leaves step {command.step_id} unchanged",
                )
            )
        revised_commands[index_by_step[command.step_id]] = command

    revised_files = {item.path: item for item in plan.files}
    added_paths: list[str] = []
    for generated in decision.changed_files:
        if generated.path not in revised_files:
            added_paths.append(generated.path)
        revised_files[generated.path] = generated
    revised = plan.model_copy(
        update={
            "files": [
                revised_files[item.path] for item in plan.files
            ]
            + [revised_files[path] for path in added_paths],
            "commands": revised_commands,
        }
    )
    normalized = normalize_execution_plan(
        revised,
        task,
        available_executables,
    )
    plan_issues = validate_execution_plan(
        normalized.plan,
        task,
        available_executables,
    )
    if plan_issues:
        issues.append(
            RepairIssue(
                code="INVALID_REPAIR_PLAN",
                detail=", ".join(
                    f"{item.code}@{item.location}" for item in plan_issues
                ),
            )
        )
    return issues


def request_repair(
    *,
    task: TaskSpec,
    plan: ExecutionPlan,
    report: PublicValidationReport,
    failed_log: str,
    current_files: dict[str, str],
    knowledge_text: str,
    skills_text: str,
    geometry_facts: GeometryFacts | None = None,
    mesh_quality_report: MeshQualityReport | None = None,
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> RepairDecision:
    mesh_scoped = report.failure_layer in _MESH_FAILURES
    scoped_files = (
        _mesh_prompt_files(current_files)
        if mesh_scoped
        else current_files
    )
    plan_payload = plan.model_dump(mode="json")
    if mesh_scoped:
        plan_payload["files"] = [
            item.model_dump(mode="json")
            for item in plan.files
            if item.path in scoped_files or _is_mesh_path(item.path)
        ]
    payload: dict[str, Any] = {
        "task": task.agent_payload(),
        "plan": plan_payload,
        "failed_public_report": report.model_dump(mode="json"),
        "failed_step_log": failed_log[-12000:],
        "current_declared_files": scoped_files,
        "dynamic_public_knowledge": knowledge_text,
        "portable_workflow_skill": skills_text,
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
            "因为 EVIDENCE 指向 CAUSE，只能修改或新增安全的算例相对路径生成文件，"
            "或修改已有 typed command；同时说明预期检查和一个保持不变的 control。"
            + (
                " 当前是 mesh-scoped repair：只能修改 mesh/check 命令和网格文件；"
                "仅在 patch 同步有直接证据时，可只改初始场 boundaryField。"
                if mesh_scoped
                else ""
            )
        ),
    }
    user_prompt = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if any(
        protected in user_prompt for protected in task.protected_paths
    ):
        raise ValueError("repair prompt contains a protected path")
    return gateway.generate_structured(
        ModelRequest(
            purpose="repair-openfoam-attempt",
            system_prompt=(
                "提出一个由证据限定范围的最小 OpenFOAM repair。遵循提供的公开知识与"
                "工作流 Skill。不得访问 tutorial、私有 evaluator 或 golden data。"
            ),
            user_prompt=user_prompt,
        ),
        RepairDecision,
        budget=budget,
        trace=trace,
    ).value
