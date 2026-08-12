"""Deterministic classification of public native-execution failures."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.inspection import InspectionReport
from foampilot.plans import ExecutionPlan
from foampilot.validation.models import PublicValidationReport
from foampilot.workflow import FailureDomain, FailureRecord


Confidence = Literal["high", "medium", "low"]
RepairOperationName = Literal[
    "add_file",
    "replace_file",
    "insert_command_before",
    "insert_command_after",
    "replace_command",
    "remove_command",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureEvidence(StrictModel):
    kind: Literal[
        "inspection_issue",
        "log_pattern",
        "public_report",
        "prior_failure",
    ]
    value: str = Field(min_length=1)


class FailureScopeHints(StrictModel):
    files: list[str] = Field(default_factory=list)
    dictionary_blocks: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


class NativeFailureClassification(StrictModel):
    schema_version: Literal[1] = 1
    domain: FailureDomain
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    confidence: Confidence
    failed_stage: Literal[
        "mesh",
        "check",
        "initialize",
        "solve",
        "postprocess",
    ] | None
    failed_step_id: str | None
    evidence: list[FailureEvidence] = Field(min_length=1)
    scope_hints: FailureScopeHints
    allowed_operations: list[RepairOperationName] = Field(min_length=1)


class FailureClassificationError(ValueError):
    code = "FAILURE_CLASSIFICATION_INVALID"
    message = "失败分类与原始公开证据不一致。"
    recovery = "请保留原始失败层和命令阶段，并重新执行确定性分类。"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


_LAYER_FACTS: dict[str, tuple[FailureDomain, str | None]] = {
    "REQUEST_INCOMPLETE": (FailureDomain.TASK, None),
    "ENVIRONMENT_BLOCKED": (FailureDomain.ENVIRONMENT, None),
    "PLAN_INVALID": (FailureDomain.PLAN, None),
    "CASE_GENERATION_FAILED": (FailureDomain.CASE, None),
    "STATIC_INSPECTION_FAILED": (FailureDomain.INSPECTION, None),
    "MESH_FAILED": (FailureDomain.MESH, "mesh"),
    "MESH_QUALITY_FAILED": (FailureDomain.MESH, "check"),
    "INITIALIZATION_FAILED": (FailureDomain.INITIALIZATION, "initialize"),
    "SOLVER_FAILED": (FailureDomain.SOLVER, "solve"),
    "POSTPROCESS_FAILED": (FailureDomain.POSTPROCESS, "postprocess"),
    "PUBLIC_VALIDATION_FAILED": (FailureDomain.VALIDATION, None),
}

_CASE_PATH = re.compile(
    r"(?:^|\s)(?:/[^\s:]*/case/|/case/)"
    r"((?:0|constant|system)/[^\s:,;\[\]]+)"
)
_MISSING_KEYWORD = re.compile(
    r"\bkeyword\s+([^\s]+)\s+(?:is\s+)?(?:undefined|not\s+found)",
    re.IGNORECASE,
)
_MISSING_OBJECT = re.compile(
    r"(?:cannot\s+find|could\s+not\s+find|unknown)\s+"
    r"(?:object|field)\s+([^\s,;]+)",
    re.IGNORECASE,
)
_MISSING_CASE_FILE = re.compile(
    r"(?:cannot\s+find|could\s+not\s+find|no\s+such\s+file)\s+"
    r"(?:file\s+)?((?:0|constant|system)/[^\s,;]+)",
    re.IGNORECASE,
)


def _base_facts(
    report: PublicValidationReport,
) -> tuple[FailureDomain, str | None]:
    if report.failure_layer is None:
        raise FailureClassificationError("passing report cannot be classified")
    return _LAYER_FACTS[report.failure_layer]


def _command_stage(
    plan: ExecutionPlan,
    step_id: str | None,
    fallback: str | None,
) -> str | None:
    if step_id is None:
        return fallback
    command = next(
        (item for item in plan.commands if item.step_id == step_id),
        None,
    )
    return command.stage.value if command is not None else fallback


def _path_from_log(log_tail: str) -> str | None:
    match = _CASE_PATH.search(log_tail)
    if match is None:
        return None
    return match.group(1).rstrip(".\")'")


def _keyword_hints(
    keyword: str,
    log_tail: str,
) -> tuple[list[str], list[str]]:
    explicit_path = _path_from_log(log_tail)
    if explicit_path is not None:
        files = [explicit_path]
    elif keyword.startswith("div(") or keyword.startswith("grad("):
        files = ["system/fvSchemes"]
    elif keyword in {"simulationType", "model"}:
        files = ["constant/thermophysicalTransport"]
    else:
        files = ["system/fvSolution", "system/fvSchemes"]
    blocks: list[str] = []
    if keyword.startswith("div("):
        blocks = ["divSchemes"]
    elif keyword.startswith("grad("):
        blocks = ["gradSchemes"]
    elif keyword in {"solver", "preconditioner", "smoother"}:
        blocks = ["solvers"]
    return files, blocks


def _resolve_declared_paths(
    files: list[str],
    plan: ExecutionPlan,
) -> list[str]:
    declared = sorted(
        (item.path for item in plan.files),
        key=len,
        reverse=True,
    )
    resolved: list[str] = []
    for candidate in files:
        match = next(
            (
                path
                for path in declared
                if candidate == path or candidate.startswith(path + "/")
            ),
            candidate,
        )
        resolved.append(match)
    return list(dict.fromkeys(resolved))


def validate_failure_classification(
    classification: NativeFailureClassification,
    *,
    report: PublicValidationReport,
) -> None:
    domain, stage = _base_facts(report)
    if classification.domain != domain:
        raise FailureClassificationError(
            "classification domain conflicts with public failure layer"
        )
    if stage is not None and classification.failed_stage != stage:
        raise FailureClassificationError(
            "classification stage conflicts with public failure layer"
        )
    if (
        report.failed_step_id is not None
        and classification.failed_step_id != report.failed_step_id
    ):
        raise FailureClassificationError(
            "classification step conflicts with public report"
        )


def classify_native_failure(
    *,
    report: PublicValidationReport,
    plan: ExecutionPlan,
    log_tail: str,
    inspection: InspectionReport | None = None,
    prior_failure: FailureRecord | None = None,
) -> NativeFailureClassification:
    """Classify one failure using public deterministic evidence only."""

    domain, default_stage = _base_facts(report)
    # The evaluator-owned failure layer is the canonical stage fact. A stale
    # or broad failed_step_id must not silently relabel mesh/initialization as
    # solver failure.
    failed_stage = default_stage
    commands = (
        [report.failed_step_id]
        if report.failed_step_id is not None
        else []
    )
    evidence: list[FailureEvidence] = []
    if prior_failure is not None:
        evidence.append(
            FailureEvidence(
                kind="prior_failure",
                value=f"{prior_failure.domain.value}:{prior_failure.code}",
            )
        )

    if inspection is not None and inspection.issues:
        issue = inspection.issues[0]
        result = NativeFailureClassification(
            domain=FailureDomain.INSPECTION,
            code=issue.code.lower(),
            confidence="high",
            failed_stage=None,
            failed_step_id=report.failed_step_id,
            evidence=[
                *evidence,
                FailureEvidence(
                    kind="inspection_issue",
                    value=f"{issue.code}:{issue.path or '<case>'}",
                ),
            ],
            scope_hints=FailureScopeHints(
                files=[issue.path] if issue.path is not None else [],
                commands=commands,
            ),
            allowed_operations=["replace_file"],
        )
        validate_failure_classification(result, report=report)
        return result

    combined = report.feedback() + "\n" + log_tail[-12000:]
    lowered = combined.lower()
    keyword_match = _MISSING_KEYWORD.search(combined)
    has_mesh_command = any(
        command.stage.value == "mesh" for command in plan.commands
    )
    missing_poly_mesh = (
        "constant/polymesh" in lowered
        or (
            'cannot find file "points"' in lowered
            and 'directory "polymesh"' in lowered
        )
    )
    if missing_poly_mesh and not has_mesh_command:
        code = "missing_typed_command"
        files, blocks = [], []
        allowed = ["insert_command_before", "insert_command_after"]
    elif keyword_match is not None:
        keyword = keyword_match.group(1).rstrip(".:,;")
        files, blocks = _keyword_hints(keyword, combined)
        code = "missing_dictionary_keyword"
        allowed: list[RepairOperationName] = ["replace_file"]
    elif (
        "unknown function type" in lowered
        or "unknown function object" in lowered
    ):
        code = "unknown_function_object_type"
        files, blocks = ["system/controlDict"], ["functions"]
        allowed = ["replace_file"]
    elif (
        "required initialization command" in lowered
        and "missing" in lowered
    ):
        code = "missing_typed_command"
        files, blocks = [], []
        allowed = ["insert_command_before", "insert_command_after"]
    elif (
        "unsupported optional command" in lowered
        or ("command" in lowered and "is not required" in lowered)
        or (
            (
                failed_stage == "postprocess"
                or (report.failed_step_id or "").startswith("optional")
            )
            and any(
                marker in lowered
                for marker in (
                    "invalid option",
                    "unknown option",
                    "unrecognized option",
                )
            )
        )
    ):
        code = "unsupported_typed_command"
        files, blocks = [], []
        allowed = ["remove_command"]
    elif (file_match := _MISSING_CASE_FILE.search(combined)) is not None:
        code = "missing_case_file"
        files = [file_match.group(1).rstrip(".:,;\")'")]
        blocks = []
        allowed = ["add_file"]
    elif (object_match := _MISSING_OBJECT.search(combined)) is not None:
        object_name = object_match.group(1).rstrip(".:,;")
        code = "missing_registry_object"
        files = (
            ["0/p_rgh"]
            if object_name in {"rho", "thermo:rho"}
            else [f"0/{object_name}"]
        )
        blocks = []
        allowed = ["add_file", "replace_file"]
    elif (
        "different dimensions" in lowered
        or "inconsistent dimensions" in lowered
        or "dimension mismatch" in lowered
    ):
        code = "dimension_mismatch"
        files = [
            _path_from_log(combined)
            or "constant/physicalProperties"
        ]
        blocks = []
        allowed = ["replace_file"]
    elif re.search(
        r"(?:\bnan\b|\binf(?:inity)?\b|floating point exception|courant number)",
        lowered,
    ):
        code = "numerical_instability"
        files = ["system/controlDict"]
        blocks = []
        allowed = ["replace_file"]
    else:
        result = NativeFailureClassification(
            domain=domain,
            code="unclassified_native_failure",
            confidence="low",
            failed_stage=failed_stage,
            failed_step_id=report.failed_step_id,
            evidence=[
                *evidence,
                FailureEvidence(
                    kind="public_report",
                    value=(report.feedback() or report.failure_layer)[:500],
                ),
            ],
            scope_hints=FailureScopeHints(commands=commands),
            allowed_operations=["replace_file"],
        )
        validate_failure_classification(result, report=report)
        return result

    files = _resolve_declared_paths(files, plan)
    result = NativeFailureClassification(
        domain=domain,
        code=code,
        confidence="high",
        failed_stage=failed_stage,
        failed_step_id=report.failed_step_id,
        evidence=[
            *evidence,
            FailureEvidence(
                kind="log_pattern",
                value=code,
            ),
        ],
        scope_hints=FailureScopeHints(
            files=files,
            dictionary_blocks=blocks,
            commands=commands,
        ),
        allowed_operations=allowed,
    )
    validate_failure_classification(result, report=report)
    return result


__all__ = [
    "FailureClassificationError",
    "FailureEvidence",
    "FailureScopeHints",
    "NativeFailureClassification",
    "classify_native_failure",
    "validate_failure_classification",
]
