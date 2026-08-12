"""Deterministic classification of public native-execution failures."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.inspection import InspectionReport
from foampilot.evidence import NativeErrorFact, RunAssessment, RunFacts
from foampilot.plans import ExecutionPlan
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
        "run_fact",
        "run_assessment",
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
    "ENVIRONMENT_BLOCKED": (FailureDomain.ENVIRONMENT, None),
    "STATIC_INSPECTION_FAILED": (FailureDomain.INSPECTION, None),
    "MESH_FAILED": (FailureDomain.MESH, "mesh"),
    "MESH_QUALITY_FAILED": (FailureDomain.MESH, "check"),
    "INITIALIZATION_FAILED": (FailureDomain.INITIALIZATION, "initialize"),
    "SOLVER_FAILED": (FailureDomain.SOLVER, "solve"),
    "POSTPROCESS_FAILED": (FailureDomain.POSTPROCESS, "postprocess"),
}

def _base_facts(
    report: RunAssessment,
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


def _keyword_hints(
    keyword: str,
    explicit_path: str | None,
) -> tuple[list[str], list[str]]:
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
    report: RunAssessment,
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
            "classification step conflicts with run assessment"
        )


def classify_native_failure(
    *,
    report: RunAssessment,
    plan: ExecutionPlan,
    run_facts: RunFacts,
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

    errors = list(run_facts.native_errors)
    by_code: dict[str, NativeErrorFact] = {
        item.code: item for item in errors
    }
    has_mesh_command = any(
        command.stage.value == "mesh" for command in plan.commands
    )
    missing_poly_mesh = (
        (missing := by_code.get("MISSING_CASE_FILE")) is not None
        and missing.path is not None
        and missing.path.casefold().startswith("constant/polymesh/")
    )
    if missing_poly_mesh and not has_mesh_command:
        code = "missing_typed_command"
        files, blocks = [], []
        allowed = ["insert_command_before", "insert_command_after"]
    elif (keyword_fact := by_code.get("MISSING_DICTIONARY_KEYWORD")) is not None:
        keyword = keyword_fact.subject or "unknown"
        files, blocks = _keyword_hints(keyword, keyword_fact.path)
        code = "missing_dictionary_keyword"
        allowed: list[RepairOperationName] = ["replace_file"]
    elif (
        "UNKNOWN_FUNCTION_OBJECT_TYPE" in by_code
    ):
        code = "unknown_function_object_type"
        files, blocks = ["system/controlDict"], ["functions"]
        allowed = ["replace_file"]
    elif (
        report.failed_step_id is None
        and report.failure_layer == "INITIALIZATION_FAILED"
    ):
        code = "missing_typed_command"
        files, blocks = [], []
        allowed = ["insert_command_before", "insert_command_after"]
    elif (
        "INVALID_OPTION" in by_code
        and (
            failed_stage == "postprocess"
            or (report.failed_step_id or "").startswith("optional")
        )
    ):
        code = "unsupported_typed_command"
        files, blocks = [], []
        allowed = ["remove_command"]
    elif (file_fact := by_code.get("MISSING_CASE_FILE")) is not None:
        code = "missing_case_file"
        files = [file_fact.path] if file_fact.path is not None else []
        blocks = []
        allowed = ["add_file"]
    elif (object_fact := by_code.get("MISSING_REGISTRY_OBJECT")) is not None:
        object_name = object_fact.subject or "unknown"
        code = "missing_registry_object"
        files = (
            ["0/p_rgh"]
            if object_name in {"rho", "thermo:rho"}
            else [f"0/{object_name}"]
        )
        blocks = []
        allowed = ["add_file", "replace_file"]
    elif (
        "DIMENSION_MISMATCH" in by_code
    ):
        code = "dimension_mismatch"
        files = [
            by_code["DIMENSION_MISMATCH"].path
            or "constant/physicalProperties"
        ]
        blocks = []
        allowed = ["replace_file"]
    elif any(
        item.code in {"NON_FINITE_VALUE", "FLOATING_POINT_EXCEPTION"}
        for item in errors
    ) or any(item.maximum > 1 for item in run_facts.courant):
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
                    kind="run_assessment",
                    value=report.detail[:500],
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
                kind="run_fact",
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
