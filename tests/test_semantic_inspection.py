from __future__ import annotations

from pathlib import Path

from foampilot.inspection import inspect_native_case, inspect_semantics
from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseModels,
    CasePatch,
    CaseRegion,
)
from foampilot.plans import ExecutionPlan, GeneratedFile, NativeCommand
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload


def _header(name: str, klass: str = "dictionary") -> str:
    return (
        "FoamFile\n{\n"
        "    format ascii;\n"
        f"    class {klass};\n"
        f"    object {name};\n"
        "}\n"
    )


def _task(*, reconstruct: bool = False) -> TaskSpec:
    requirements = ["normal solver completion"]
    if reconstruct:
        requirements.append("reconstruct parallel results")
    return TaskSpec.model_validate(
        canonical_task_payload({
            "schema_version": 2,
            "task_id": "semantic-test",
            "title": "Semantic inspection",
            "prompt": (
                "Use icoFoam for transient laminar incompressible "
                "single-phase flow."
            ),
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 2,
                "max_wall_seconds": 300,
                "max_mpi_ranks": 4,
                "memory_mib": 1024,
            },
            "required_outputs": ["velocity and pressure"],
            "acceptance_requirements": requirements,
            "public_checks": [
                {
                    "name": "completion",
                    "kind": "completion",
                    "parameters": {},
                }
            ],
            "protected_paths": ["/private/semantic-target"],
        })
    )


def _manifest(
    *,
    solver: str = "icoFoam",
    family: str = "incompressible-laminar",
) -> CaseManifest:
    return CaseManifest(
        solver_executable=solver,
        solver_family=family,
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
        patches=[
            CasePatch(
                name="walls",
                region="default",
                mesh_type="wall",
            )
        ],
        models=CaseModels(transport="Newtonian"),
    )


def _plan(
    *,
    solver: str = "icoFoam",
    application: str | None = None,
    mpi_ranks: int = 1,
    decompose: bool = False,
    reconstruct: bool = False,
) -> ExecutionPlan:
    application = application or solver
    files = [
        GeneratedFile(
            path="system/controlDict",
            content=_header("controlDict") + f"application {application};\n",
        ),
        GeneratedFile(
            path="system/fvSchemes",
            content=_header("fvSchemes"),
        ),
        GeneratedFile(
            path="system/fvSolution",
            content=_header("fvSolution"),
        ),
        GeneratedFile(
            path="system/blockMeshDict",
            content=(
                _header("blockMeshDict")
                + "boundary\n(\n walls { type wall; faces (); }\n);\n"
            ),
        ),
        GeneratedFile(
            path="constant/physicalProperties",
            content=_header("physicalProperties"),
        ),
        GeneratedFile(
            path="0/U",
            content=_header("U", "volVectorField"),
        ),
        GeneratedFile(
            path="0/p",
            content=_header("p", "volScalarField"),
        ),
    ]
    commands = [
        NativeCommand(
            step_id="mesh",
            stage="mesh",
            executable="blockMesh",
            timeout_seconds=30,
        ),
        NativeCommand(
            step_id="check",
            stage="check",
            executable="checkMesh",
            timeout_seconds=30,
        ),
    ]
    if decompose:
        files.append(
            GeneratedFile(
                path="system/decomposeParDict",
                content=_header("decomposeParDict"),
            )
        )
        commands.append(
            NativeCommand(
                step_id="decompose",
                stage="decompose",
                executable="decomposePar",
                timeout_seconds=30,
            )
        )
    commands.append(
        NativeCommand(
            step_id="solve",
            stage="solve",
            executable=solver,
            mpi_ranks=mpi_ranks,
            timeout_seconds=120,
        )
    )
    if reconstruct:
        commands.append(
            NativeCommand(
                step_id="reconstruct",
                stage="reconstruct",
                executable="reconstructPar",
                timeout_seconds=30,
            )
        )
    return ExecutionPlan(
        schema_version=3,
        manifest=_manifest(solver=solver),
        files=files,
        commands=commands,
    )


def _materialize(root: Path, plan: ExecutionPlan) -> None:
    for item in plan.files:
        path = root / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item.content, encoding="utf-8")


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_semantic_errors_capture_solver_application_and_field_mismatches(
    tmp_path: Path,
):
    solver_mismatch = _plan()
    solver_mismatch.commands[-1].executable = "simpleFoam"
    _materialize(tmp_path / "solver", solver_mismatch)

    application_mismatch = _plan(application="simpleFoam")
    _materialize(tmp_path / "application", application_mismatch)

    field_mismatch = _plan()
    field_mismatch.manifest.fields[0].path = "0/missingU"
    _materialize(tmp_path / "field", field_mismatch)

    assert "SEMANTIC_SOLVER_COMMAND_MISMATCH" in _codes(
        inspect_semantics(tmp_path / "solver", _task(), solver_mismatch)
    )
    assert "SEMANTIC_APPLICATION_MISMATCH" in _codes(
        inspect_semantics(
            tmp_path / "application",
            _task(),
            application_mismatch,
        )
    )
    assert "SEMANTIC_FIELD_REGION_MISMATCH" in _codes(
        inspect_semantics(tmp_path / "field", _task(), field_mismatch)
    )


def test_solver_created_output_is_not_required_before_execution(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan.manifest.fields.append(
        CaseField(
            name="phi",
            region="default",
            path="1/phi",
            role="solver-written face flux",
            created_by="solver",
        )
    )
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    assert "SEMANTIC_FIELD_PATH_MISSING" not in _codes(report)


def test_solver_created_output_may_use_a_descriptive_manifest_alias(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan.manifest.fields.append(
        CaseField(
            name="U-final",
            region="default",
            path="0.03/U",
            role="final velocity written by the solver",
            created_by="solver",
        )
    )
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    assert "SEMANTIC_FIELD_REGION_MISMATCH" not in _codes(report)


def test_command_stage_shape_is_checked_without_guessing_unknown_utility(
    tmp_path: Path,
):
    plan = _plan()
    plan.commands[0].stage = "solve"
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    assert "SEMANTIC_COMMAND_STAGE_MISMATCH" in _codes(report)


def test_external_and_surface_mesh_utilities_require_mesh_stage(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan.commands = [
        NativeCommand(
            step_id=f"mesh-{index}",
            stage="solve",
            executable=name,
            args=[],
            mpi_ranks=1,
            timeout_seconds=10,
        )
        for index, name in enumerate((
            "surfaceCheck",
            "surfaceFeatureExtract",
            "snappyHexMesh",
            "gmsh",
            "gmshToFoam",
        ), start=1)
    ] + [plan.commands[-1]]
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    stage_issues = [
        item
        for item in report.issues
        if item.code == "SEMANTIC_COMMAND_STAGE_MISMATCH"
    ]
    assert len(stage_issues) == 5


def test_mpi_requires_decomposition_and_requested_reconstruction(
    tmp_path: Path,
):
    no_decomposition = _plan(mpi_ranks=4)
    _materialize(tmp_path / "no-decompose", no_decomposition)
    no_reconstruct = _plan(mpi_ranks=4, decompose=True)
    _materialize(tmp_path / "no-reconstruct", no_reconstruct)

    first = inspect_semantics(
        tmp_path / "no-decompose",
        _task(),
        no_decomposition,
    )
    second = inspect_semantics(
        tmp_path / "no-reconstruct",
        _task(reconstruct=True),
        no_reconstruct,
    )

    assert {
        "SEMANTIC_MPI_DECOMPOSE_CONFIG_MISSING",
        "SEMANTIC_MPI_DECOMPOSE_STAGE_MISSING",
    } <= _codes(first)
    assert "SEMANTIC_RECONSTRUCT_STAGE_MISSING" in _codes(second)


def test_every_blocking_semantic_issue_has_rule_provenance(tmp_path: Path):
    plan = _plan(application="simpleFoam", mpi_ranks=4)
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    assert report.issues
    for issue in report.issues:
        assert issue.severity == "error"
        assert issue.provenance is not None
        assert issue.provenance.rule_id
        assert issue.provenance.openfoam_distribution == "foundation"
        assert issue.provenance.openfoam_version == "10"
        assert issue.provenance.source
        assert issue.provenance.tested_by


def test_unknown_solver_family_is_advisory_not_blocking(tmp_path: Path):
    plan = _plan(solver="newResearchFoam")
    plan.manifest.solver_family = "unregistered-research"
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    assert report.issues == []
    assert "SEMANTIC_FAMILY_UNREGISTERED" in {
        issue.code for issue in report.advisories
    }


def test_region_aware_manifest_accepts_fluid_solid_cht_layout(
    tmp_path: Path,
):
    manifest = CaseManifest(
        solver_executable="chtMultiRegionFoam",
        solver_family="conjugate-heat-transfer",
        regime="transient",
        physics_family="conjugate_heat_transfer",
        mesh_family="splitMeshRegions",
        dimensionality="2d",
        regions=[
            CaseRegion(name="fluid", kind="fluid", path_prefix="fluid"),
            CaseRegion(name="solid", kind="solid", path_prefix="solid"),
        ],
        fields=[
            CaseField(
                name="T",
                region="fluid",
                path="0/fluid/T",
                role="temperature",
                created_by="author",
            ),
            CaseField(
                name="T",
                region="solid",
                path="0/solid/T",
                role="temperature",
                created_by="author",
            ),
        ],
        patches=[],
    )
    plan = ExecutionPlan(
        schema_version=3,
        manifest=manifest,
        files=[
            GeneratedFile(
                path="system/controlDict",
                content=(
                    _header("controlDict")
                    + "application chtMultiRegionFoam;\n"
                ),
            ),
            GeneratedFile(
                path="0/fluid/T",
                content=_header("T", "volScalarField"),
            ),
            GeneratedFile(
                path="0/solid/T",
                content=_header("T", "volScalarField"),
            ),
        ],
        commands=[
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="chtMultiRegionFoam",
                timeout_seconds=120,
            )
        ],
    )
    _materialize(tmp_path, plan)

    report = inspect_semantics(tmp_path, _task(), plan)

    assert report.issues == []


def test_native_inspection_composes_semantic_report(tmp_path: Path):
    plan = _plan(application="simpleFoam")
    _materialize(tmp_path, plan)

    report = inspect_native_case(
        case_root=tmp_path,
        task=_task(),
        plan=plan,
        available_executables={
            "blockMesh",
            "checkMesh",
            "icoFoam",
        },
    )

    issue = next(
        item
        for item in report.issues
        if item.code == "SEMANTIC_APPLICATION_MISMATCH"
    )
    assert issue.provenance is not None
