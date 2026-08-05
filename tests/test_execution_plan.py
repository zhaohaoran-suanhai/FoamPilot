from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    validate_execution_plan,
)
from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseModels,
    CaseRegion,
)
from foampilot.tasks import TaskSpec


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 2,
            "task_id": "side-driven-box",
            "title": "Side-driven enclosure",
            "prompt": "Solve a laminar incompressible side-driven box.",
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 2,
                "max_wall_seconds": 120,
                "max_mpi_ranks": 4,
                "memory_mib": 2048,
            },
            "required_outputs": ["velocity field", "pressure field"],
            "acceptance_requirements": ["mesh passes checkMesh"],
            "public_checks": [
                {
                    "name": "mesh-quality",
                    "kind": "mesh_ok",
                    "parameters": {},
                }
            ],
            "public_assets": [],
            "protected_paths": ["/private/tutorial/cavity"],
        }
    )


def valid_plan() -> ExecutionPlan:
    return ExecutionPlan(
        schema_version=3,
        manifest=CaseManifest(
            solver_executable="icoFoam",
            solver_family="incompressible-laminar",
            regime="transient",
            physics_family="fluid",
            mesh_family="blockMesh",
            dimensionality="2d",
            regions=[
                CaseRegion(
                    name="default",
                    kind="fluid",
                    path_prefix="",
                )
            ],
            fields=[
                CaseField(
                    name="U",
                    region="default",
                    path="0/U",
                    role="velocity",
                    created_by="author",
                )
            ],
            models=CaseModels(transport="Newtonian"),
        ),
        files=[
            GeneratedFile(
                path="system/controlDict",
                content="FoamFile { class dictionary; }\napplication icoFoam;\n",
            ),
            GeneratedFile(
                path="0/U",
                content="FoamFile { class volVectorField; }\n",
            ),
        ],
        commands=[
            NativeCommand(
                step_id="mesh",
                stage="mesh",
                executable="blockMesh",
                args=[],
                mpi_ranks=1,
                timeout_seconds=20,
            ),
            NativeCommand(
                step_id="initialize",
                stage="initialize",
                executable="potentialFoam",
                args=[],
                mpi_ranks=2,
                timeout_seconds=20,
            ),
            NativeCommand(
                step_id="solve-a",
                stage="solve",
                executable="icoFoam",
                args=[],
                mpi_ranks=1,
                timeout_seconds=30,
            ),
            NativeCommand(
                step_id="solve-b",
                stage="solve",
                executable="icoFoam",
                args=["-latestTime"],
                mpi_ranks=1,
                timeout_seconds=30,
            ),
        ],
    )


def test_plan_accepts_arbitrary_installed_command_sequence(
    task: TaskSpec,
) -> None:
    assert validate_execution_plan(
        valid_plan(),
        task,
        {"blockMesh", "potentialFoam", "icoFoam"},
    ) == []


def test_plan_schema_rejects_removed_mechanical_fields() -> None:
    payload = valid_plan().model_dump(mode="json")
    payload["application"] = "icoFoam"

    with pytest.raises(ValidationError, match="application"):
        ExecutionPlan.model_validate(payload)

    command = payload["commands"][0]
    command["phase"] = "mesh"
    payload.pop("application")
    with pytest.raises(ValidationError, match="phase"):
        ExecutionPlan.model_validate(payload)


def test_plan_rejects_unsafe_files_shell_and_protected_references(
    task: TaskSpec,
) -> None:
    plan = valid_plan().model_copy(deep=True)
    plan.files[0].path = "../system/controlDict"
    plan.files[1].content = "include /private/tutorial/cavity"
    plan.commands[0].args = ["&&", "cp"]

    issues = validate_execution_plan(
        plan,
        task,
        {"blockMesh", "potentialFoam", "icoFoam"},
    )

    assert {issue.code for issue in issues} >= {
        "UNSAFE_FILE_PATH",
        "PROTECTED_REFERENCE",
        "SHELL_TOKEN",
    }


def test_plan_rejects_duplicate_paths_steps_and_public_asset_overlap(
    task: TaskSpec,
) -> None:
    payload = task.model_dump(mode="json")
    payload["public_assets"] = [
        {
            "path": "0/U",
            "sha256": "a" * 64,
            "purpose": "provided field",
        }
    ]
    task = TaskSpec.model_validate(payload)
    plan = valid_plan().model_copy(deep=True)
    plan.files.append(plan.files[0].model_copy())
    plan.commands.append(plan.commands[0].model_copy())

    issues = validate_execution_plan(
        plan,
        task,
        {"blockMesh", "potentialFoam", "icoFoam"},
    )

    assert {issue.code for issue in issues} >= {
        "DUPLICATE_FILE_PATH",
        "DUPLICATE_STEP_ID",
        "PUBLIC_ASSET_OVERWRITE",
    }


def test_plan_rejects_unknown_executable_rank_host_and_timeout(
    task: TaskSpec,
) -> None:
    plan = valid_plan().model_copy(deep=True)
    plan.commands[0].executable = "missingFoam"
    plan.commands[1].mpi_ranks = 8
    plan.commands[2].args = ["--host", "remote"]
    plan.commands[3].timeout_seconds = 100

    issues = validate_execution_plan(
        plan,
        task,
        {"blockMesh", "potentialFoam", "icoFoam"},
    )

    assert {issue.code for issue in issues} >= {
        "EXECUTABLE_UNAVAILABLE",
        "MPI_RANK_LIMIT",
        "MPI_HOST_SELECTION",
        "TIMEOUT_BUDGET",
    }
