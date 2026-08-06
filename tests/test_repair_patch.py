from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.agent.repair import request_repair_patch
from foampilot.agent.status import AgentStatusSnapshot
from foampilot.agent.failure import NativeFailureClassification
from foampilot.agent.repair_patch import (
    RepairPatch,
    RepairPatchError,
    apply_repair_patch,
)
from foampilot.agent.repair_scope import RepairScope
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelContextArtifact,
    ModelStage,
)
from foampilot.models.schema import strict_response_schema

from tests.test_execution_plan import task as task_fixture
from tests.test_execution_plan import valid_plan
from tests.test_native_case_generation import RecordingModel, _model_window


def _scope(*, operations: list[str] | None = None) -> RepairScope:
    return RepairScope.model_validate(
        {
            "schema_version": 1,
            "failure_code": "unclassified_native_failure",
            "relevant_files": [
                {
                    "path": "system/controlDict",
                    "content_mode": "full",
                    "bytes": 56,
                    "sha256": "a" * 64,
                    "exists": True,
                    "content": "FoamFile { class dictionary; }",
                },
                {
                    "path": "constant/newProperties",
                    "content_mode": "metadata_only",
                    "bytes": 0,
                    "sha256": "e3b0c44298fc1c149afbf4c8996fb924"
                    "27ae41e4649b934ca495991b7852b855",
                    "exists": False,
                },
            ],
            "relevant_commands": [
                "mesh",
                "initialize",
                "solve-a",
                "solve-b",
            ],
            "relevant_knowledge_ids": [],
            "allowed_operations": operations
            or [
                "add_file",
                "replace_file",
                "insert_command_before",
                "insert_command_after",
                "replace_command",
                "remove_command",
            ],
            "earliest_possible_rerun_stage": "inspection",
            "excluded_file_count": 0,
        }
    )


def _patch(**overrides) -> RepairPatch:
    payload = {
        "schema_version": 1,
        "because": "The public failure identifies one missing setting.",
        "evidence": ["missing_dictionary_keyword"],
        "file_operations": [],
        "command_operations": [],
        "expected_check": "The failed command advances.",
        "stable_control": "Geometry and boundary values remain unchanged.",
    }
    payload.update(overrides)
    return RepairPatch.model_validate(payload)


def _current_files() -> dict[str, str]:
    return {item.path: item.content for item in valid_plan().files}


def _apply(patch: RepairPatch, *, scope: RepairScope | None = None):
    return apply_repair_patch(
        patch,
        scope=scope or _scope(),
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        available_executables={
            "blockMesh",
            "checkMesh",
            "setFields",
            "potentialFoam",
            "icoFoam",
        },
        current_files=_current_files(),
    )


def test_patch_adds_and_replaces_files() -> None:
    revised_control = (
        "FoamFile { class dictionary; }\napplication icoFoam;\n"
        "deltaT 0.001;\n"
    )
    result = _apply(
        _patch(
            file_operations=[
                {
                    "operation": "replace",
                    "path": "system/controlDict",
                    "content": revised_control,
                },
                {
                    "operation": "add",
                    "path": "constant/newProperties",
                    "content": "FoamFile { class dictionary; }\nvalue 1;\n",
                },
            ]
        )
    )

    by_path = {item.path: item.content for item in result.plan.files}
    assert by_path["system/controlDict"] == revised_control
    assert "constant/newProperties" in by_path
    assert result.changes.changed_file_paths == [
        "system/controlDict",
        "constant/newProperties",
    ]


def test_patch_supports_all_command_operations_in_order() -> None:
    result = _apply(
        _patch(
            command_operations=[
                {
                    "operation": "insert_after",
                    "anchor_step_id": "mesh",
                    "command": {
                        "step_id": "check-mesh",
                        "stage": "check",
                        "executable": "checkMesh",
                        "args": [],
                        "mpi_ranks": 1,
                        "timeout_seconds": 5,
                    },
                },
                {
                    "operation": "insert_before",
                    "anchor_step_id": "solve-a",
                    "command": {
                        "step_id": "set-fields",
                        "stage": "initialize",
                        "executable": "setFields",
                        "args": [],
                        "mpi_ranks": 1,
                        "timeout_seconds": 5,
                    },
                },
                {
                    "operation": "replace",
                    "target_step_id": "solve-a",
                    "command": {
                        "step_id": "solve-a",
                        "stage": "solve",
                        "executable": "icoFoam",
                        "args": ["-latestTime"],
                        "mpi_ranks": 1,
                        "timeout_seconds": 25,
                    },
                },
                {
                    "operation": "remove",
                    "target_step_id": "solve-b",
                },
            ]
        )
    )

    assert [item.step_id for item in result.plan.commands] == [
        "mesh",
        "check-mesh",
        "initialize",
        "set-fields",
        "solve-a",
    ]
    assert result.changes.command_operations == [
        "insert_after:check-mesh",
        "insert_before:set-fields",
        "replace:solve-a",
        "remove:solve-b",
    ]


def test_exact_noop_operation_is_dropped_when_patch_has_real_change() -> None:
    plan = valid_plan()
    unchanged_control = next(
        item.content
        for item in plan.files
        if item.path == "system/controlDict"
    )
    unchanged_solve = next(
        item.model_dump(mode="json")
        for item in plan.commands
        if item.step_id == "solve-a"
    )
    result = _apply(
        _patch(
            file_operations=[
                {
                    "operation": "replace",
                    "path": "system/controlDict",
                    "content": unchanged_control + "deltaT 0.001;\n",
                }
            ],
            command_operations=[
                {
                    "operation": "replace",
                    "target_step_id": "solve-a",
                    "command": unchanged_solve,
                }
            ],
        )
    )

    assert result.changes.changed_file_paths == ["system/controlDict"]
    assert result.changes.command_operations == []
    assert result.normalizations == ["DROP_NO_OP_COMMAND:solve-a"]


def test_all_noop_patch_is_rejected() -> None:
    unchanged_control = _current_files()["system/controlDict"]

    with pytest.raises(RepairPatchError) as captured:
        _apply(
            _patch(
                file_operations=[
                    {
                        "operation": "replace",
                        "path": "system/controlDict",
                        "content": unchanged_control,
                    }
                ]
            )
        )

    assert captured.value.code == "REPAIR_PATCH_INVALID"
    assert "NO_OP_REPAIR_PATCH" in captured.value.detail


def test_file_patch_does_not_reject_preexisting_flexible_command_order() -> None:
    plan = valid_plan().model_copy(deep=True)
    plan.commands = [
        plan.commands[0],
        plan.commands[2],
        plan.commands[1],
        plan.commands[3],
    ]
    current = {item.path: item.content for item in plan.files}
    result = apply_repair_patch(
        _patch(
            file_operations=[
                {
                    "operation": "replace",
                    "path": "system/controlDict",
                    "content": current["system/controlDict"] + "deltaT 0.001;\n",
                }
            ]
        ),
        scope=_scope(operations=["replace_file"]),
        task=task_fixture.__wrapped__(),
        plan=plan,
        available_executables={"blockMesh", "potentialFoam", "icoFoam"},
        current_files=current,
    )

    assert [item.step_id for item in result.plan.commands] == [
        item.step_id for item in plan.commands
    ]


@pytest.mark.parametrize(
    "operation",
    [
        {
            "operation": "insert_before",
            "anchor_step_id": "missing",
            "command": {
                "step_id": "set-fields",
                "stage": "initialize",
                "executable": "setFields",
                "timeout_seconds": 5,
            },
        },
        {
            "operation": "insert_after",
            "anchor_step_id": "solve-a",
            "command": {
                "step_id": "late-mesh",
                "stage": "mesh",
                "executable": "blockMesh",
                "timeout_seconds": 5,
            },
        },
        {
            "operation": "remove",
            "target_step_id": "missing",
        },
    ],
)
def test_invalid_anchor_stage_order_and_target_are_rejected(operation) -> None:
    with pytest.raises(RepairPatchError):
        _apply(_patch(command_operations=[operation]))


def test_patch_cannot_escape_scope_or_task_safety() -> None:
    with pytest.raises(RepairPatchError) as captured:
        _apply(
            _patch(
                file_operations=[
                    {
                        "operation": "replace",
                        "path": "0/U",
                        "content": "include /private/tutorial/cavity",
                    }
                ]
            ),
            scope=_scope(operations=["replace_file"]),
        )

    assert "OUTSIDE_REPAIR_SCOPE" in captured.value.detail


def test_operation_shape_is_strict() -> None:
    with pytest.raises(ValidationError):
        _patch(
            command_operations=[
                {
                    "operation": "remove",
                    "target_step_id": "solve-b",
                    "command": {
                        "step_id": "unexpected",
                        "stage": "solve",
                        "executable": "icoFoam",
                        "timeout_seconds": 1,
                    },
                }
            ]
        )


def test_repair_patch_schema_avoids_provider_unsupported_oneof() -> None:
    schema = strict_response_schema(RepairPatch.model_json_schema())
    encoded = str(schema)

    assert "oneOf" not in encoded
    assert "discriminator" not in encoded


def test_repair_request_uses_scope_not_full_case() -> None:
    patch = _patch(
        file_operations=[
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": "FoamFile { class dictionary; }\napplication icoFoam;\n",
            }
        ]
    )
    model = RecordingModel([patch])
    classification = NativeFailureClassification.model_validate(
        {
            "schema_version": 1,
            "domain": "solver",
            "code": "missing_dictionary_keyword",
            "confidence": "high",
            "failed_stage": "solve",
            "failed_step_id": "solve-a",
            "evidence": [{"kind": "log_pattern", "value": "missing"}],
            "scope_hints": {
                "files": ["system/controlDict"],
                "dictionary_blocks": [],
                "commands": ["solve-a"],
            },
            "allowed_operations": ["replace_file"],
        }
    )
    status = AgentStatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source_event_sequence": 9,
            "current_stage": "repair",
            "last_completed_stage": "PUBLIC_VALIDATION_COMPLETE",
            "attempt": {"current": 1, "maximum": 2},
            "capability": {
                "solver_family": "incompressible-laminar",
                "solver": "icoFoam",
                "regions": ["default"],
            },
            "latest_failure": {
                "domain": "solver",
                "code": "missing_dictionary_keyword",
                "detail": "missing",
            },
            "budget": {
                "model_logical_requests_remaining": 1,
                "transport_attempts_remaining": 6,
                "model_seconds_remaining": 300,
                "execution_seconds_remaining": 60,
            },
            "context": {
                "knowledge_ids": [],
                "skill_names": ["openfoam-author-native-case"],
                "knowledge_sources_sha256": "a" * 64,
                "skills_sha256": "b" * 64,
            },
            "allowed_actions": ["replace_file"],
            "immutable_constraints": {
                "public_assets": [],
                "protected_path_count": 1,
                "protected_paths_sha256": "c" * 64,
                "openfoam_distribution": "foundation",
                "openfoam_version": "10",
            },
        }
    )

    actual = request_repair_patch(
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        classification=classification,
        repair_scope=_scope(operations=["replace_file"]),
        failed_log="missing keyword",
        knowledge_text="public rule",
        skills_text="minimal repair",
        status_snapshot=status,
        status_artifact=ModelContextArtifact(
            path="agent-status-repair-01.json",
            sha256="d" * 64,
        ),
        gateway=model,
        budget=_model_window(ModelStage.REPAIR),
        trace=InMemoryModelTraceSink(),
    )

    assert actual == patch
    prompt = model.requests[0].user_prompt
    assert "failure_classification" in prompt
    assert "repair_scope" in prompt
    assert "system/controlDict" in prompt
    assert "FoamFile { class volVectorField; }" not in prompt
    assert model.requests[0].context_artifacts[0].path == (
        "agent-status-repair-01.json"
    )
