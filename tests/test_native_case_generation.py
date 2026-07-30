from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
import pytest

from foampilot.agent.generation import (
    author_case_bundle,
    materialize_case,
)
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.models import ModelRequest
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
)
from foampilot.tasks import TaskSpec


class RecordingModel:
    def __init__(self, replies: list[BaseModel | Exception]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []

    def generate_structured(self, request, schema):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, schema)
        return reply


def _task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "task_id": "native-generation",
            "title": "Native generation",
            "prompt": "Create and solve a small laminar flow case.",
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 2,
                "max_wall_seconds": 120,
                "max_mpi_ranks": 1,
                "memory_mib": 1024,
            },
            "required_outputs": ["velocity"],
            "acceptance_requirements": ["normal completion"],
            "public_checks": [
                {
                    "name": "completion",
                    "kind": "completion",
                    "parameters": {},
                }
            ],
            "public_assets": [],
            "protected_paths": ["/private/tutorial/native-generation"],
        }
    )


def _environment(*commands: str) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=Path("/opt/openfoam"),
        tutorial_root=Path("/private/tutorial"),
        workspace_root=Path("/runs"),
        workspace_writable=True,
        commands=[
            CommandFact(name=name, path=Path("/opt/openfoam/bin") / name)
            for name in commands
        ],
        mpi_launcher=None,
        gmsh=None,
        max_mpi_ranks=1,
    )


def _plan(
    *,
    application: str = "icoFoam",
    files: list[GeneratedFile] | None = None,
) -> ExecutionPlan:
    return ExecutionPlan(
        schema_version=2,
        files=files
        or [
            GeneratedFile(
                path="system/controlDict",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object controlDict;\n}\n"
                    f"application {application};\n"
                ),
            ),
            GeneratedFile(
                path="0/U",
                content=(
                    "FoamFile\n{\n class volVectorField;\n object U;\n}\n"
                ),
            ),
        ],
        commands=[
            NativeCommand(
                step_id="mesh",
                executable="blockMesh",
                timeout_seconds=30,
            ),
            NativeCommand(
                step_id="solve",
                executable=application,
                timeout_seconds=60,
            ),
        ],
    )


def test_one_model_call_authors_and_materializes_complete_bundle(
    tmp_path: Path,
) -> None:
    plan = _plan()
    model = RecordingModel([plan])

    actual = author_case_bundle(
        _task(),
        _environment("blockMesh", "icoFoam"),
        model,
        "public knowledge",
        "portable skill",
    )
    generated = materialize_case(actual, _task(), tmp_path)

    assert actual == plan
    assert len(model.requests) == 1
    assert model.requests[0].purpose == "author-openfoam-case-bundle"
    assert generated == [
        tmp_path / "system/controlDict",
        tmp_path / "0/U",
    ]
    assert "application icoFoam;" in (
        tmp_path / "system/controlDict"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / ".foampilot/generation-checkpoint.json").exists()


def test_bundle_prompt_has_no_review_or_evaluator_contract() -> None:
    model = RecordingModel([_plan()])

    author_case_bundle(
        _task(),
        _environment("blockMesh", "icoFoam"),
        model,
        "public knowledge",
        "portable skill",
    )

    prompt = (
        model.requests[0].system_prompt
        + "\n"
        + model.requests[0].user_prompt
    )
    assert "/private/tutorial" not in prompt
    assert "expected_evidence" not in prompt
    assert "satisfies_outputs" not in prompt
    assert "review-openfoam-plan" not in prompt


def test_bundle_prompt_keeps_diagnostics_outside_the_required_solve() -> None:
    model = RecordingModel([_plan()])

    author_case_bundle(
        _task(),
        _environment("blockMesh", "icoFoam"),
        model,
        "public knowledge",
        "portable skill",
    )

    prompt = model.requests[0].system_prompt
    assert "Generate only files and commands required to solve the case." in (
        prompt
    )
    assert (
        "Do not add function objects, sampling, extrema, or residualControl "
        "solely to produce evaluation evidence."
    ) in prompt
    assert (
        "The evaluator derives measurements from solver logs and written "
        "fields after a successful solve."
    ) in prompt
    assert (
        "For MPI, set the solver executable and mpi_ranks; never emit "
        "mpirun or orterun."
    ) in prompt
    assert (
        "Use plain checkMesh unless the public task explicitly requires "
        "stricter flags"
    ) in prompt
    assert "-allGeometry or -allTopology" in prompt


def test_materializer_rejects_unsafe_and_protected_files(
    tmp_path: Path,
) -> None:
    unsafe = _plan(
        files=[GeneratedFile(path="../outside", content="escape")]
    )
    with pytest.raises(ValueError, match="safe relative"):
        materialize_case(unsafe, _task(), tmp_path / "unsafe")

    protected = _plan(
        files=[
            GeneratedFile(
                path="system/controlDict",
                content="/private/tutorial/native-generation",
            )
        ]
    )
    with pytest.raises(ValueError, match="protected path"):
        materialize_case(protected, _task(), tmp_path / "protected")


def test_materializer_requires_an_empty_case_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing").write_text("user data", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        materialize_case(_plan(), _task(), tmp_path)
