from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.authoring import CaseBundle
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.extensions import CapabilityRegistry
from foampilot.plans import GeneratedFile, compile_execution_plan
from foampilot.plans.compiler import PlanCompilationError
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload
from tests.test_plan_extensions import _context


def _task(*, max_wall_seconds: int = 120) -> TaskSpec:
    return TaskSpec.model_validate(
        canonical_task_payload(
            {
                "schema_version": 3,
                "task_id": "compile-plan-test",
                "title": "Compile plan test",
                "request_text": "Compile one deterministic pisoFoam plan.",
                "openfoam_target": {
                    "distribution": "foundation",
                    "version": "10",
                },
                "resource_budget": {
                    "max_attempts": 1,
                    "max_wall_seconds": max_wall_seconds,
                    "max_mpi_ranks": 4,
                    "memory_mib": 512,
                },
                "required_outputs": ["velocity"],
                "acceptance_intent": ["normal completion"],
                "protected_paths": [],
            }
        )
    )


def _environment(*, missing: tuple[str, ...] = ()) -> EnvironmentSnapshot:
    names = {
        "blockMesh",
        "checkMesh",
        "decomposePar",
        "pisoFoam",
        "reconstructPar",
    } - set(missing)
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=Path("/opt/OpenFOAM/OpenFOAM-10"),
        tutorial_root=None,
        workspace_root=Path("/tmp/compile-plan-test"),
        workspace_writable=True,
        commands=[
            CommandFact(name=name, path=Path("/opt/openfoam/bin") / name)
            for name in sorted(names)
        ],
        mpi_launcher=Path("/usr/bin/mpirun"),
        gmsh=None,
        max_mpi_ranks=4,
    )


def _bundle(
    *,
    mesh: str = "provided",
    solver: str = "pisoFoam",
    ranks: int = 1,
) -> CaseBundle:
    context = _context(mesh=mesh)
    manifest = context.manifest.model_copy(
        update={"solver_executable": solver}
    )
    files = [
        GeneratedFile(
            path="system/controlDict",
            content=(
                "FoamFile { class dictionary; }\n"
                f"application {solver};\n"
            ),
        ),
        GeneratedFile(
            path="0/U",
            content="FoamFile { class volVectorField; }\n",
        ),
    ]
    if mesh == "blockMesh":
        files.append(
            GeneratedFile(
                path="system/blockMeshDict",
                content="FoamFile { class dictionary; }\nvertices ();\n",
            )
        )
    if ranks > 1:
        files.append(
            GeneratedFile(
                path="system/decomposeParDict",
                content=(
                    "FoamFile { class dictionary; }\n"
                    f"numberOfSubdomains {ranks};\n"
                ),
            )
        )
    return CaseBundle(manifest=manifest, files=files)


def _compile(*, mesh: str = "provided", ranks: int = 1):
    context = _context(mesh=mesh, ranks=ranks)
    return compile_execution_plan(
        design=context.design,
        bundle=_bundle(mesh=mesh, ranks=ranks),
        environment=_environment(),
        task=_task(),
        registry=CapabilityRegistry.planning_first_party(),
    )


def test_compiler_uses_registered_contributors_only() -> None:
    plan = _compile(mesh="provided")

    assert plan.compiled_from_design_sha256 == _context().design.design_sha256
    assert set(plan.compiler_identities) == {
        "foampilot.mesh.openfoam-provided",
        "foampilot.solver.foundation10-serial",
    }
    assert [item.executable for item in plan.commands] == [
        "checkMesh",
        "pisoFoam",
    ]


def test_compiler_rejects_manifest_solver_mismatch() -> None:
    context = _context()

    with pytest.raises(
        PlanCompilationError,
        match="DESIGN_MANIFEST_MISMATCH",
    ):
        compile_execution_plan(
            design=context.design,
            bundle=_bundle(solver="icoFoam"),
            environment=_environment(),
            task=_task(),
            registry=CapabilityRegistry.planning_first_party(),
        )


def test_compiler_rejects_missing_required_authored_path() -> None:
    context = _context(mesh="blockMesh")
    bundle = _bundle(mesh="blockMesh").model_copy(
        update={
            "files": [
                item
                for item in _bundle(mesh="blockMesh").files
                if item.path != "system/blockMeshDict"
            ]
        }
    )

    with pytest.raises(
        PlanCompilationError,
        match="REQUIRED_AUTHORED_PATH_MISSING",
    ):
        compile_execution_plan(
            design=context.design,
            bundle=bundle,
            environment=_environment(),
            task=_task(),
            registry=CapabilityRegistry.planning_first_party(),
        )


def test_compiler_rejects_unavailable_command_and_target_drift() -> None:
    context = _context()
    with pytest.raises(
        PlanCompilationError,
        match="PLAN_EXECUTABLE_UNAVAILABLE",
    ):
        compile_execution_plan(
            design=context.design,
            bundle=_bundle(),
            environment=_environment(missing=("pisoFoam",)),
            task=_task(),
            registry=CapabilityRegistry.planning_first_party(),
        )

    changed_target = _task().model_copy(
        update={
            "openfoam_target": _task().openfoam_target.model_copy(
                update={"version": "13"}
            )
        }
    )
    with pytest.raises(
        PlanCompilationError,
        match="PLAN_TARGET_MISMATCH",
    ):
        compile_execution_plan(
            design=context.design,
            bundle=_bundle(),
            environment=_environment(),
            task=changed_target,
            registry=CapabilityRegistry.planning_first_party(),
        )


def test_parallel_compiler_preserves_runner_owned_mpi_shape() -> None:
    plan = _compile(mesh="provided", ranks=4)

    solve = next(item for item in plan.commands if item.stage == "solve")
    assert solve.executable == "pisoFoam"
    assert solve.mpi_ranks == 4
    assert all(item.executable != "mpirun" for item in plan.commands)
    assert "system/decomposeParDict" in {item.path for item in plan.files}
