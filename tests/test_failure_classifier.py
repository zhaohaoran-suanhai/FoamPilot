from __future__ import annotations

import pytest
from datetime import datetime, timezone

from foampilot.agent.failure import (
    FailureClassificationError,
    NativeFailureClassification,
    classify_native_failure,
    validate_failure_classification,
)
from foampilot.inspection import InspectionIssue, InspectionReport
from foampilot.evidence import NativeErrorFact, RawCommandEvidence, RunFacts
from foampilot.validation.models import (
    PublicValidationCheck,
    PublicValidationReport,
)
from foampilot.workflow import FailureDomain, FailureRecord

from tests.test_execution_plan import valid_plan


_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
_SHA = "a" * 64


def _facts(
    *,
    error_code: str | None = None,
    subject: str | None = None,
    path: str | None = None,
    step_id: str = "solve-a",
) -> RunFacts:
    step = RawCommandEvidence(
        step_id=step_id,
        stage="solve",
        executable="icoFoam",
        argv=("icoFoam",),
        return_code=1,
        started_at=_NOW,
        finished_at=_NOW,
        elapsed_seconds=0,
        timed_out=False,
        stdout_path="logs/solve.out",
        stderr_path="logs/solve.err",
        stdout_sha256=_SHA,
        stderr_sha256=_SHA,
        execution_backend="host",
    )
    return RunFacts(
        run_id="run-failed",
        attempt=1,
        plan_sha256=_SHA,
        extractor_identities={"foundation-10": "1.0.0/protocol-1"},
        raw_steps=(step,),
        native_errors=(
            (
                NativeErrorFact(
                    step_id=step_id,
                    code=error_code,
                    detail=error_code,
                    subject=subject,
                    path=path,
                ),
            )
            if error_code is not None
            else ()
        ),
        source_sha256={"logs/solve.out": _SHA, "logs/solve.err": _SHA},
    )


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
        run_facts=_facts(),
        inspection=inspection,
    )

    assert result.domain == FailureDomain.INSPECTION
    assert result.code == "unsupported_of10_function_object"
    assert result.confidence == "high"
    assert result.scope_hints.files == ["system/controlDict"]
    assert result.allowed_operations == ["replace_file"]


@pytest.mark.parametrize(
    ("error_code", "subject", "fact_path", "code", "path", "block"),
    [
        (
            "MISSING_DICTIONARY_KEYWORD",
            "div(phi,K)",
            "system/fvSchemes",
            "missing_dictionary_keyword",
            "system/fvSchemes",
            "divSchemes",
        ),
        (
            "MISSING_DICTIONARY_KEYWORD",
            "simulationType",
            "constant/thermophysicalTransport",
            "missing_dictionary_keyword",
            "constant/thermophysicalTransport",
            None,
        ),
        (
            "MISSING_DICTIONARY_KEYWORD",
            "dimensions",
            "constant/pRef",
            "missing_dictionary_keyword",
            "constant/pRef",
            None,
        ),
        (
            "MISSING_REGISTRY_OBJECT",
            "thermo:rho",
            None,
            "missing_registry_object",
            "0/p_rgh",
            None,
        ),
        (
            "DIMENSION_MISMATCH",
            None,
            None,
            "dimension_mismatch",
            "constant/physicalProperties",
            None,
        ),
        (
            "MISSING_CASE_FILE",
            None,
            "constant/physicalProperties.water",
            "missing_case_file",
            "constant/physicalProperties.water",
            None,
        ),
    ],
)
def test_classifies_common_solver_failures_without_model_call(
    error_code: str,
    subject: str | None,
    fact_path: str | None,
    code: str,
    path: str,
    block: str | None,
) -> None:
    result = classify_native_failure(
        report=_report("SOLVER_FAILED"),
        plan=valid_plan(),
        run_facts=_facts(
            error_code=error_code,
            subject=subject,
            path=fact_path,
        ),
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
        run_facts=_facts(),
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
        run_facts=_facts(),
    )
    extra = classify_native_failure(
        report=_report(
            "POSTPROCESS_FAILED",
            detail="unsupported optional command post is not required",
            step_id="post",
        ),
        plan=valid_plan(),
        run_facts=_facts(error_code="INVALID_OPTION"),
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
        run_facts=_facts(
            error_code="MISSING_CASE_FILE",
            path="constant/polyMesh/points",
        ),
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
        run_facts=_facts(error_code="INVALID_OPTION", step_id="optional-post"),
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
