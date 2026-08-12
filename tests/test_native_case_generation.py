from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
import pytest

from foampilot.agent.generation import materialize_case
from foampilot.authoring import CaseBundle
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.models import (
    ModelBudgetLedger,
    ModelRequest,
    ModelResult,
    ModelStage,
)
from foampilot.simulation import FactEvidence, ResolvedValue, SimulationIntent
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
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
from tests.support.tasks import canonical_task_payload


class RecordingModel:
    def __init__(self, replies: list[BaseModel | Exception]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []
        self.all_requests: list[ModelRequest] = []
        self.budgets = []

    primary_backend_id = "recording"
    primary_model = "recording-model"
    policy_sha256 = "a" * 64

    def generate_structured(
        self,
        request,
        schema,
        *,
        budget,
        trace,
        output_normalizer=None,
    ):
        del trace, output_normalizer
        self.all_requests.append(request)
        if schema is SimulationIntent:
            assert budget.stage == ModelStage.INTENT_INTERPRETATION
            payload = __import__("json").loads(request.user_prompt)
            request_text = str(payload["request_text"])
            solver = next(
                (
                    request_text[index : index + len(candidate)]
                    for candidate in (
                        kind.partition(":")[2]
                        for kind in payload["available_capability_kinds"]
                        if kind.startswith("solver:")
                    )
                    for index in range(len(request_text))
                    if request_text[index : index + len(candidate)].casefold()
                    == candidate.casefold()
                ),
                None,
            )
            reply = SimulationIntent(
                facts=(
                    ResolvedValue(
                        field_path="solver.family",
                        value=solver,
                        source="user_text",
                        impact="low",
                        evidence=(
                            FactEvidence(kind="user_quote", detail=solver),
                        ),
                        confirmed=True,
                    ),
                )
                if solver is not None
                else (),
            )
        elif schema is CaseDesignProposal:
            assert budget.stage == ModelStage.CASE_DESIGN
            payload = __import__("json").loads(request.user_prompt)
            requirements = payload["ResolvedRequirements"]["resolved"]
            solver_fact = next(
                item for item in requirements
                if item["field_path"] == "solver.family"
            )
            descriptor = next(
                item for item in payload["capability_registry"]
                if any(
                    kind.startswith("solver:")
                    for kind in item["capability_kinds"]
                )
            )
            reply = CaseDesignProposal(
                solver_family=ResolvedValue.model_validate(solver_fact),
                physical_models=(),
                materials=(),
                boundary_designs=(),
                initial_conditions=(),
                time_design=(),
                numerical_design=(
                    ResolvedValue(
                        field_path="numerics.delta_t",
                        value=0.01,
                        source="deterministic_rule",
                        impact="low",
                        evidence=(
                            FactEvidence(
                                kind="test_fixture",
                                detail="fixture controlDict time step",
                            ),
                        ),
                        confirmed=True,
                    ),
                ),
                region_models=(),
                extension_decisions=(
                    ExtensionDecision(
                        extension_id=descriptor["extension_id"],
                        schema_version=descriptor["protocol_version"],
                        values=(),
                        provenance=(
                            FactEvidence(
                                kind="test_fixture",
                                detail="scripted registered extension",
                            ),
                        ),
                    ),
                ),
                uncertainties=(),
                alternatives=(),
                reasoning_evidence=(
                    FactEvidence(
                        kind="test_fixture",
                        detail="scripted coherent case design",
                    ),
                ),
                capability_conflicts=(),
            )
        else:
            self.requests.append(request)
            self.budgets.append(budget)
            assert budget.stage in {
                ModelStage.CASE_AUTHORING,
                ModelStage.GENERATION,
                ModelStage.REPAIR,
            }
            reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if schema is CaseBundle and isinstance(reply, ExecutionPlan):
            reply = CaseBundle(
                manifest=reply.manifest,
                files=reply.files,
            )
        assert isinstance(reply, schema)
        return ModelResult(
            value=reply,
            logical_request_id=f"recording-{len(self.requests)}",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
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
        canonical_task_payload({
            "schema_version": 2,
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
        })
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
        schema_version=4,
        compiled_from_design_sha256="a" * 64,
        compiler_identities={"test.fixture": "1.0.0/protocol-1"},
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
                    "FoamFile\n{\n    format ascii;\n"
                    "    class dictionary;\n"
                    "    object controlDict;\n}\n"
                    f"application {application};\n"
                    "deltaT 0.01;\n"
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
                path="system/blockMeshDict",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object blockMeshDict;\n}\n"
                    "vertices ();\nblocks ();\n"
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


def test_materializes_complete_compiled_plan_bundle(tmp_path: Path) -> None:
    plan = _plan()
    generated = materialize_case(plan, _task(), tmp_path)

    assert generated == [
        tmp_path / "system/controlDict",
        tmp_path / "system/fvSchemes",
        tmp_path / "system/fvSolution",
        tmp_path / "system/blockMeshDict",
        tmp_path / "constant/physicalProperties",
        tmp_path / "0/U",
        tmp_path / "0/p",
    ]
    assert "application icoFoam;" in (
        tmp_path / "system/controlDict"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / ".foampilot/generation-checkpoint.json").exists()


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
