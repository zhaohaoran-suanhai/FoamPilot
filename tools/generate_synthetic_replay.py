#!/usr/bin/env python3
"""确定性生成完全由 FoamPilot 拥有的 replay 回归资产。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

import yaml

from foampilot.agent.generation import materialize_case
from foampilot.inspection import inspect_native_case
from foampilot.plans import ExecutionPlan
from foampilot.runtime import PlanRunResult, PlanStepResult
from foampilot.tasks import TaskSpec
from foampilot.validation import (
    PublicValidationCheck,
    PublicValidationReport,
)


_SECRET_PATTERNS = (
    re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)"
        rb"\s*[:=]\s*[^\s,;]+"
    ),
)


@dataclass(frozen=True)
class SyntheticFixtureSpec:
    fixture_id: str
    kind: str
    dimensions: tuple[float, float, float]
    patches: tuple[str, str, str, str]
    expected_status: str
    mpi_ranks: int = 1
    include_field: bool = False
    thermal: bool = False
    multi_region: bool = False
    bad_pressure_dimension: bool = False


SPECS = (
    SyntheticFixtureSpec(
        "fp-single-box",
        "single_region_success",
        (0.037, 0.023, 0.001),
        ("drive", "return", "shell", "span"),
        "PUBLIC_VALIDATION_PASS",
    ),
    SyntheticFixtureSpec(
        "fp-mpi-duct",
        "mpi_success",
        (0.061, 0.019, 0.001),
        ("entry", "exit", "wallBand", "span"),
        "PUBLIC_VALIDATION_PASS",
        mpi_ranks=4,
    ),
    SyntheticFixtureSpec(
        "fp-included-field",
        "include_success",
        (0.029, 0.017, 0.001),
        ("feed", "drain", "jacket", "span"),
        "PUBLIC_VALIDATION_PASS",
        include_field=True,
    ),
    SyntheticFixtureSpec(
        "fp-heated-slot",
        "buoyant_success",
        (0.021, 0.047, 0.001),
        ("warm", "cool", "sealed", "span"),
        "PUBLIC_VALIDATION_PASS",
        thermal=True,
    ),
    SyntheticFixtureSpec(
        "fp-coupled-blocks",
        "multi_region_success",
        (0.032, 0.024, 0.001),
        ("fluidGate", "solidGate", "casing", "span"),
        "PUBLIC_VALIDATION_PASS",
        thermal=True,
        multi_region=True,
    ),
    SyntheticFixtureSpec(
        "fp-bad-dimension",
        "known_failure",
        (0.033, 0.018, 0.001),
        ("source", "sink", "skin", "span"),
        "STATIC_INSPECTION_FAILED",
        bad_pressure_dimension=True,
    ),
)


def _header(*, object_name: str, class_name: str = "dictionary") -> str:
    return (
        "FoamFile\n"
        "{\n"
        "    version 2.0;\n"
        "    format ascii;\n"
        f"    class {class_name};\n"
        f"    object {object_name};\n"
        "}\n\n"
    )


def _block_mesh(spec: SyntheticFixtureSpec) -> str:
    x, y, z = spec.dimensions
    first, second, third, fourth = spec.patches
    return _header(object_name="blockMeshDict") + f"""scale 1;
vertices
(
    (0 0 0)
    ({x:.3f} 0 0)
    ({x:.3f} {y:.3f} 0)
    (0 {y:.3f} 0)
    (0 0 {z:.3f})
    ({x:.3f} 0 {z:.3f})
    ({x:.3f} {y:.3f} {z:.3f})
    (0 {y:.3f} {z:.3f})
);
blocks
(
    hex (0 1 2 3 4 5 6 7) (8 6 1) simpleGrading (1 1 1)
);
edges ();
boundary
(
    {first}
    {{
        type patch;
        faces ((0 4 7 3));
    }}
    {second}
    {{
        type patch;
        faces ((1 2 6 5));
    }}
    {third}
    {{
        type wall;
        faces ((0 1 5 4) (3 7 6 2));
    }}
    {fourth}
    {{
        type empty;
        faces ((0 3 2 1) (4 5 6 7));
    }}
);
mergePatchPairs ();
"""


def _boundary_field(
    patches: tuple[str, str, str, str],
    *,
    vector: bool,
) -> str:
    zero = "uniform (0 0 0)" if vector else "uniform 0"
    lines = ["boundaryField", "{"]
    for patch in patches:
        patch_type = "empty" if patch == "span" else "fixedValue"
        lines.extend(
            [
                f"    {patch}",
                "    {",
                f"        type {patch_type};",
                *( [f"        value {zero};"] if patch_type != "empty" else [] ),
                "    }",
            ]
        )
    lines.extend(["}", ""])
    return "\n".join(lines)


def _field(
    *,
    name: str,
    vector: bool,
    patches: tuple[str, str, str, str],
    dimensions: str,
    include_internal: bool = False,
) -> str:
    class_name = "volVectorField" if vector else "volScalarField"
    internal = (
        '#include "U.internal.inc"'
        if include_internal
        else ("uniform (0 0 0);" if vector else "uniform 0;")
    )
    return (
        _header(object_name=name, class_name=class_name)
        + f"dimensions {dimensions};\n"
        + f"internalField {internal}\n"
        + _boundary_field(patches, vector=vector)
    )


def _case_files(spec: SyntheticFixtureSpec) -> dict[str, str]:
    prefix = "0/fluidZone" if spec.multi_region else "0"
    pressure_dimensions = (
        "[1 -1 -2 0 0 0 0]"
        if spec.bad_pressure_dimension
        else "[0 2 -2 0 0 0 0]"
    )
    files = {
        f"{prefix}/U": _field(
            name="U",
            vector=True,
            patches=spec.patches,
            dimensions="[0 1 -1 0 0 0 0]",
            include_internal=spec.include_field,
        ),
        f"{prefix}/p": _field(
            name="p",
            vector=False,
            patches=spec.patches,
            dimensions=pressure_dimensions,
        ),
        "constant/physicalProperties": (
            _header(object_name="physicalProperties")
            + "nu [0 2 -1 0 0 0 0] 0.00001;\n"
        ),
        "system/blockMeshDict": _block_mesh(spec),
        "system/controlDict": (
            _header(object_name="controlDict")
            + "application icoFoam;\n"
            + "startFrom startTime;\nstartTime 0;\nstopAt endTime;\n"
            + "endTime 0.02;\ndeltaT 0.01;\nwriteControl timeStep;\n"
            + "writeInterval 1;\n"
        ),
        "system/fvSchemes": (
            _header(object_name="fvSchemes")
            + "ddtSchemes { default Euler; }\n"
            + "gradSchemes { default Gauss linear; }\n"
            + "divSchemes { default none; div(phi,U) Gauss upwind; }\n"
            + "laplacianSchemes { default Gauss linear corrected; }\n"
            + "interpolationSchemes { default linear; }\n"
            + "snGradSchemes { default corrected; }\n"
        ),
        "system/fvSolution": (
            _header(object_name="fvSolution")
            + "solvers\n{\n"
            + "    p { solver PCG; preconditioner DIC; tolerance 1e-06; relTol 0; }\n"
            + "    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-06; relTol 0; }\n"
            + "}\nPISO { nCorrectors 2; nNonOrthogonalCorrectors 0; }\n"
        ),
    }
    if spec.include_field:
        files[f"{prefix}/U.internal.inc"] = "uniform (0 0 0);\n"
    if spec.thermal:
        temperature_prefix = (
            "0/solidZone" if spec.multi_region else "0"
        )
        files[f"{temperature_prefix}/T"] = _field(
            name="T",
            vector=False,
            patches=spec.patches,
            dimensions="[0 0 0 1 0 0 0]",
        ).replace("uniform 0", "uniform 300")
    if spec.mpi_ranks > 1:
        files["system/decomposeParDict"] = (
            _header(object_name="decomposeParDict")
            + f"numberOfSubdomains {spec.mpi_ranks};\n"
            + "method simple;\nsimpleCoeffs { n (4 1 1); delta 0.001; }\n"
        )
    return files


def _manifest(spec: SyntheticFixtureSpec, files: dict[str, str]) -> dict:
    if spec.multi_region:
        regions = [
            {"name": "fluidZone", "kind": "fluid", "path_prefix": "fluidZone"},
            {"name": "solidZone", "kind": "solid", "path_prefix": "solidZone"},
        ]
        fluid = "fluidZone"
        prefix = "0/fluidZone"
    else:
        regions = [{"name": "domain", "kind": "fluid", "path_prefix": ""}]
        fluid = "domain"
        prefix = "0"
    fields = [
        {"name": "U", "region": fluid, "path": f"{prefix}/U", "role": "velocity", "created_by": "author"},
        {"name": "p", "region": fluid, "path": f"{prefix}/p", "role": "kinematic pressure", "created_by": "author"},
    ]
    if spec.thermal:
        region = "solidZone" if spec.multi_region else fluid
        path = "0/solidZone/T" if spec.multi_region else "0/T"
        fields.append(
            {"name": "T", "region": region, "path": path, "role": "temperature", "created_by": "author"}
        )
    patches = []
    for index, name in enumerate(spec.patches):
        region = (
            "solidZone"
            if spec.multi_region and index == 1
            else fluid
        )
        patches.append(
            {"name": name, "region": region, "mesh_type": ("empty" if name == "span" else "patch")}
        )
    return {
        "solver_executable": "icoFoam",
        "solver_family": "synthetic-incompressible",
        "regime": "transient",
        "physics_family": (
            "synthetic-thermal" if spec.thermal else "synthetic-flow"
        ),
        "mesh_family": "synthetic-block",
        "dimensionality": "2d",
        "regions": regions,
        "fields": fields,
        "patches": patches,
        "models": {"turbulence": "laminar", "transport": "Newtonian"},
    }


def _commands(spec: SyntheticFixtureSpec) -> list[dict]:
    commands = [
        {"step_id": "mesh", "stage": "mesh", "executable": "blockMesh", "args": [], "mpi_ranks": 1, "timeout_seconds": 10},
        {"step_id": "mesh-check", "stage": "check", "executable": "checkMesh", "args": [], "mpi_ranks": 1, "timeout_seconds": 10},
    ]
    if spec.mpi_ranks > 1:
        commands.append(
            {"step_id": "decompose", "stage": "decompose", "executable": "decomposePar", "args": [], "mpi_ranks": 1, "timeout_seconds": 10}
        )
    commands.append(
        {"step_id": "solve", "stage": "solve", "executable": "icoFoam", "args": [], "mpi_ranks": spec.mpi_ranks, "timeout_seconds": 20}
    )
    if spec.mpi_ranks > 1:
        commands.append(
            {"step_id": "reconstruct", "stage": "reconstruct", "executable": "reconstructPar", "args": [], "mpi_ranks": 1, "timeout_seconds": 10}
        )
    return commands


def _plan(spec: SyntheticFixtureSpec) -> ExecutionPlan:
    files = _case_files(spec)
    return ExecutionPlan.model_validate(
        {
            "schema_version": 3,
            "manifest": _manifest(spec, files),
            "files": [
                {"path": path, "content": content}
                for path, content in sorted(files.items())
            ],
            "commands": _commands(spec),
        }
    )


def _task(spec: SyntheticFixtureSpec, plan: ExecutionPlan) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 2,
            "task_id": spec.fixture_id,
            "title": f"Synthetic replay {spec.fixture_id}",
            "prompt": (
                "Inspect a deterministic, repository-owned native case and "
                + ("reconstruct parallel results." if spec.mpi_ranks > 1 else "preserve its typed execution plan.")
            ),
            "openfoam_target": {"distribution": "foundation", "version": "10"},
            "resource_budget": {
                "max_attempts": 1,
                "max_wall_seconds": sum(item.timeout_seconds for item in plan.commands),
                "max_mpi_ranks": spec.mpi_ranks,
                "memory_mib": 1024,
            },
            "required_outputs": ["synthetic replay evidence"],
            "acceptance_requirements": ["deterministic static inspection"],
            "public_checks": [{"name": "synthetic-check", "kind": "completion", "parameters": {}}],
            "protected_paths": [],
        }
    )


def _write(path: Path, payload: bytes) -> None:
    if any(pattern.search(payload) for pattern in _SECRET_PATTERNS):
        raise ValueError(f"secret-like bytes rejected: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _json_bytes(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def generate_fixture(
    spec: SyntheticFixtureSpec,
    output_root: Path,
) -> Path:
    destination = output_root / spec.fixture_id
    if destination.exists():
        raise FileExistsError(f"fixture already exists: {destination}")
    destination.mkdir(parents=True)
    plan = _plan(spec)
    task = _task(spec, plan)
    case_root = destination / "case"
    materialize_case(plan, task, case_root)

    available = {command.executable for command in plan.commands}
    inspection = inspect_native_case(
        case_root=case_root,
        task=task,
        plan=plan,
        available_executables=available,
    )
    expected_failure = spec.expected_status != "PUBLIC_VALIDATION_PASS"
    if expected_failure == inspection.passed:
        raise RuntimeError(
            f"synthetic inspection expectation mismatch: {spec.fixture_id}"
        )
    validation = PublicValidationReport(
        checks=[
            PublicValidationCheck(
                name="synthetic-static-inspection",
                passed=inspection.passed,
                detail=(
                    "Synthetic case passed deterministic inspection."
                    if inspection.passed
                    else "Synthetic bad dimension was rejected."
                ),
            )
        ],
        failure_layer=(None if inspection.passed else "STATIC_INSPECTION_FAILED"),
        failed_step_id=(None if inspection.passed else "solve"),
    )
    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    steps: list[PlanStepResult] = []
    for command in plan.commands:
        stdout = Path("case/.foampilot/logs") / f"{command.step_id}.stdout.log"
        stderr = Path("case/.foampilot/logs") / f"{command.step_id}.stderr.log"
        _write(destination / stdout, f"synthetic {command.step_id}: complete\n".encode())
        _write(destination / stderr, b"")
        steps.append(
            PlanStepResult(
                step_id=command.step_id,
                command=[command.executable, *command.args],
                return_code=0,
                started_at=fixed,
                finished_at=fixed,
                timed_out=False,
                stdout_path=stdout,
                stderr_path=stderr,
                execution_backend="host",
            )
        )
    result = PlanRunResult(case_dir=Path("case"), steps=steps)
    summary = {
        "schema_version": 1,
        "task_id": spec.fixture_id,
        "status": spec.expected_status,
        "attempts": [{"attempt": 1, "status": spec.expected_status}],
        "message": "Deterministic synthetic replay fixture.",
    }
    _write(destination / "execution-plan.json", _json_bytes(plan))
    _write(destination / "task.json", _json_bytes(task))
    _write(destination / "static-inspection.json", _json_bytes(inspection))
    _write(destination / "public-validation.json", _json_bytes(validation))
    _write(destination / "run-result.json", _json_bytes(result))
    _write(destination / "summary.json", _json_bytes(summary))
    return destination


def _file_records(root: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
            }
        )
    return records


def generate_all(
    output_root: str | Path,
    *,
    replace: bool = False,
) -> Path:
    root = Path(output_root).resolve()
    if root in {Path("/"), Path.home().resolve()}:
        raise ValueError("unsafe synthetic output root")
    if root.exists():
        if not replace:
            raise FileExistsError(f"output root already exists: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    generator_sha256 = sha256(Path(__file__).read_bytes()).hexdigest()
    fixtures = []
    for spec in SPECS:
        destination = generate_fixture(spec, root)
        fixtures.append(
            {
                "fixture_id": spec.fixture_id,
                "kind": spec.kind,
                "source_kind": "synthetic_foampilot",
                "generator_sha256": generator_sha256,
                "expected": {"native_status": spec.expected_status},
                "files": _file_records(destination),
            }
        )
    index = {"schema_version": 2, "fixtures": fixtures}
    _write(
        root / "index.yaml",
        yaml.safe_dump(index, sort_keys=False).encode("utf-8"),
    )
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--replace", action="store_true")
    arguments = parser.parse_args()
    print(generate_all(arguments.output_root, replace=arguments.replace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
