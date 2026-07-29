"""Evidence-scoped repairs for native typed execution plans."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.models import (
    ModelClient,
    ModelRequest,
    generate_with_retry,
)
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    validate_execution_plan,
)
from foampilot.tasks import TaskSpec
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
    plan_issues = validate_execution_plan(
        revised,
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
    client: ModelClient,
) -> RepairDecision:
    payload: dict[str, Any] = {
        "task": task.agent_payload(),
        "plan": plan.model_dump(mode="json"),
        "failed_public_report": report.model_dump(mode="json"),
        "failed_step_log": failed_log[-12000:],
        "current_declared_files": current_files,
        "dynamic_public_knowledge": knowledge_text,
        "portable_workflow_skill": skills_text,
        "repair_contract": (
            "Because EVIDENCE indicates CAUSE, change or add only safe "
            "case-relative generated files, or change existing typed "
            "commands; name the expected check and one stable control."
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
    return generate_with_retry(
        client,
        ModelRequest(
            purpose="repair-openfoam-attempt",
            system_prompt=(
                "Propose one minimal evidence-scoped OpenFOAM repair. "
                "Follow the supplied public knowledge and workflow Skill. "
                "Do not access tutorials, private evaluators, or golden data."
            ),
            user_prompt=user_prompt,
        ),
        RepairDecision,
    )
