from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest
import yaml

from foampilot.acceptance import (
    AcceptanceCompiler,
    AcceptanceEvaluator,
    AcceptanceRequest,
    AcceptanceScope,
)
from foampilot.assets import (
    AssetBundle,
    BundleMember,
    OpenFOAMPolyMeshAdapter,
    compute_bundle_manifest_sha256,
)
from foampilot.authoring import CaseBundle
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.evidence import OpenFOAM10EvidenceExtractor, assess_native_run
from foampilot.extensions import CapabilityRegistry
from foampilot.manifests import CaseManifest, CaseModels, CaseRegion
from foampilot.observations import (
    ObservationPlanner,
    ObservationRequest,
    ObservationScope,
    TimeSelection,
    first_party_observation_registry,
    collect_foundation10_observation_artifacts,
    inject_observation_fragments,
    verify_observation_field_dimensions,
)
from foampilot.plans import GeneratedFile, PlanCompilationError, compile_execution_plan
from foampilot.postprocessing import PostProcessingEngine, foundation10_calculators
from foampilot.preprocessing import InputMeshFacts, inspect_poly_mesh
from foampilot.repair import RepairPolicy, coordinate_repair
from foampilot.runtime import PlanRunResult, PlanStepResult, ReusedStepResult
from foampilot.simulation import (
    FactEvidence,
    ResolvedValue,
    RiskDecision,
    SimulationIntent,
    canonical_sha256,
    freeze_case_design,
)
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
from foampilot.tasks import OpenFOAMTarget, PublicAsset, ResourceBudget, TaskSpec


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/contract_first"
POLYMESH = ROOT / "tests/fixtures/poly_mesh/minimal"
START = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _mesh_bundle(tmp_path: Path) -> tuple[Path, AssetBundle, PublicAsset]:
    source_root = tmp_path / "public"
    mesh_root = source_root / "mesh"
    shutil.copytree(POLYMESH, mesh_root)
    members = tuple(
        BundleMember(
            relative_path=path.name,
            logical_name=path.name,
            sha256=_sha(path),
            bytes=path.stat().st_size,
        )
        for path in sorted(mesh_root.iterdir())
        if path.is_file()
    )
    values = {
        "adapter_id": "foampilot.asset.openfoam-poly-mesh",
        "kind": "openfoam_poly_mesh",
        "source_path": "mesh",
        "install_path": "constant/polyMesh",
        "region": None,
        "members": members,
    }
    digest = compute_bundle_manifest_sha256(**values)
    declaration = PublicAsset(
        path="mesh",
        sha256=digest,
        purpose="immutable public replay mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256=digest,
    )
    bundle = OpenFOAMPolyMeshAdapter().inspect(source_root, declaration)
    return mesh_root, bundle, declaration


def _fact(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="user_text",
        impact="high",
        evidence=(
            FactEvidence(kind="test_fixture", detail=f"fixture value for {path}"),
        ),
        confirmed=True,
    )


def _request(
    observation_id: str,
    kind: str,
    *,
    scope: str = "global",
    names: tuple[str, ...] = (),
    time: str = "final",
    quantity: str | None = None,
    dimension: str | None = None,
    region: str | None = None,
) -> ObservationRequest:
    defaults = {
        "residual": ("residual", "1"),
        "continuity": ("continuity", "1"),
        "flow_rate": ("volumetric_flow_rate", "0 3 -1 0 0 0 0"),
        "pressure_difference": ("pressure_difference", "0 2 -2 0 0 0 0"),
        "region_average": ("velocity_magnitude", "0 1 -1 0 0 0 0"),
    }
    default_quantity, default_dimension = defaults[kind]
    return ObservationRequest(
        observation_id=observation_id,
        kind=kind,
        quantity=quantity or default_quantity,
        dimension=dimension or default_dimension,
        scope=ObservationScope(kind=scope, names=names, region=region),
        time_selection=TimeSelection(kind=time),
        provenance=(FactEvidence(kind="user_quote", detail=observation_id),),
    )


def _intent(
    scenario: dict[str, object],
    regions: tuple[str, ...],
) -> tuple[SimulationIntent, object]:
    common = (
        _request("residual-history", "residual", time="history"),
        _request("continuity-history", "continuity", time="history"),
    )
    solver = str(scenario["solver"])
    compressible = solver in {
        "rhoPimpleFoam",
        "rhoSimpleFoam",
        "rhoCentralFoam",
        "buoyantFoam",
        "chtMultiRegionFoam",
    }
    region = "fluid" if len(regions) > 1 else None
    flow = _request(
            "inlet-flow-history",
            "flow_rate",
            scope="patch",
            names=("inlet",),
            time="history",
            quantity="mass_flow_rate" if compressible else "volumetric_flow_rate",
            dimension=(
                "1 0 -1 0 0 0 0" if compressible else "0 3 -1 0 0 0 0"
            ),
            region=region,
        )
    pressure = _request(
            "pressure-drop",
            "pressure_difference",
            scope="patch_pair",
            names=("inlet", "outlet"),
            dimension=(
                "1 -1 -2 0 0 0 0" if compressible else "0 2 -2 0 0 0 0"
            ),
            region=region,
        )
    if len(regions) > 1:
        field = _request(
            "solid-average-temperature",
            "region_average",
            scope="region",
            names=("solid",),
            quantity="temperature",
            dimension="0 0 0 1 0 0 0",
            region="solid",
        )
    elif scenario["physics_family"] == "heat_transfer":
        field = _request(
            "zone-average-temperature",
            "region_average",
            scope="cell_zone",
            names=("zoneA",),
            quantity="temperature",
            dimension="0 0 0 1 0 0 0",
        )
    else:
        field = _request(
            "zone-average-velocity",
            "region_average",
            scope="cell_zone",
            names=("zoneA",),
        )
    requests = (*common, flow, pressure, field)
    statement = "absolute cumulative continuity <= 1e-5"
    condition = AcceptanceRequest(
        condition_id="continuity-limit",
        observation=requests[1],
        operator="less_equal",
        limit=1.0e-5,
        unit="1",
        scope=AcceptanceScope(time="latest"),
        source="user_text",
        confirmed=True,
        provenance=(FactEvidence(kind="user_quote", detail=statement),),
    )
    intent = SimulationIntent(
        facts=(_fact("physics.requested", "typed replay"),),
        constraints=("Foundation OpenFOAM 10",),
        observation_requests=requests,
        acceptance_requests=(condition,),
    )
    acceptance = AcceptanceCompiler().compile(
        observation_requests=requests,
        condition_requests=(condition,),
    )
    return intent, acceptance


def _design(scenario: dict[str, object], intent: SimulationIntent):
    mesh_strategy = str(scenario["mesh_strategy"])
    mesh_extension = (
        "foampilot.mesh.openfoam-provided"
        if mesh_strategy == "provided"
        else "foampilot.mesh.block-mesh"
    )
    proposal = CaseDesignProposal(
        solver_family=_fact("solver.family", str(scenario["solver"])),
        physical_models=(
            _fact("physics.regime", str(scenario["regime"])),
            _fact("physics.family", str(scenario["physics_family"])),
        ),
        materials=(),
        boundary_designs=(
            _fact("boundaries.inlet.mesh_type", "patch"),
            _fact("boundaries.outlet.mesh_type", "patch"),
        ),
        initial_conditions=(),
        time_design=(),
        numerical_design=(),
        region_models=tuple(
            _fact(f"regions.{region}.kind", "solid" if region == "solid" else "fluid")
            for region in scenario.get("regions", [])
        ) + (_fact("cell_zones.zoneA.kind", "fluid"),),
        extension_decisions=(
            ExtensionDecision(
                extension_id=mesh_extension,
                schema_version=1,
                values=(_fact("mesh.strategy", mesh_strategy),),
                provenance=(FactEvidence(kind="test_fixture", detail="mesh route"),),
            ),
            ExtensionDecision(
                extension_id="foampilot.solver.foundation10-serial",
                schema_version=1,
                values=(_fact("execution.mpi_ranks", 1),),
                provenance=(FactEvidence(kind="test_fixture", detail="serial route"),),
            ),
        ),
        uncertainties=(),
        alternatives=(),
        reasoning_evidence=(
            FactEvidence(kind="test_fixture", detail="coherent replay design"),
        ),
        capability_conflicts=(),
    )
    registry = CapabilityRegistry.planning_first_party()
    identities = {
        item.extension_id: (
            f"{registry.descriptor(item.extension_id).extension_version}/"
            f"protocol-{registry.descriptor(item.extension_id).protocol_version}"
        )
        for item in proposal.extension_decisions
    }
    decision = RiskDecision(
        state="READY_TO_AUTHOR",
        questions=(),
        reason_codes=("DESIGN_FACTS_RESOLVED",),
        proposal_sha256=canonical_sha256(proposal),
        required_extension_ids=tuple(sorted(identities)),
        required_extension_identities=identities,
    )
    return freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=intent,
    )


def _mesh_facts(
    facts: InputMeshFacts,
    regions: tuple[str, ...],
) -> tuple[InputMeshFacts, ...]:
    if regions == ("default",):
        return (facts,)
    return tuple(facts.model_copy(update={"region": region}) for region in regions)


def _manifest(scenario: dict[str, object], regions: tuple[str, ...]) -> CaseManifest:
    return CaseManifest(
        solver_executable=str(scenario["solver"]),
        solver_family=str(scenario["solver_family"]),
        regime=str(scenario["regime"]),
        physics_family=str(scenario["physics_family"]),
        mesh_family=str(scenario["mesh_strategy"]),
        dimensionality="2d",
        regions=[
            CaseRegion(
                name=name,
                kind="solid" if name == "solid" else "fluid",
                path_prefix="" if name == "default" else f"constant/{name}",
            )
            for name in regions
        ],
        models=CaseModels(
            transport="Newtonian",
            thermophysical=(
                "hePsiThermo"
                if scenario["physics_family"] in {"compressible", "heat_transfer"}
                else None
            ),
        ),
    )


def _bundle(
    scenario: dict[str, object],
    manifest: CaseManifest,
    observation_plan,
) -> CaseBundle:
    files = [
        GeneratedFile(
            path="system/controlDict",
            content=(
                "FoamFile{}\n"
                f"application {scenario['solver']};\n"
                "endTime 1;\ndeltaT 0.1;\n"
            ),
        ),
        GeneratedFile(path="system/fvSchemes", content="FoamFile{}\n"),
        GeneratedFile(path="system/fvSolution", content="FoamFile{}\n"),
    ]
    if scenario["mesh_strategy"] == "blockMesh":
        files.append(
            GeneratedFile(
                path="system/blockMeshDict",
                content="FoamFile{}\nconvertToMeters 1;\nvertices ();\n",
            )
        )
    authored = CaseBundle(manifest=manifest, files=files)
    return inject_observation_fragments(authored, observation_plan)[0]


def _task(
    scenario: dict[str, object],
    declaration: PublicAsset | None,
) -> TaskSpec:
    provided = scenario["mesh_strategy"] == "provided"
    target_version = str(scenario.get("target_version", "10"))
    return TaskSpec(
        task_id=f"replay-{scenario['id']}",
        title=f"contract-first replay {scenario['id']}",
        request_text=(
            "Replay one immutable contract-first scenario; absolute cumulative "
            "continuity <= 1e-5."
        ),
        openfoam_target=OpenFOAMTarget(
            distribution="foundation",
            version=target_version,
        ),
        resource_budget=ResourceBudget(
            max_attempts=2,
            max_wall_seconds=600,
            max_mpi_ranks=1,
            memory_mib=512,
        ),
        required_outputs=["U", "p"],
        acceptance_intent=["absolute cumulative continuity <= 1e-5"],
        public_assets=[declaration] if provided and declaration is not None else [],
        explicit_facts=[
            _fact(
                "mesh.intent",
                {"strategy": "provided" if provided else "blockMesh"},
            )
        ],
    )


def _environment(scenario: dict[str, object]) -> EnvironmentSnapshot:
    names = {"checkMesh", "postProcess", str(scenario["solver"])}
    if scenario["mesh_strategy"] == "blockMesh":
        names.add("blockMesh")
    version = str(scenario.get("target_version", "10"))
    root = Path(f"/opt/OpenFOAM/OpenFOAM-{version}")
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version=version,
        openfoam_root=root,
        tutorial_root=None,
        workspace_root=Path("/tmp/foampilot-replay"),
        workspace_writable=True,
        commands=[
            CommandFact(name=name, path=root / "platforms/bin" / name)
            for name in sorted(names)
        ],
        mpi_launcher=None,
        gmsh=None,
        max_mpi_ranks=1,
    )


def _step_log(scenario: dict[str, object], stage: str) -> bytes:
    if stage == "check":
        return (FIXTURES / "checkmesh-ok.log").read_bytes()
    if stage == "solve":
        name = (
            "solver-region-normal.log"
            if scenario["id"] == "region_case"
            else "solver-normal.log"
        )
        return (FIXTURES / name).read_bytes()
    return b"End\n"


def _run_result(
    tmp_path: Path,
    scenario: dict[str, object],
    plan,
    *,
    mode: str = "normal",
) -> PlanRunResult:
    case = tmp_path / "run-replay" / "attempt-01" / "case"
    logs = case / ".foampilot/logs"
    logs.mkdir(parents=True, exist_ok=True)
    steps = []
    reused = []
    commands = list(plan.commands)
    if mode in {"failed", "cancelled"}:
        commands = commands[: next(i for i, item in enumerate(commands) if str(item.stage) == "solve") + 1]
    if mode == "resumed":
        for command in commands:
            if str(command.stage) in {"mesh", "check"}:
                reused.append(
                    ReusedStepResult(
                        step_id=command.step_id,
                        stage=str(command.stage),
                        executable=command.executable,
                        source_kind="parent_attempt",
                        source_id="attempt-01",
                        reason_codes=["CANCELLATION_RESUME_REUSE"],
                    )
                )
        commands = [item for item in commands if str(item.stage) not in {"mesh", "check"}]
    for index, command in enumerate(commands):
        stdout = logs / f"{mode}-{command.step_id}.stdout.log"
        stderr = logs / f"{mode}-{command.step_id}.stderr.log"
        payload = _step_log(scenario, str(command.stage))
        return_code = 0
        cancelled = False
        if str(command.stage) == "solve" and mode == "failed":
            payload = (FIXTURES / "solver-failed.log").read_bytes()
            return_code = 1
        elif str(command.stage) == "solve" and mode == "cancelled":
            payload = b"Time = 0.1\n"
            return_code = 130
            cancelled = True
        stdout.write_bytes(payload)
        stderr.write_bytes(b"")
        steps.append(
            PlanStepResult(
                step_id=command.step_id,
                command=[command.executable, *command.args],
                return_code=return_code,
                started_at=START + timedelta(seconds=index),
                finished_at=START + timedelta(seconds=index + 1),
                elapsed_seconds=1.0,
                timed_out=False,
                cancelled=cancelled,
                stdout_path=stdout,
                stderr_path=stderr,
                execution_backend="host",
            )
        )
    if mode in {"normal", "resumed"}:
        compressible = str(scenario["solver"]) in {
            "rhoPimpleFoam",
            "rhoSimpleFoam",
            "rhoCentralFoam",
            "buoyantFoam",
            "chtMultiRegionFoam",
        }
        dimensions = {
            "U": "0 1 -1 0 0 0 0",
            "p": (
                "1 -1 -2 0 0 0 0"
                if compressible
                else "0 2 -2 0 0 0 0"
            ),
            "phi": (
                "1 0 -1 0 0 0 0"
                if compressible
                else "0 3 -1 0 0 0 0"
            ),
            "T": "0 0 0 1 0 0 0",
        }
        regions = tuple(scenario.get("regions", ["default"]))
        fields_by_region = (
            {"fluid": ("U", "p", "phi"), "solid": ("T",)}
            if len(regions) > 1
            else {
                "default": (
                    ("U", "p", "phi", "T")
                    if scenario["physics_family"] == "heat_transfer"
                    else ("U", "p", "phi")
                )
            }
        )
        for region, field_names in fields_by_region.items():
            for name in field_names:
                output = case / "1"
                if region != "default":
                    output /= region
                output /= name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    f"FoamFile{{}}\ndimensions [{dimensions[name]}];\n",
                    encoding="utf-8",
                )
    return PlanRunResult(
        case_dir=case,
        steps=steps,
        failed_step_id=("solve" if mode in {"failed", "cancelled"} else None),
        cancelled=mode == "cancelled",
        reused_steps=reused,
    )


def _collected_artifacts(case_root: Path, observation_plan) -> dict[str, Path]:
    files = {
        "flow_rate": "surfaceFieldValue.dat",
        "pressure_difference": "fieldValueDelta.dat",
        "region_average": "volFieldValue.dat",
    }
    for item in observation_plan.items:
        filename = files.get(item.kind)
        if filename is None or item.evidence_strategy.kind not in {
            "postprocess_command",
            "runtime_configuration",
        }:
            continue
        output = case_root / "postProcessing"
        if item.scope.region is not None:
            output /= item.scope.region
        output = output / item.observation_id / "0" / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        if item.kind == "flow_rate":
            value = "-0.1"
        elif item.kind == "pressure_difference":
            value = "0.25"
        elif item.quantity in {"velocity", "velocity_magnitude", "region_average"}:
            value = "(0.08 0 0)"
        elif item.quantity == "temperature":
            value = "300"
        else:
            value = "1"
        output.write_text(
            f"# Time value\n1 {value}\n",
            encoding="utf-8",
        )
    return collect_foundation10_observation_artifacts(case_root, observation_plan)


def _load_scenarios() -> list[dict[str, object]]:
    payload = yaml.safe_load((FIXTURES / "matrix.yaml").read_text(encoding="utf-8"))
    return list(payload["scenarios"])


@pytest.mark.parametrize("scenario", _load_scenarios(), ids=lambda item: item["id"])
def test_contract_first_replay_matrix(
    tmp_path: Path,
    scenario: dict[str, object],
) -> None:
    regions = tuple(scenario.get("regions", ["default"]))
    declaration = None
    if scenario["mesh_strategy"] == "provided":
        mesh_root, asset_bundle, declaration = _mesh_bundle(tmp_path)
        inspected = inspect_poly_mesh(mesh_root, asset_bundle, length_unit="m")
        assert inspected.bundle_manifest_sha256 == asset_bundle.manifest_sha256
        assert inspected.raw_content_included is False
        mesh_facts = _mesh_facts(inspected, regions)
    else:
        mesh_facts = ()

    intent, acceptance_plan = _intent(scenario, regions)
    design = _design(scenario, intent)
    assert design.intent_sha256 == canonical_sha256(intent)
    observation_plan = ObservationPlanner().compile(
        intent=intent,
        design=design,
        mesh_facts=mesh_facts,
        registry=first_party_observation_registry(),
        acceptance_plan=acceptance_plan,
    )
    manifest = _manifest(scenario, regions)
    case_bundle = _bundle(scenario, manifest, observation_plan)
    assert case_bundle.manifest.solver_executable == design.proposal.solver_family.value
    assert any(item.path == "system/foampilot-observations" for item in case_bundle.files)

    task = _task(scenario, declaration)
    if not bool(scenario.get("supported", True)):
        with pytest.raises(PlanCompilationError, match=str(scenario["error_code"])):
            compile_execution_plan(
                design=design,
                bundle=case_bundle,
                environment=_environment(scenario),
                task=task,
                registry=CapabilityRegistry.planning_first_party(),
                observation_plan=observation_plan,
            )
        return

    plan = compile_execution_plan(
        design=design,
        bundle=case_bundle,
        environment=_environment(scenario),
        task=task,
        registry=CapabilityRegistry.planning_first_party(),
        observation_plan=observation_plan,
    )
    assert plan.compiled_from_design_sha256 == design.design_sha256
    if scenario["mesh_strategy"] == "provided":
        assert not any(str(item.stage) == "mesh" for item in plan.commands)
        assert all(
            not item.path.startswith("constant/polyMesh") for item in plan.files
        )
    else:
        assert any(item.executable == "blockMesh" for item in plan.commands)
        assert task.public_assets == []

    if scenario["transition"] == "failure_repair":
        failed_result = _run_result(tmp_path, scenario, plan, mode="failed")
        failed_facts = OpenFOAM10EvidenceExtractor().extract(
            failed_result, plan, failed_result.case_dir
        )
        failed_assessment = assess_native_run(failed_facts)
        assert failed_assessment.failure_layer == "SOLVER_FAILED"
        repair = coordinate_repair(
            category="mechanical",
            design=design,
            policy=RepairPolicy(),
        )
        assert repair.state == "MECHANICAL_PATCH"
    elif scenario["transition"] == "cancellation_resume":
        cancelled_result = _run_result(tmp_path, scenario, plan, mode="cancelled")
        cancelled_facts = OpenFOAM10EvidenceExtractor().extract(
            cancelled_result, plan, cancelled_result.case_dir
        )
        cancelled_assessment = assess_native_run(cancelled_facts)
        assert cancelled_assessment.reason_codes == ("COMMAND_CANCELLED",)

    mode = "resumed" if scenario["transition"] == "cancellation_resume" else "normal"
    run_result = _run_result(tmp_path, scenario, plan, mode=mode)
    run_facts = OpenFOAM10EvidenceExtractor().extract(
        run_result, plan, run_result.case_dir
    )
    assessment = assess_native_run(run_facts)
    assert assessment.ok is True
    assert run_facts.residuals
    assert run_facts.continuity
    assert run_facts.solver_progress[-1].completed_normally is True
    if mode == "resumed":
        assert run_facts.reused_steps

    metrics = PostProcessingEngine(
        calculators=foundation10_calculators()
    ).derive(
        observation_plan,
        run_facts,
        run_result.case_dir,
        (
            verify_observation_field_dimensions(
                run_result.case_dir,
                observation_plan,
            )
            or _collected_artifacts(run_result.case_dir, observation_plan)
        ),
    )
    assert metrics.run_facts_sha256 == canonical_sha256(run_facts)
    assert metrics.observation_plan_sha256 == observation_plan.canonical_sha256()
    assert all(item.status == "AVAILABLE" for item in metrics.series)

    report = AcceptanceEvaluator().evaluate(acceptance_plan, metrics)
    assert report.verdict == "PASS"
    assert [item.condition_id for item in report.conditions] == ["continuity-limit"]
    assert report.derived_metrics_sha256 == metrics.canonical_sha256()
    assert report.run_facts_sha256 == metrics.run_facts_sha256


def test_contract_first_fixture_ids_cover_required_scenarios() -> None:
    assert {item["id"] for item in _load_scenarios()} >= {
        "provided_poly_mesh",
        "generated_mesh",
        "region_case",
        "steady_incompressible",
        "transient_incompressible",
        "compressible",
        "heat_transfer",
        "multiphase",
        "failure_repair",
        "cancellation_resume",
        "unsupported_target",
    }
