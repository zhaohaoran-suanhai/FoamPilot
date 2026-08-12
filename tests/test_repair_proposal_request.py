from __future__ import annotations

from foampilot.agent.failure import NativeFailureClassification
from foampilot.agent.repair import request_repair_proposal
from foampilot.agent.repair_scope import RepairScope
from foampilot.agent.status import AgentStatusSnapshot
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelContextArtifact,
    ModelStage,
)
from foampilot.models.schema import strict_response_schema
from foampilot.repair import RepairProposal

from tests.test_execution_plan import task as task_fixture
from tests.test_execution_plan import valid_plan
from tests.test_native_case_generation import RecordingModel, _model_window


def _scope() -> RepairScope:
    return RepairScope.model_validate(
        {
            "failure_code": "numerical_instability",
            "relevant_files": [
                {
                    "path": "system/controlDict",
                    "content_mode": "full",
                    "bytes": 56,
                    "sha256": "a" * 64,
                    "exists": True,
                    "content": "FoamFile { class dictionary; }",
                }
            ],
            "relevant_commands": ["solve-a"],
            "allowed_operations": ["replace_file"],
            "earliest_possible_rerun_stage": "inspection",
            "excluded_file_count": 3,
        }
    )


def _classification() -> NativeFailureClassification:
    return NativeFailureClassification.model_validate(
        {
            "domain": "solver",
            "code": "numerical_instability",
            "confidence": "high",
            "failed_stage": "solve",
            "failed_step_id": "solve-a",
            "evidence": [{"kind": "log_pattern", "value": "Courant number 10"}],
            "scope_hints": {
                "files": ["system/controlDict"],
                "dictionary_blocks": [],
                "commands": ["solve-a"],
            },
            "allowed_operations": ["replace_file"],
        }
    )


def _status() -> AgentStatusSnapshot:
    return AgentStatusSnapshot.model_validate(
        {
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
                "code": "numerical_instability",
                "detail": "Courant number 10",
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


def _proposal() -> RepairProposal:
    return RepairProposal(
        category="numerical",
        because="reduce the unstable time step",
        design_changes=(
            {
                "field_path": "numerics.delta_t",
                "old_value": 0.02,
                "new_value": 0.01,
                "operator": "replace",
            },
        ),
        file_operations=(
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": (
                    "FoamFile { class dictionary; }\n"
                    "application icoFoam;\ndeltaT 0.01;\n"
                ),
            },
        ),
        expected_checks=("rerun failed solver",),
    )


def test_repair_proposal_schema_is_provider_compatible_and_command_free() -> None:
    schema = strict_response_schema(RepairProposal.model_json_schema())
    encoded = str(schema)

    assert "oneOf" not in encoded
    assert "discriminator" not in encoded
    assert "command_operations" not in encoded
    assert "NativeCommand" not in encoded


def test_repair_request_uses_scope_not_full_case() -> None:
    proposal = _proposal()
    model = RecordingModel([proposal])

    actual = request_repair_proposal(
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        classification=_classification(),
        repair_scope=_scope(),
        failed_log="Courant number 10",
        knowledge_text="public rule",
        skills_text="minimal repair",
        status_snapshot=_status(),
        status_artifact=ModelContextArtifact(
            path="agent-status-repair-01.json",
            sha256="d" * 64,
        ),
        gateway=model,
        budget=_model_window(ModelStage.REPAIR),
        trace=InMemoryModelTraceSink(),
    )

    assert actual == proposal
    prompt = model.requests[0].user_prompt
    assert "failure_classification" in prompt
    assert "repair_scope" in prompt
    assert "system/controlDict" in prompt
    assert "FoamFile { class volVectorField; }" not in prompt
    assert model.requests[0].context_artifacts[0].path == (
        "agent-status-repair-01.json"
    )
    assert "commands" not in model.requests[0].system_prompt.lower()
