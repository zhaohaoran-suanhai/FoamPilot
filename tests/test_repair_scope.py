from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.agent.failure import NativeFailureClassification
from foampilot.agent.repair_scope import (
    RepairScopeError,
    build_repair_scope,
)

from tests.test_execution_plan import task as task_fixture
from tests.test_execution_plan import valid_plan


def _classification(**overrides) -> NativeFailureClassification:
    payload = {
        "schema_version": 1,
        "domain": "solver",
        "code": "missing_dictionary_keyword",
        "confidence": "high",
        "failed_stage": "solve",
        "failed_step_id": "solve-a",
        "evidence": [
            {
                "kind": "log_pattern",
                "value": "keyword div(phi,U) is undefined",
            }
        ],
        "scope_hints": {
            "files": ["system/fvSchemes"],
            "dictionary_blocks": ["divSchemes"],
            "commands": ["solve-a"],
        },
        "allowed_operations": ["replace_file"],
    }
    payload.update(overrides)
    return NativeFailureClassification.model_validate(payload)


def test_scope_extracts_matching_dictionary_block_and_excludes_others() -> None:
    files = {
        "system/fvSchemes": """FoamFile { class dictionary; }
ddtSchemes { default Euler; }
divSchemes
{
    default none;
    div(phi,U) Gauss upwind;
}
laplacianSchemes { default Gauss linear corrected; }
""",
        "system/controlDict": "application icoFoam;\n",
        "constant/physicalProperties": "nu 1e-6;\n",
    }

    scope = build_repair_scope(
        classification=_classification(),
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        current_files=files,
        knowledge_ids=("of10.ico.contract", "of10.generic.numerics"),
        max_full_file_bytes=1,
    )

    assert len(scope.relevant_files) == 1
    selected = scope.relevant_files[0]
    assert selected.path == "system/fvSchemes"
    assert selected.content_mode == "matching_block"
    assert selected.block == "divSchemes"
    assert "div(phi,U)" in (selected.content or "")
    assert "laplacianSchemes" not in (selected.content or "")
    assert scope.relevant_commands == ["solve-a"]
    assert scope.excluded_file_count == 2
    assert scope.earliest_possible_rerun_stage == "inspection"


def test_large_files_degrade_representation_instead_of_blocking() -> None:
    large = "value 1;\n" * 10000
    huge = "0 " * (600 * 1024)
    classification = _classification(
        code="unclassified_native_failure",
        confidence="low",
        scope_hints={
            "files": ["0/U", "0/U.internal.inc"],
            "dictionary_blocks": [],
            "commands": ["solve-a"],
        },
    )

    scope = build_repair_scope(
        classification=classification,
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        current_files={"0/U": large, "0/U.internal.inc": huge},
        knowledge_ids=(),
        max_full_file_bytes=1024,
        metadata_only_bytes=1024 * 1024,
    )

    by_path = {item.path: item for item in scope.relevant_files}
    assert by_path["0/U"].content_mode == "head_tail_excerpt"
    assert len(by_path["0/U"].content or "") < len(large)
    assert by_path["0/U.internal.inc"].content_mode == "metadata_only"
    assert by_path["0/U.internal.inc"].content is None


def test_mesh_scope_does_not_expose_unrelated_physics() -> None:
    classification = _classification(
        domain="mesh",
        code="unclassified_native_failure",
        confidence="low",
        failed_stage="mesh",
        failed_step_id="mesh-a",
        scope_hints={
            "files": [],
            "dictionary_blocks": [],
            "commands": ["mesh-a"],
        },
        allowed_operations=["replace_file"],
    )
    files = {
        "system/blockMeshDict": "vertices ();\n",
        "system/fvSchemes": "secret-physics-scheme;\n",
        "constant/physicalProperties": "secret-viscosity;\n",
        "0/U": "boundaryField { wall { type noSlip; } }\n",
    }

    scope = build_repair_scope(
        classification=classification,
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        current_files=files,
        knowledge_ids=(),
    )

    serialized = scope.model_dump_json()
    assert "system/blockMeshDict" in serialized
    assert "secret-physics-scheme" not in serialized
    assert "secret-viscosity" not in serialized


def test_scope_never_exposes_protected_path_content() -> None:
    task = task_fixture.__wrapped__()
    protected = task.protected_paths[0]
    scope = build_repair_scope(
        classification=_classification(),
        task=task,
        plan=valid_plan(),
        current_files={
            "system/fvSchemes": f"#include \"{protected}\"\n"
        },
        knowledge_ids=(),
    )

    assert protected not in scope.model_dump_json()
    assert scope.relevant_files[0].content_mode == "metadata_only"


def test_scope_preserves_grouped_field_name_and_target_file() -> None:
    classification = _classification(
        code="missing_registry_object",
        evidence=[
            {
                "kind": "log_pattern",
                "value": "cannot find object thermo:rho",
            }
        ],
        scope_hints={
            "files": ["0/p_rgh"],
            "dictionary_blocks": [],
            "commands": ["solve-a"],
        },
        allowed_operations=["replace_file"],
    )
    content = "rho thermo:rho;\nboundaryField {}\n"

    scope = build_repair_scope(
        classification=classification,
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        current_files={"0/p_rgh": content},
        knowledge_ids=("of10.compressible.pressure",),
    )

    assert "thermo:rho" in scope.model_dump_json()
    assert scope.relevant_files[0].path == "0/p_rgh"


def test_scope_reports_unresolved_when_no_relevant_public_evidence() -> None:
    with pytest.raises(RepairScopeError) as captured:
        build_repair_scope(
            classification=_classification(
                code="unclassified_native_failure",
                confidence="low",
                scope_hints={
                    "files": [],
                    "dictionary_blocks": [],
                    "commands": [],
                },
            ),
            task=task_fixture.__wrapped__(),
            plan=valid_plan(),
            current_files={},
            knowledge_ids=(),
        )

    assert captured.value.code == "REPAIR_SCOPE_UNRESOLVED"
    assert captured.value.message == "无法在上下文预算内确定足够的修复证据。"
