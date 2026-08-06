from __future__ import annotations

import os
from pathlib import Path

import pytest

from foampilot.agent import NativeAgent
from foampilot.agent.repair_patch import RepairPatch
from foampilot.artifacts import ArtifactStore
from foampilot.models import BackendRegistry, ModelGateway
from foampilot.plans import ExecutionPlan, NativeCommand
from foampilot.runtime import RuntimeConfig
from foampilot.tasks import load_task_spec
from tests.support.model_gateway import ScriptedBackend, valid_response


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "examples/tasks/non-tutorial-side-driven-box.yaml"
PLAN = ROOT / "tests/fixtures/gates/non-tutorial-side-driven-plan.json"


def _gateway(*responses: str) -> ModelGateway:
    registry = BackendRegistry()
    registry.register(
        ScriptedBackend([valid_response(item) for item in responses]),
        priority=10,
    )
    return ModelGateway(registry=registry, sleep=lambda seconds: None)


@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_OPENFOAM") != "1",
    reason="real OpenFOAM Runner integration is opt-in",
)
def test_real_repair_inserts_missing_mesh_commands(tmp_path: Path) -> None:
    task = load_task_spec(TASK)
    complete = ExecutionPlan.model_validate_json(PLAN.read_text(encoding="utf-8"))
    solver = next(command for command in complete.commands if command.stage == "solve")
    missing_mesh = complete.model_copy(update={"commands": [solver]})
    patch = RepairPatch(
        because="The solver cannot open constant/polyMesh because mesh steps are absent.",
        evidence=["cannot find constant/polyMesh/points"],
        file_operations=[],
        command_operations=[
            {
                "operation": "insert_before",
                "anchor_step_id": solver.step_id,
                "command": next(
                    command.model_dump(mode="json")
                    for command in complete.commands
                    if command.stage == "mesh"
                ),
            },
            {
                "operation": "insert_before",
                "anchor_step_id": solver.step_id,
                "command": next(
                    command.model_dump(mode="json")
                    for command in complete.commands
                    if command.stage == "check"
                ),
            },
        ],
        expected_check="blockMesh and checkMesh run before icoFoam.",
        stable_control="All case files and physical settings remain unchanged.",
    )

    outcome = NativeAgent(
        gateway=_gateway(missing_mesh.model_dump_json(), patch.model_dump_json()),
        runtime_config=RuntimeConfig.local_foundation_v10(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
    ).solve(task)

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert len(outcome.summary.attempts) == 2
    repaired = ExecutionPlan.model_validate_json(
        (outcome.run_dir / "attempt-02/execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert [command.stage.value for command in repaired.commands] == [
        "mesh",
        "check",
        "solve",
    ]


@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_OPENFOAM") != "1",
    reason="real OpenFOAM Runner integration is opt-in",
)
def test_real_repair_removes_invalid_optional_command(tmp_path: Path) -> None:
    task = load_task_spec(TASK)
    complete = ExecutionPlan.model_validate_json(PLAN.read_text(encoding="utf-8"))
    complete.commands[-1].timeout_seconds = 250
    complete.commands.append(
        NativeCommand(
            step_id="optional-invalid-check",
            stage="check",
            executable="checkMesh",
            args=["-notARealOption"],
            timeout_seconds=10,
        )
    )
    patch = RepairPatch(
        because="The trailing optional command uses an invalid option.",
        evidence=["Invalid option: -notARealOption"],
        file_operations=[],
        command_operations=[
            {
                "operation": "remove",
                "target_step_id": "optional-invalid-check",
            }
        ],
        expected_check="The required mesh and solver path completes.",
        stable_control="Mesh, solver and all case files remain unchanged.",
    )

    outcome = NativeAgent(
        gateway=_gateway(complete.model_dump_json(), patch.model_dump_json()),
        runtime_config=RuntimeConfig.local_foundation_v10(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
    ).solve(task)

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert len(outcome.summary.attempts) == 2
    repaired = ExecutionPlan.model_validate_json(
        (outcome.run_dir / "attempt-02/execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert "optional-invalid-check" not in {
        command.step_id for command in repaired.commands
    }
