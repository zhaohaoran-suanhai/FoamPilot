from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
import pytest

from foampilot.agent.generation import (
    author_case_bundle,
    materialize_case,
)
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelRequest,
    ModelResult,
    ModelStage,
)
from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseModels,
    CaseRegion,
)
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
)
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec


class RecordingModel:
    def __init__(self, replies: list[BaseModel | Exception]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []

    provider_name = "recording"
    model = "recording-model"

    def generate_structured(
        self,
        request,
        schema,
        *,
        budget,
        trace,
    ):
        del trace
        self.requests.append(request)
        assert budget.stage in {ModelStage.GENERATION, ModelStage.REPAIR}
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, schema)
        return ModelResult(
            value=reply,
            logical_request_id=f"recording-{len(self.requests)}",
            transport_attempts=1,
            elapsed_seconds=0,
        )


def _model_window(stage: ModelStage):
    return ModelBudgetLedger.start().open_stage(
        stage,
        stage_deadline_seconds=(
            360 if stage == ModelStage.GENERATION else 240
        ),
    )


def _task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "task_id": "native-generation",
            "title": "Native generation",
            "prompt": (
                "Create and solve a small transient laminar incompressible "
                "single-phase flow case using icoFoam."
            ),
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


def _capability() -> CapabilityProfile:
    return CapabilityProfile(
        physics_family="fluid",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        turbulence="laminar",
        solver_family="incompressible-laminar",
        solver_executable="icoFoam",
        mesh_family="blockMesh",
        parallel_expected=False,
        confidence="high",
    )


def _plan(
    *,
    application: str = "icoFoam",
    files: list[GeneratedFile] | None = None,
) -> ExecutionPlan:
    return ExecutionPlan(
        schema_version=3,
        manifest=CaseManifest(
            solver_executable=application,
            solver_family=(
                "incompressible-laminar"
                if application == "icoFoam"
                else application
            ),
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
                ),
                CaseField(
                    name="p",
                    region="default",
                    path="0/p",
                    role="kinematic_pressure",
                    created_by="author",
                ),
            ],
            models=CaseModels(transport="Newtonian"),
        ),
        files=(
            lambda defaults: (
                defaults
                if files is None
                else [
                    {
                        item.path: item
                        for item in [*defaults, *files]
                    }[path]
                    for path in dict.fromkeys(
                        item.path for item in [*defaults, *files]
                    )
                ]
            )
        )(
            [
            GeneratedFile(
                path="system/controlDict",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object controlDict;\n}\n"
                    f"application {application};\n"
                ),
            ),
            GeneratedFile(
                path="system/fvSchemes",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object fvSchemes;\n}\n"
                ),
            ),
            GeneratedFile(
                path="system/fvSolution",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object fvSolution;\n}\n"
                ),
            ),
            GeneratedFile(
                path="constant/physicalProperties",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object physicalProperties;\n}\n"
                ),
            ),
            GeneratedFile(
                path="0/U",
                content=(
                    "FoamFile\n{\n class volVectorField;\n object U;\n}\n"
                ),
            ),
            GeneratedFile(
                path="0/p",
                content=(
                    "FoamFile\n{\n class volScalarField;\n object p;\n}\n"
                ),
            ),
        ]
        ),
        commands=[
            NativeCommand(
                step_id="mesh",
                stage="mesh",
                executable="blockMesh",
                timeout_seconds=30,
            ),
            NativeCommand(
                step_id="solve",
                stage="solve",
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
        _capability(),
        model,
        "public knowledge",
        "portable skill",
        budget=_model_window(ModelStage.GENERATION),
        trace=InMemoryModelTraceSink(),
    )
    generated = materialize_case(actual, _task(), tmp_path)

    assert actual == plan
    assert len(model.requests) == 1
    assert model.requests[0].purpose == "author-openfoam-case-bundle"
    assert generated == [
        tmp_path / "system/controlDict",
        tmp_path / "system/fvSchemes",
        tmp_path / "system/fvSolution",
        tmp_path / "constant/physicalProperties",
        tmp_path / "0/U",
        tmp_path / "0/p",
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
        _capability(),
        model,
        "public knowledge",
        "portable skill",
        budget=_model_window(ModelStage.GENERATION),
        trace=InMemoryModelTraceSink(),
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
        _capability(),
        model,
        "public knowledge",
        "portable skill",
        budget=_model_window(ModelStage.GENERATION),
        trace=InMemoryModelTraceSink(),
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
