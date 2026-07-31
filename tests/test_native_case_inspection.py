from __future__ import annotations

from pathlib import Path

from foampilot.inspection import inspect_native_case
from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseRegion,
)
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
)
from foampilot.tasks import TaskSpec


def _task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "task_id": "inspect-native",
            "title": "Inspect native case",
            "prompt": "Solve a two-dimensional laminar enclosure.",
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
            "acceptance_requirements": ["mesh quality"],
            "public_checks": [
                {
                    "name": "mesh-quality",
                    "kind": "mesh_ok",
                    "parameters": {},
                }
            ],
            "public_assets": [],
            "protected_paths": ["/private/golden/cavity"],
        }
    )


def _plan() -> ExecutionPlan:
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
                ),
                CaseField(
                    name="p",
                    region="default",
                    path="0/p",
                    role="kinematic_pressure",
                    created_by="author",
                ),
            ],
        ),
        files=[
            GeneratedFile(
                path="system/controlDict",
                content=_header("controlDict") + "application icoFoam;\n",
            ),
            GeneratedFile(
                path="system/blockMeshDict",
                content=_header("blockMeshDict"),
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
                path="constant/physicalProperties",
                content=_header("physicalProperties"),
            ),
            GeneratedFile(
                path="0/U",
                content=_header("U", klass="volVectorField"),
            ),
            GeneratedFile(
                path="0/p",
                content=_header("p", klass="volScalarField"),
            ),
        ],
        commands=[
            NativeCommand(
                step_id="mesh",
                stage="mesh",
                executable="blockMesh",
                args=[],
                mpi_ranks=1,
                timeout_seconds=30,
            ),
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="icoFoam",
                args=[],
                mpi_ranks=1,
                timeout_seconds=60,
            ),
        ],
    )


def _header(object_name: str, *, klass: str = "dictionary") -> str:
    return (
        "FoamFile\n"
        "{\n"
        "    format ascii;\n"
        f"    class {klass};\n"
        f"    object {object_name};\n"
        "}\n"
    )


def _write_declared_case(
    root: Path,
    *,
    application: str = "icoFoam",
    control_suffix: str = "",
) -> None:
    (root / "system").mkdir(parents=True)
    (root / "constant").mkdir()
    (root / "0").mkdir()
    (root / "system/controlDict").write_text(
        _header("controlDict")
        + f"application {application};\n"
        + control_suffix,
        encoding="utf-8",
    )
    (root / "system/blockMeshDict").write_text(
        _header("blockMeshDict")
        + """
boundary
(
    movingWall
    {
        type wall;
        faces ((0 1 2 3));
    }
    fixedWalls
    {
        type wall;
        faces ((4 5 6 7));
    }
);
""",
        encoding="utf-8",
    )
    (root / "system/fvSchemes").write_text(
        _header("fvSchemes"),
        encoding="utf-8",
    )
    (root / "system/fvSolution").write_text(
        _header("fvSolution"),
        encoding="utf-8",
    )
    (root / "constant/physicalProperties").write_text(
        _header("physicalProperties"),
        encoding="utf-8",
    )
    (root / "0/U").write_text(
        _header("U", klass="volVectorField")
        + """
dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField
{
    movingWall
    {
        type fixedValue;
        value uniform (1 0 0);
    }
    fixedWalls
    {
        type noSlip;
    }
}
""",
        encoding="utf-8",
    )
    (root / "0/p").write_text(
        _header("p", klass="volScalarField")
        + """
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    movingWall { type zeroGradient; }
    fixedWalls { type zeroGradient; }
}
""",
        encoding="utf-8",
    )


def _inspect(root: Path):
    return inspect_native_case(
        case_root=root,
        task=_task(),
        plan=_plan(),
        available_executables={"blockMesh", "icoFoam"},
    )


def test_inspection_accepts_agent_authored_minimal_case(
    tmp_path: Path,
) -> None:
    _write_declared_case(tmp_path)

    report = _inspect(tmp_path)

    assert report.passed
    assert report.observed_patches == ["fixedWalls", "movingWall"]


def test_inspection_rejects_protected_path_without_application_coupling(
    tmp_path: Path,
) -> None:
    _write_declared_case(
        tmp_path,
        application="simpleFoam",
        control_suffix="// /private/golden/cavity\n",
    )

    report = _inspect(tmp_path)

    codes = {issue.code for issue in report.issues}
    assert "PROTECTED_REFERENCE" in codes
    assert "APPLICATION_MISMATCH" not in codes


def test_inspection_reports_missing_header_and_unbalanced_file(
    tmp_path: Path,
) -> None:
    _write_declared_case(tmp_path)
    (tmp_path / "0/U").write_text(
        "boundaryField\n{\n    wall { type noSlip; }\n",
        encoding="utf-8",
    )

    report = _inspect(tmp_path)

    assert {issue.code for issue in report.issues} >= {
        "MISSING_FOAM_HEADER",
        "UNBALANCED_DELIMITERS",
    }


def test_inspection_reports_missing_declared_file(tmp_path: Path) -> None:
    _write_declared_case(tmp_path)
    (tmp_path / "system/blockMeshDict").unlink()

    report = _inspect(tmp_path)

    assert any(issue.code == "MISSING_DECLARED_FILE" for issue in report.issues)


def test_inspection_accepts_headerless_include_fragments(
    tmp_path: Path,
) -> None:
    _write_declared_case(tmp_path)
    (tmp_path / "constant").mkdir(exist_ok=True)
    fragment = "constant/values.inc"
    (tmp_path / fragment).write_text(
        "uniform 0;\n",
        encoding="utf-8",
    )
    plan = _plan()
    plan.files.append(
        GeneratedFile(path=fragment, content="uniform 0;\n")
    )

    report = inspect_native_case(
        case_root=tmp_path,
        task=_task(),
        plan=plan,
        available_executables={"blockMesh", "icoFoam"},
    )

    assert not any(
        issue.code == "MISSING_FOAM_HEADER"
        and issue.path == fragment
        for issue in report.issues
    )


def test_inspection_rejects_explicit_missing_field_patch(
    tmp_path: Path,
) -> None:
    _write_declared_case(tmp_path)
    velocity = tmp_path / "0/U"
    velocity.write_text(
        velocity.read_text(encoding="utf-8").replace(
            """
    fixedWalls
    {
        type noSlip;
    }
""",
            "",
        ),
        encoding="utf-8",
    )

    report = _inspect(tmp_path)

    issue = next(
        item
        for item in report.issues
        if item.code == "MISSING_FIELD_PATCH"
    )
    assert issue.path == "0/U"
    assert "fixedWalls" in issue.detail


def test_inspection_keeps_unresolved_patch_coverage_advisory(
    tmp_path: Path,
) -> None:
    _write_declared_case(tmp_path)
    (tmp_path / "0/U").write_text(
        _header("U", klass="volVectorField")
        + """
dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField
{
    #include "U.boundary"
}
""",
        encoding="utf-8",
    )

    report = _inspect(tmp_path)

    assert report.passed
    advisory = next(
        item
        for item in report.advisories
        if item.code == "PATCH_COVERAGE_UNVERIFIED"
    )
    assert advisory.path == "0/U"


def test_inspection_rejects_generated_shell_entrypoint(
    tmp_path: Path,
) -> None:
    _write_declared_case(tmp_path)
    allrun = tmp_path / "Allrun"
    allrun.write_text("#!/bin/sh\nicoFoam\n", encoding="utf-8")
    allrun.chmod(0o755)

    report = _inspect(tmp_path)

    assert any(issue.code == "GENERATED_SHELL" for issue in report.issues)


def test_inspection_rejects_foundation_v10_field_min_max(
    tmp_path: Path,
) -> None:
    _write_declared_case(
        tmp_path,
        control_suffix="""
functions
{
    alphaBounds
    {
        type fieldMinMax;
        fields (alpha.water);
    }
}
""",
    )

    report = _inspect(tmp_path)

    issue = next(
        item
        for item in report.issues
        if item.code == "UNSUPPORTED_OF10_FUNCTION_OBJECT"
    )
    assert issue.path == "system/controlDict"
    assert "volFieldValue" in issue.detail
