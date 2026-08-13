from __future__ import annotations

import json
from pathlib import Path

import pytest

from foampilot.acceptance import AcceptanceRequest, AcceptanceScope
from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseModels,
    CasePatch,
    CaseRegion,
)
from foampilot.plans import ExecutionPlan, GeneratedFile, NativeCommand
from foampilot.observations import ObservationRequest, ObservationScope, TimeSelection
from foampilot.runtime import resolve_runtime_config, run_preflight
from foampilot.simulation import FactEvidence, SimulationIntent
from foampilot.tasks import load_task_spec
from tests.test_native_case_generation import RecordingModel


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "examples/tasks/provided-poly-mesh.yaml"


def _observation(
    observation_id: str,
    kind: str,
    *,
    scope: str,
    names: tuple[str, ...] = (),
    time: str = "final",
) -> ObservationRequest:
    dimensions = {
        "residual": "1",
        "continuity": "1",
        "flow_rate": "0 3 -1 0 0 0 0",
        "pressure_difference": "0 2 -2 0 0 0 0",
        "region_average": "0 1 -1 0 0 0 0",
    }
    return ObservationRequest(
        observation_id=observation_id,
        kind=kind,
        quantity=kind,
        dimension=dimensions[kind],
        scope=ObservationScope(kind=scope, names=names),
        time_selection=TimeSelection(kind=time),
        provenance=(FactEvidence(kind="user_quote", detail=observation_id),),
    )


class ObservedProvidedMeshModel(RecordingModel):
    def generate_structured(self, request, schema, **kwargs):
        result = super().generate_structured(request, schema, **kwargs)
        if schema is not SimulationIntent:
            return result
        requests = (
            _observation("residual-history", "residual", scope="global", time="history"),
            _observation("continuity-history", "continuity", scope="global", time="history"),
            _observation("inlet-flow", "flow_rate", scope="patch", names=("inlet",)),
            _observation("outlet-flow", "flow_rate", scope="patch", names=("outlet",)),
            _observation(
                "pressure-drop",
                "pressure_difference",
                scope="patch_pair",
                names=("inlet", "outlet"),
            ),
            _observation(
                "zone-average-velocity",
                "region_average",
                scope="cell_zone",
                names=("zoneA",),
            ).model_copy(update={"quantity": "velocity_magnitude"}),
        )
        acceptance = AcceptanceRequest(
            condition_id="continuity-limit",
            observation=requests[1],
            operator="less_equal",
            limit=1.0e-5,
            unit="1",
            scope=AcceptanceScope(time="latest"),
            source="user_text",
            confirmed=True,
            provenance=(
                FactEvidence(
                    kind="user_quote",
                    detail="absolute cumulative continuity <= 1e-5",
                ),
            ),
        )
        return result.model_copy(
            update={
                "value": result.value.model_copy(
                    update={
                        "observation_requests": requests,
                        "acceptance_requests": (acceptance,),
                    }
                )
            }
        )


def _provided_plan() -> ExecutionPlan:
    patches = [
        CasePatch(name=name, region="default", mesh_type=patch_type)
        for name, patch_type in (
            ("inlet", "patch"),
            ("outlet", "patch"),
            ("top", "symmetryPlane"),
            ("bottom", "symmetryPlane"),
            ("frontAndBack", "empty"),
        )
    ]
    return ExecutionPlan(
        schema_version=4,
        compiled_from_design_sha256="a" * 64,
        compiler_identities={"test.fixture": "1.0.0/protocol-1"},
        manifest=CaseManifest(
            solver_executable="icoFoam",
            solver_family="incompressible-laminar",
            regime="transient",
            physics_family="fluid",
            mesh_family="provided",
            dimensionality="2d",
            regions=[CaseRegion(name="default", kind="fluid", path_prefix="")],
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
            patches=patches,
            models=CaseModels(transport="Newtonian"),
        ),
        files=[
            GeneratedFile(
                path="0/U",
                content="""FoamFile
{
    version 2.0;
    format ascii;
    class volVectorField;
    location \"0\";
    object U;
}
dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField
{
    inlet { type fixedValue; value uniform (0.1 0 0); }
    outlet { type zeroGradient; }
    top { type symmetryPlane; }
    bottom { type symmetryPlane; }
    frontAndBack { type empty; }
}
""",
            ),
            GeneratedFile(
                path="0/p",
                content="""FoamFile
{
    version 2.0;
    format ascii;
    class volScalarField;
    location \"0\";
    object p;
}
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    inlet { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    top { type symmetryPlane; }
    bottom { type symmetryPlane; }
    frontAndBack { type empty; }
}
""",
            ),
            GeneratedFile(
                path="constant/physicalProperties",
                content="""FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    location \"constant\";
    object physicalProperties;
}
nu [0 2 -1 0 0 0 0] 0.01;
""",
            ),
            GeneratedFile(
                path="system/controlDict",
                content="""FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    location \"system\";
    object controlDict;
}
application icoFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 0.04;
deltaT 0.01;
writeControl timeStep;
writeInterval 1;
purgeWrite 0;
writeFormat ascii;
runTimeModifiable false;
""",
            ),
            GeneratedFile(
                path="system/fvSchemes",
                content="""FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    location \"system\";
    object fvSchemes;
}
ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""",
            ),
            GeneratedFile(
                path="system/fvSolution",
                content="""FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    location \"system\";
    object fvSolution;
}
solvers
{
    p { solver PCG; preconditioner DIC; tolerance 1e-10; relTol 0; }
    pFinal { $p; relTol 0; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0; }
}
PISO
{
    nCorrectors 2;
    nNonOrthogonalCorrectors 0;
    pRefCell 0;
    pRefValue 0;
}
""",
            ),
        ],
        commands=[
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="icoFoam",
                timeout_seconds=20,
            )
        ],
    )


@pytest.mark.real_openfoam
def test_real_provided_mesh_vertical_gate(tmp_path: Path) -> None:
    runtime_file = tmp_path / "runtime.toml"
    runtime_file.write_text("schema_version = 1\n", encoding="utf-8")
    try:
        runtime = resolve_runtime_config(
            environ={},
            user_config=runtime_file,
            candidate_roots=(ROOT.parent / "OpenFOAM-10",),
        ).config
        preflight = run_preflight(runtime, workspace_root=tmp_path)
    except (OSError, RuntimeError, ValueError) as error:
        pytest.skip(f"OPENFOAM10_NOT_AVAILABLE: {error}")
    if not preflight.ok:
        pytest.skip(
            "OPENFOAM10_NOT_AVAILABLE: "
            f"{preflight.failure_code or preflight.failure_message}"
        )

    model = ObservedProvidedMeshModel([_provided_plan()])
    outcome = NativeAgent(
        gateway=model,
        runtime_config=runtime,
        artifact_store=ArtifactStore(tmp_path / "runs"),
    ).solve(load_task_spec(TASK), public_asset_root=ROOT)

    assert outcome.status == "RUN_COMPLETED", outcome.summary
    bundles = json.loads(
        (outcome.run_dir / "asset-bundles.json").read_text(encoding="utf-8")
    )
    assert bundles[0]["install_path"] == "constant/polyMesh"
    input_facts = json.loads(
        (outcome.run_dir / "input-mesh-facts.json").read_text(encoding="utf-8")
    )
    assert input_facts[0]["cells"] == 2
    executed = json.loads(
        (outcome.run_dir / "pre-authoring-mesh-facts.json").read_text(
            encoding="utf-8"
        )
    )
    assert executed[0]["mesh_check"]["mesh_ok"] is True
    assert all(
        not item.path.startswith("constant/polyMesh")
        for item in _provided_plan().files
    )
    prompt = model.requests[0].user_prompt
    assert '"authoritative_input_mesh_facts"' in prompt
    assert '"cells": 2' in prompt
    assert "4(1 4 10 7)" not in prompt
    case = outcome.run_dir / "attempt-01/case"
    assert (case / "constant/polyMesh/cellZones").is_file()
    assert (case / "0.04/U").is_file()
    run_facts = json.loads(
        (outcome.run_dir / "attempt-01/run-facts.json").read_text(encoding="utf-8")
    )
    assert run_facts["mesh_checks"][0]["mesh_ok"] is True
    assert run_facts["solver_progress"][-1]["completed_normally"] is True
    assert run_facts["residuals"]
    assert run_facts["continuity"]
    metrics = json.loads(
        (outcome.run_dir / "derived-metrics.json").read_text(encoding="utf-8")
    )
    by_id = {item["observation_id"]: item for item in metrics["series"]}
    for observation_id in (
        "inlet-flow",
        "outlet-flow",
        "pressure-drop",
        "zone-average-velocity",
    ):
        assert by_id[observation_id]["status"] == "AVAILABLE", by_id[observation_id]
        assert by_id[observation_id]["samples"]
    report = json.loads(
        (outcome.run_dir / "result-report.json").read_text(encoding="utf-8")
    )
    assert report["verdict"] == "PASS"
    assert [item["condition_id"] for item in report["conditions"]] == [
        "continuity-limit"
    ]
    assert report["conditions"][0]["status"] == "PASS"
    assert ArtifactStore(tmp_path / "runs").verify(outcome.run_dir) == []
