from __future__ import annotations

import pytest

from foampilot.agent.failure import (
    FailureClassificationError,
    NativeFailureClassification,
    classify_native_failure,
    validate_failure_classification,
)
from foampilot.inspection import InspectionIssue, InspectionReport
from foampilot.validation.models import (
    PublicValidationCheck,
    PublicValidationReport,
)
from foampilot.workflow import FailureDomain, FailureRecord

from tests.test_execution_plan import valid_plan


def _report(
    layer: str,
    *,
    detail: str = "native execution failed",
    step_id: str | None = "solve-a",
) -> PublicValidationReport:
    return PublicValidationReport.model_validate(
        {
            "checks": [
                {
                    "name": "completion",
                    "passed": False,
                    "detail": detail,
                }
            ],
            "failure_layer": layer,
            "failed_step_id": step_id,
        }
    )


def test_classifies_static_inspection_with_exact_path() -> None:
    inspection = InspectionReport(
        issues=[
            InspectionIssue(
                code="UNSUPPORTED_OF10_FUNCTION_OBJECT",
                path="system/controlDict",
                detail="fieldMinMax is unavailable",
            )
        ]
    )

    result = classify_native_failure(
        report=_report("STATIC_INSPECTION_FAILED", step_id=None),
        plan=valid_plan(),
        log_tail="UNSUPPORTED_OF10_FUNCTION_OBJECT",
        inspection=inspection,
    )

    assert result.domain == FailureDomain.INSPECTION
    assert result.code == "unsupported_of10_function_object"
    assert result.confidence == "high"
    assert result.scope_hints.files == ["system/controlDict"]
    assert result.allowed_operations == ["replace_file"]


@pytest.mark.parametrize(
    ("log_tail", "code", "path", "block"),
    [
        (
            "FOAM FATAL IO ERROR: keyword div(phi,K) is undefined in "
            "dictionary /case/system/fvSchemes",
            "missing_dictionary_keyword",
            "system/fvSchemes",
            "divSchemes",
        ),
        (
            "FOAM FATAL IO ERROR: keyword simulationType is undefined in "
            "dictionary /case/constant/thermophysicalTransport",
            "missing_dictionary_keyword",
            "constant/thermophysicalTransport",
            None,
        ),
        (
            "[14] FOAM FATAL IO ERROR: keyword dimensions is undefined in "
            "dictionary //case/constant/pRef[14]",
            "missing_dictionary_keyword",
            "constant/pRef",
            None,
        ),
        (
            "FOAM FATAL ERROR: cannot find object thermo:rho in database",
            "missing_registry_object",
            "0/p_rgh",
            None,
        ),
        (
            "FOAM FATAL ERROR: Different dimensions for '(a + b)'",
            "dimension_mismatch",
            "constant/physicalProperties",
            None,
        ),
        (
            "FOAM FATAL ERROR: cannot find constant/physicalProperties.water",
            "missing_case_file",
            "constant/physicalProperties.water",
            None,
        ),
    ],
)
def test_classifies_common_solver_failures_without_model_call(
    log_tail: str,
    code: str,
    path: str,
    block: str | None,
) -> None:
    result = classify_native_failure(
        report=_report("SOLVER_FAILED"),
        plan=valid_plan(),
        log_tail=log_tail,
    )

    assert result.domain == FailureDomain.SOLVER
    assert result.code == code
    assert result.confidence == "high"
    assert path in result.scope_hints.files
    assert result.scope_hints.dictionary_blocks == (
        [block] if block is not None else []
    )
    assert result.failed_step_id == "solve-a"


@pytest.mark.parametrize(
    ("layer", "domain", "stage"),
    [
        ("MESH_FAILED", FailureDomain.MESH, "mesh"),
        ("MESH_QUALITY_FAILED", FailureDomain.MESH, "check"),
        ("INITIALIZATION_FAILED", FailureDomain.INITIALIZATION, "initialize"),
        ("SOLVER_FAILED", FailureDomain.SOLVER, "solve"),
        ("POSTPROCESS_FAILED", FailureDomain.POSTPROCESS, "postprocess"),
        ("PUBLIC_VALIDATION_FAILED", FailureDomain.VALIDATION, None),
    ],
)
def test_unknown_failure_keeps_public_layer_without_guessing(
    layer: str,
    domain: FailureDomain,
    stage: str | None,
) -> None:
    result = classify_native_failure(
        report=_report(layer, detail="unrecognized evidence"),
        plan=valid_plan(),
        log_tail="unrecognized evidence",
    )

    assert result.domain == domain
    assert result.code == "unclassified_native_failure"
    assert result.confidence == "low"
    assert result.failed_stage == stage


def test_classifies_missing_and_extra_typed_commands() -> None:
    missing = classify_native_failure(
        report=_report(
            "INITIALIZATION_FAILED",
            detail="required initialization command setFields is missing",
            step_id=None,
        ),
        plan=valid_plan(),
        log_tail="required initialization command setFields is missing",
    )
    extra = classify_native_failure(
        report=_report(
            "POSTPROCESS_FAILED",
            detail="unsupported optional command post is not required",
            step_id="post",
        ),
        plan=valid_plan(),
        log_tail="unsupported optional command post is not required",
    )

    assert missing.code == "missing_typed_command"
    assert missing.allowed_operations == [
        "insert_command_before",
        "insert_command_after",
    ]
    assert extra.code == "unsupported_typed_command"
    assert extra.allowed_operations == ["remove_command"]


def test_classifies_missing_mesh_command_from_native_mesh_lookup() -> None:
    plan = valid_plan().model_copy(deep=True)
    plan.commands = [
        command for command in plan.commands if command.stage != "mesh"
    ]

    result = classify_native_failure(
        report=_report("SOLVER_FAILED", step_id="solve-a"),
        plan=plan,
        log_tail="cannot find file /case/constant/polyMesh/points",
    )

    assert result.code == "missing_typed_command"
    assert result.scope_hints.files == []
    assert result.allowed_operations == [
        "insert_command_before",
        "insert_command_after",
    ]


def test_classifies_invalid_postprocess_option_as_removable_command() -> None:
    plan = valid_plan().model_copy(deep=True)
    plan.commands.append(
        plan.commands[-1].model_copy(
            update={
                "step_id": "optional-post",
                "stage": "postprocess",
                "executable": "checkMesh",
                "args": ["-notARealOption"],
            }
        )
    )

    result = classify_native_failure(
        report=_report("POSTPROCESS_FAILED", step_id="optional-post"),
        plan=plan,
        log_tail="Invalid option: -notARealOption",
    )

    assert result.code == "unsupported_typed_command"
    assert result.allowed_operations == ["remove_command"]


def test_classification_validation_rejects_domain_conflict() -> None:
    report = _report("MESH_FAILED", step_id="mesh")
    conflicting = NativeFailureClassification.model_validate(
        {
            "schema_version": 1,
            "domain": "solver",
            "code": "unclassified_native_failure",
            "confidence": "low",
            "failed_stage": "solve",
            "failed_step_id": "mesh",
            "evidence": [{"kind": "public_report", "value": "failed"}],
            "scope_hints": {
                "files": [],
                "dictionary_blocks": [],
                "commands": ["mesh"],
            },
            "allowed_operations": ["replace_file"],
        }
    )

    with pytest.raises(FailureClassificationError) as captured:
        validate_failure_classification(conflicting, report=report)

    assert captured.value.code == "FAILURE_CLASSIFICATION_INVALID"
    assert captured.value.message == "失败分类与原始公开证据不一致。"
