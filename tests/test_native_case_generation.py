from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
import pytest

from foampilot.agent.generation import (
    author_case_bundle,
    materialize_case,
)
from foampilot.agent.status import AgentStatusSnapshot
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelRequest,
    ModelResult,
    ModelStage,
    ModelContextArtifact,
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
from foampilot.preprocessing import (
    BoundingBox,
    ExecutedMeshFacts,
    GeometryFacts,
    InputMeshFacts,
    MeshCheckFact,
    MeshQualityReport,
)
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload


class RecordingModel:
    def __init__(self, replies: list[BaseModel | Exception]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []
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
        self.requests.append(request)
        self.budgets.append(budget)
        assert budget.stage in {ModelStage.GENERATION, ModelStage.REPAIR}
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
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


def _author_status() -> AgentStatusSnapshot:
    return AgentStatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source_event_sequence": 5,
            "current_stage": "author",
            "last_completed_stage": "CONTEXT_READY",
            "attempt": {"current": 1, "maximum": 2},
            "capability": {
                "solver_family": "incompressible-laminar",
                "solver": "icoFoam",
                "regions": [],
            },
            "latest_failure": None,
            "budget": {
                "model_logical_requests_remaining": 2,
                "transport_attempts_remaining": 7,
                "model_seconds_remaining": 600,
                "execution_seconds_remaining": 120,
            },
            "context": {
                "knowledge_ids": ["of10.ico.contract"],
                "skill_names": ["openfoam-author-native-case"],
                "knowledge_sources_sha256": "a" * 64,
                "skills_sha256": "b" * 64,
            },
            "allowed_actions": ["author_case_bundle"],
            "immutable_constraints": {
                "public_assets": [],
                "protected_path_count": 1,
                "protected_paths_sha256": "c" * 64,
                "openfoam_distribution": "foundation",
                "openfoam_version": "10",
            },
        }
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


def test_bundle_prompt_and_request_reference_deterministic_status() -> None:
    model = RecordingModel([_plan()])
    status = _author_status()
    reference = ModelContextArtifact(
        path="agent-status-author-01.json",
        sha256="d" * 64,
    )

    author_case_bundle(
        _task(),
        _environment("blockMesh", "icoFoam"),
        _capability(),
        model,
        "public knowledge",
        "portable skill",
        status_snapshot=status,
        status_artifact=reference,
        budget=_model_window(ModelStage.GENERATION),
        trace=InMemoryModelTraceSink(),
    )

    assert "DETERMINISTIC AGENT STATUS" in model.requests[0].user_prompt
    assert '"current_stage": "author"' in model.requests[0].user_prompt
    assert model.requests[0].context_artifacts == (reference,)


def test_bundle_prompt_contains_bounded_public_geometry_facts() -> None:
    model = RecordingModel([_plan()])
    facts = GeometryFacts(
        mode="surface",
        source_hashes={"geometry/body.stl": "b" * 64},
        declared_length_unit="mm",
        bounding_box_m=BoundingBox(
            minimum=(0.0, 0.0, 0.0),
            maximum=(0.1, 0.02, 0.01),
        ),
        point_count=12,
        face_count=20,
        surface_names=("body",),
        region_names=("fluid",),
        closed_surface=True,
        manifold_status="closed_manifold",
        dimensionality_observation="three_d",
        patch_role_matches=(),
        topology_observations=("boundary_edges=0",),
        warnings=(),
    )

    author_case_bundle(
        _task(),
        _environment("blockMesh", "icoFoam"),
        _capability(),
        model,
        "public knowledge",
        "portable skill",
        geometry_facts=facts,
        budget=_model_window(ModelStage.GENERATION),
        trace=InMemoryModelTraceSink(),
    )

    prompt = model.requests[0].user_prompt
    assert "PUBLIC GEOMETRY FACTS" in prompt
    assert '"face_count": 20' in prompt
    assert '"maximum"' in prompt
    assert "/tmp/" not in prompt


def test_bundle_prompt_contains_compact_authoritative_mesh_facts() -> None:
    model = RecordingModel([_plan()])
    input_facts = InputMeshFacts(
        bundle_manifest_sha256="a" * 64,
        inspector_id="foampilot.mesh.poly-mesh",
        inspector_version="1.0.0",
        region=None,
        declared_length_unit="m",
        source_member_sha256={"points": "b" * 64},
        points=12,
        faces=11,
        internal_faces=1,
        cells=2,
        bounding_box_m=BoundingBox(
            minimum=(0, 0, 0),
            maximum=(2, 1, 1),
        ),
        patches=(),
        cell_zones=(),
        face_zones=(),
        point_zones=(),
        dimensionality_observations=("empty patch frontAndBack",),
        topology_observations=("owner count equals face count",),
        warnings=(),
    )
    metrics = MeshQualityReport(
        strategy="provided",
        commands_completed=("inspect-provided-mesh",),
        mesh_created=True,
        check_mesh_passed=True,
        cells=2,
        faces=11,
        points=12,
        regions=1,
        patches=(),
        failed_requirements=(),
        warnings=(),
        evidence_files=(".foampilot/logs/check.log",),
    )
    executed = ExecutedMeshFacts(
        mesh_check=MeshCheckFact(
            executed=True,
            executable_identity="checkMesh:trusted",
            return_code=0,
            timed_out=False,
            mesh_ok=True,
            evidence_paths=(".foampilot/logs/check.log",),
        ),
        metrics=metrics,
    )

    author_case_bundle(
        _task(),
        _environment("checkMesh", "icoFoam"),
        _capability(),
        model,
        "public knowledge",
        "portable skill",
        input_mesh_facts=(input_facts,),
        executed_mesh_facts=(executed,),
        budget=_model_window(ModelStage.GENERATION),
        trace=InMemoryModelTraceSink(),
    )

    prompt = model.requests[0].user_prompt
    assert "AUTHORITATIVE INPUT MESH FACTS" in prompt
    assert "PRE-AUTHORING EXECUTED MESH FACTS" in prompt
    assert '"cells": 2' in prompt
    assert "points\n(" not in prompt


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
    assert "只生成求解该算例必需的文件和命令" in prompt
    assert (
        "不要仅为制造评测证据而添加 function object、sampling、\n"
        "extrema 或 residualControl"
    ) in prompt
    assert (
        "求解成功后，由 evaluator 从 solver log 和写出字段计算观测量"
    ) in prompt
    assert (
        "使用 MPI 时，设置 solver executable 和 mpi_ranks；绝不能生成\n"
        "mpirun 或 orterun"
    ) in prompt
    assert (
        "除非公开任务明确要求更严格 flag，否则只使用普通 checkMesh"
    ) in prompt
    assert "-allGeometry 或 -allTopology" in prompt


def test_bundle_prompt_requires_applicable_retrieved_contract_rules() -> None:
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
    assert "content.rules 视为必须落实的适用契约" in prompt
    assert "公开任务明确冲突" in prompt


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
