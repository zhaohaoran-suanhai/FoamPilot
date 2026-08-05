from __future__ import annotations

from foampilot.agent.repair import (
    RepairDecision,
    failure_fingerprint,
    request_repair,
    should_stop_repair,
    validate_repair_decision,
)
from foampilot.models import InMemoryModelTraceSink, ModelStage
from foampilot.plans import GeneratedFile, NativeCommand
from foampilot.validation.models import (
    PublicValidationCheck,
    PublicValidationReport,
)

from tests.test_execution_plan import task as task_fixture
from tests.test_execution_plan import valid_plan
from tests.test_native_case_generation import (
    RecordingModel,
    _model_window,
)


def _report() -> PublicValidationReport:
    return PublicValidationReport(
        checks=[
            PublicValidationCheck(
                name="completion",
                passed=False,
                detail="solver did not end normally",
                observed={"normal_end": False},
                limits={"normal_end": True},
            )
        ],
        failure_layer="PUBLIC_VALIDATION_FAILED",
        failed_step_id="solve",
    )


def _mesh_report() -> PublicValidationReport:
    return PublicValidationReport(
        checks=[
            PublicValidationCheck(
                name="mesh-quality-intent",
                passed=False,
                detail="maximum non-orthogonality exceeds the public limit",
                observed={"max_non_orthogonality": 82.0},
                limits={"max_non_orthogonality": 70.0},
            )
        ],
        failure_layer="MESH_QUALITY_FAILED",
        failed_step_id="check-mesh",
    )


def _decision(**overrides):
    payload = {
        "because": "The solver log reports a time-step instability.",
        "evidence": ["non-finite velocity in solve log"],
        "cause": "The transient time step is too large.",
        "changed_files": [
            GeneratedFile(
                path="system/controlDict",
                content="application icoFoam;\ndeltaT 0.001;\n",
            )
        ],
        "changed_commands": [],
        "expected_check": "The solver ends without non-finite fields.",
        "stable_control": "Mesh and boundary conditions remain unchanged.",
    }
    payload.update(overrides)
    return RepairDecision.model_validate(payload)


def test_repair_fingerprint_is_stable_and_stops_repeat() -> None:
    first = failure_fingerprint(_report(), log_tail="nan in U")
    second = failure_fingerprint(
        _report().model_copy(deep=True),
        log_tail="  nan   in U  ",
    )

    assert first == second
    stop = should_stop_repair(
        fingerprints=[first, second],
        attempts_used=2,
        max_attempts=3,
        generated_bytes_changed=True,
    )
    assert stop.stop
    assert stop.reason == "REPEATED_FAILURE"


def test_repair_stops_noop_unchanged_budget_and_environment() -> None:
    no_op = should_stop_repair(
        fingerprints=["a"],
        attempts_used=1,
        max_attempts=3,
        generated_bytes_changed=True,
        decision=_decision(changed_files=[], changed_commands=[]),
    )
    assert no_op.reason == "NO_OP"

    unchanged = should_stop_repair(
        fingerprints=["a"],
        attempts_used=1,
        max_attempts=3,
        generated_bytes_changed=False,
        decision=_decision(),
    )
    assert unchanged.reason == "UNCHANGED_BYTES"

    exhausted = should_stop_repair(
        fingerprints=["a"],
        attempts_used=3,
        max_attempts=3,
        generated_bytes_changed=True,
        decision=_decision(),
    )
    assert exhausted.reason == "BUDGET_EXHAUSTED"

    environment = should_stop_repair(
        fingerprints=[],
        attempts_used=0,
        max_attempts=3,
        generated_bytes_changed=True,
        environment_failure=True,
    )
    assert environment.reason == "ENVIRONMENT_FAILURE"


def test_repair_validation_allows_safe_new_files_but_rejects_unsafe_changes(
) -> None:
    task = task_fixture.__wrapped__()
    plan = valid_plan()
    new_dictionary = _decision(
        changed_files=[
            GeneratedFile(
                path="constant/physicalProperties.water",
                content="viscosityModel constant;\nnu 1e-6;\nrho 1000;\n",
            )
        ]
    )
    assert validate_repair_decision(
        new_dictionary,
        task=task,
        plan=plan,
        available_executables={
            "blockMesh",
            "checkMesh",
            "potentialFoam",
            "icoFoam",
        },
        current_files={},
    ) == []

    wrong_file = _decision(
        changed_files=[
            GeneratedFile(path="../constant/private", content="secret")
        ]
    )
    issues = validate_repair_decision(
        wrong_file,
        task=task,
        plan=plan,
        available_executables={
            "blockMesh",
            "checkMesh",
            "potentialFoam",
            "icoFoam",
        },
        current_files={},
    )
    assert any(issue.code == "INVALID_REPAIR_PLAN" for issue in issues)


def test_mesh_repair_rejects_unrelated_physics_and_solver_changes() -> None:
    task = task_fixture.__wrapped__()
    plan = valid_plan()
    decision = _decision(
        changed_files=[
            GeneratedFile(
                path="constant/physicalProperties",
                content="nu 9e-3;\n",
            )
        ],
        changed_commands=[
            NativeCommand(
                step_id="solve-a",
                stage="solve",
                executable="icoFoam",
                args=["-latestTime"],
                mpi_ranks=1,
                timeout_seconds=30,
            )
        ],
    )

    issues = validate_repair_decision(
        decision,
        task=task,
        plan=plan,
        report=_mesh_report(),
        available_executables={"blockMesh", "potentialFoam", "icoFoam"},
        current_files={"constant/physicalProperties": "nu 1e-6;\n"},
    )

    assert {item.code for item in issues} >= {
        "MESH_REPAIR_UNRELATED_FILE",
        "MESH_REPAIR_UNRELATED_COMMAND",
    }


def test_mesh_repair_allows_boundary_only_patch_synchronization() -> None:
    task = task_fixture.__wrapped__()
    plan = valid_plan()
    old_u = """internalField uniform (0 0 0);
boundaryField
{
    oldPatch { type noSlip; }
}
"""
    new_u = old_u.replace("oldPatch", "newPatch")
    decision = _decision(
        cause="The generated mesh renamed a boundary patch.",
        evidence=["checkMesh reports patch newPatch"],
        changed_files=[
            GeneratedFile(
                path="system/blockMeshDict",
                content="boundary (newPatch { type wall; faces (); });\n",
            ),
            GeneratedFile(path="0/U", content=new_u),
        ],
        stable_control="Internal field values and physical properties stay fixed.",
    )

    issues = validate_repair_decision(
        decision,
        task=task,
        plan=plan,
        report=_mesh_report(),
        available_executables={"blockMesh", "potentialFoam", "icoFoam"},
        current_files={"0/U": old_u},
    )

    assert not {
        "MESH_REPAIR_UNRELATED_FILE",
        "MESH_REPAIR_FIELD_SCOPE",
    } & {item.code for item in issues}

    wrong_command = _decision(
        changed_files=[],
        changed_commands=[
            NativeCommand(
                step_id="solve-a",
                stage="solve",
                executable="madeUpFoam",
                args=[],
                mpi_ranks=1,
                timeout_seconds=30,
            )
        ],
    )
    issues = validate_repair_decision(
        wrong_command,
        task=task,
        plan=plan,
        available_executables={
            "blockMesh",
            "checkMesh",
            "potentialFoam",
            "icoFoam",
        },
        current_files={},
    )
    assert any(issue.code == "INVALID_REPAIR_PLAN" for issue in issues)


def test_repair_request_contains_only_failed_public_evidence() -> None:
    model = RecordingModel([_decision()])
    decision = request_repair(
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        report=_report(),
        failed_log="FOAM FATAL ERROR: unstable time step",
        current_files={"system/controlDict": "application icoFoam;"},
        knowledge_text=(
            "For strict VOF bounds, first reduce the time-step family."
        ),
        skills_text="Change one causal family per repair.",
        gateway=model,
        budget=_model_window(ModelStage.REPAIR),
        trace=InMemoryModelTraceSink(),
    )

    assert decision.cause == "The transient time step is too large."
    assert model.requests[0].purpose == "repair-openfoam-attempt"
    assert "/private/" not in model.requests[0].user_prompt
    assert "first reduce the time-step family" in (
        model.requests[0].user_prompt
    )
    assert "Change one causal family per repair" in (
        model.requests[0].user_prompt
    )


def test_mesh_repair_prompt_excludes_unrelated_physics_content() -> None:
    model = RecordingModel([_decision()])

    request_repair(
        task=task_fixture.__wrapped__(),
        plan=valid_plan(),
        report=_mesh_report(),
        failed_log="checkMesh: max non-orthogonality 82",
        current_files={
            "system/blockMeshDict": "vertices ();\n",
            "constant/physicalProperties": "secret-viscosity 9e-3;\n",
            "0/U": "boundaryField { walls { type noSlip; } }\n",
        },
        knowledge_text="Reduce mesh non-orthogonality through topology.",
        skills_text="Keep physics fixed during mesh repair.",
        gateway=model,
        budget=_model_window(ModelStage.REPAIR),
        trace=InMemoryModelTraceSink(),
    )

    prompt = model.requests[0].user_prompt
    assert "vertices ();" in prompt
    assert "boundaryField" in prompt
    assert "secret-viscosity" not in prompt
