from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.knowledge import KnowledgeEntry, load_knowledge_corpus
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.routing import (
    CapabilityConfidence,
    RouteSuggestion,
    RoutingError,
    route_capability,
)
from foampilot.tasks import TaskSpec


def _task(prompt: str) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 2,
            "task_id": "route-test",
            "title": "Public routing task",
            "prompt": prompt,
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 2,
                "max_wall_seconds": 120,
                "max_mpi_ranks": 4,
                "memory_mib": 1024,
            },
            "required_outputs": ["velocity and pressure fields"],
            "acceptance_requirements": ["normal solver completion"],
            "public_checks": [
                {
                    "name": "completion",
                    "kind": "completion",
                    "parameters": {},
                }
            ],
            "protected_paths": ["/private/route-target"],
        }
    )


def _geometry_task(prompt: str, *, mode: str, strategy: str) -> TaskSpec:
    payload = _task(prompt).model_dump(mode="json")
    payload["geometry"] = {
        "mode": mode,
        "dimensionality": "three_d",
        "description": "Public geometry for routing",
        "length_unit": "m",
        "assets": (
            []
            if mode == "parametric"
            else [
                {
                    "path": "geometry/input.stl",
                    "format": "stl",
                    "role": "flow-domain",
                }
            ]
        ),
        "parameters": (
            {"length": {"value": 1.0, "unit": "m"}}
            if mode == "parametric"
            else {}
        ),
    }
    payload["mesh"] = {"strategy": strategy}
    if mode != "parametric":
        payload["public_assets"] = [
            {
                "path": "geometry/input.stl",
                "sha256": "b" * 64,
                "purpose": "public routing geometry",
            }
        ]
    return TaskSpec.model_validate(payload)


def _environment(*executables: str) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=Path("/opt/openfoam10"),
        tutorial_root=Path("/private/tutorials"),
        workspace_root=Path("/runs"),
        workspace_writable=True,
        commands=[
            CommandFact(
                name=name,
                path=Path("/opt/openfoam10/bin") / name,
            )
            for name in executables
        ],
        mpi_launcher=Path("/usr/bin/mpirun"),
        gmsh=None,
        max_mpi_ranks=4,
    )


def _solver_guide(
    solver: str,
    *,
    entry_id: str,
    title: str,
) -> KnowledgeEntry:
    return KnowledgeEntry.model_validate(
        {
            "schema_version": "1.0.0",
            "id": entry_id,
            "title": title,
            "fork": "foundation",
            "version": "10",
            "knowledge_type": "solver_guide",
            "solvers": [solver],
            "models": [],
            "tags": ["transient", "laminar", "incompressible"],
            "applicability": {
                "conditions": [
                    "Transient laminar incompressible single-phase flow."
                ],
                "not_applicable": ["Compressible flow."],
            },
            "source": {
                "kind": "official_source",
                "title": f"{solver} source",
                "locator": f"applications/solvers/{solver}",
                "sha256": "a" * 64,
                "license_spdx": "GPL-3.0-or-later",
            },
            "leakage": {
                "visibility": "public",
                "families": [],
                "contains_target_case_solution": False,
            },
            "content": {
                "summary": (
                    f"{solver} solves transient laminar incompressible flow."
                ),
                "rules": ["Use a coherent pressure-velocity coupling."],
                "failure_signals": [],
                "validation": ["Require normal completion."],
            },
        }
    )


def _route_budget():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.ROUTING,
        stage_deadline_seconds=30,
        max_transport_attempts=1,
    )


class RecordingRouteGateway:
    primary_backend_id = "recording"
    primary_model = "recording-route"
    policy_sha256 = "a" * 64

    def __init__(self, suggestion: RouteSuggestion) -> None:
        self.suggestion = suggestion
        self.requests = []

    def generate_structured(self, request, schema, *, budget, trace):
        del trace
        assert budget.stage == ModelStage.ROUTING
        assert schema is RouteSuggestion
        self.requests.append(request)
        return ModelResult(
            value=self.suggestion,
            logical_request_id="route-1",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def test_explicit_installed_solver_routes_with_system_computed_high_confidence():
    profile = route_capability(
        _task(
            "Use icoFoam for transient laminar incompressible "
            "single-phase flow."
        ),
        _environment("icoFoam", "blockMesh", "checkMesh"),
        (),
    )

    assert profile.solver_executable == "icoFoam"
    assert profile.solver_family == "incompressible-laminar"
    assert profile.regime == "transient"
    assert profile.compressibility == "incompressible"
    assert profile.confidence == CapabilityConfidence.HIGH
    assert any(
        evidence.source == "task.prompt"
        and "explicit solver icoFoam" in evidence.fact
        for evidence in profile.evidence
    )


@pytest.mark.parametrize(
    ("mode", "strategy", "expected"),
    (
        ("surface", "snappyHexMesh", "snappyHexMesh"),
        ("surface", "gmsh", "gmsh"),
        ("surface", "provided", "provided"),
    ),
)
def test_explicit_mesh_strategy_overrides_prompt_guess(
    mode: str,
    strategy: str,
    expected: str,
) -> None:
    profile = route_capability(
        _geometry_task(
            "Use icoFoam with blockMesh for transient laminar "
            "incompressible single-phase flow.",
            mode=mode,
            strategy=strategy,
        ),
        _environment(
            "icoFoam",
            "blockMesh",
            "snappyHexMesh",
            "gmsh",
            "gmshToFoam",
            "checkMesh",
        ),
        (),
    )

    assert profile.mesh_family == expected
    assert any(
        item.source == "task.mesh"
        and f"explicit mesh strategy {expected}" in item.fact
        for item in profile.evidence
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("parametric", "blockMesh"),
        ("surface", "snappyHexMesh"),
    ),
)
def test_auto_mesh_strategy_is_derived_from_geometry_mode(
    mode: str,
    expected: str,
) -> None:
    profile = route_capability(
        _geometry_task(
            "Use icoFoam for transient laminar incompressible "
            "single-phase flow.",
            mode=mode,
            strategy="auto",
        ),
        _environment("icoFoam", "blockMesh", "snappyHexMesh"),
        (),
    )

    assert profile.mesh_family == expected
    assert any(
        item.source == "task.geometry" for item in profile.evidence
    )


def test_explicit_external_mesh_strategy_requires_discovered_tool() -> None:
    with pytest.raises(RoutingError) as caught:
        route_capability(
            _geometry_task(
                "Use icoFoam for transient laminar incompressible "
                "single-phase flow.",
                mode="surface",
                strategy="gmsh",
            ),
            _environment("icoFoam", "gmshToFoam"),
            (),
        )

    assert caught.value.code == "ROUTING_UNRESOLVED"
    assert any(
        "required mesh executable is unavailable: gmsh" in item
        for item in caught.value.profile.unresolved_questions
    )

def test_explicit_disabled_thermal_stress_overrides_thermal_property_words():
    profile = route_capability(
        _task(
            "Use solidEquilibriumDisplacementFoam for an isothermal elastic "
            "beam with thermalStress disabled and alphav specified only as "
            "a required material dictionary value."
        ),
        _environment("solidEquilibriumDisplacementFoam", "blockMesh"),
        (),
    )

    assert profile.solver_executable == "solidEquilibriumDisplacementFoam"
    assert profile.energy == "disabled"


def test_explicit_shallow_water_solver_routes_to_shallow_water_physics():
    profile = route_capability(
        _task(
            "Use shallowWaterFoam for transient shallow water flow over a "
            "bed bump."
        ),
        _environment("shallowWaterFoam", "blockMesh"),
        (),
    )

    assert profile.solver_executable == "shallowWaterFoam"
    assert profile.physics_family == "shallow_water"


def test_explicit_solver_that_is_not_installed_is_unresolved():
    with pytest.raises(RoutingError) as caught:
        route_capability(
            _task(
                "Use icoFoam for transient laminar incompressible "
                "single-phase flow."
            ),
            _environment("blockMesh", "checkMesh"),
            (),
        )

    assert caught.value.code == "ROUTING_UNRESOLVED"
    assert caught.value.profile.solver_executable == "icoFoam"
    assert caught.value.profile.confidence == CapabilityConfidence.LOW


def test_unique_compatible_knowledge_candidate_routes_medium():
    profile = route_capability(
        _task(
            "Solve a transient laminar incompressible single-phase "
            "enclosure flow."
        ),
        _environment("icoFoam"),
        (
            _solver_guide(
                "icoFoam",
                entry_id="of10.solver.route-icofoam",
                title="Transient laminar enclosure solver",
            ),
        ),
    )

    assert profile.solver_executable == "icoFoam"
    assert profile.confidence == CapabilityConfidence.MEDIUM
    assert any(
        evidence.source == "knowledge"
        for evidence in profile.evidence
    )


def test_ordinary_incompressible_fluid_defaults_to_single_phase_when_no_phase_is_declared():
    profile = route_capability(
        _task(
            "Solve a transient laminar incompressible enclosure flow "
            "with kinematic pressure and viscosity."
        ),
        _environment("icoFoam"),
        (
            _solver_guide(
                "icoFoam",
                entry_id="of10.solver.route-implicit-single-phase",
                title="Transient laminar enclosure solver",
            ),
        ),
    )

    assert profile.phase_family == "single_phase"
    assert profile.confidence == CapabilityConfidence.MEDIUM


def test_missing_critical_physics_is_request_incomplete():
    with pytest.raises(RoutingError) as caught:
        route_capability(
            _task("Create an OpenFOAM case and calculate a field."),
            _environment("icoFoam"),
            (),
        )

    assert caught.value.code == "REQUEST_INCOMPLETE"
    assert caught.value.profile.confidence == CapabilityConfidence.LOW
    assert caught.value.profile.unresolved_questions


def test_model_suggestion_cannot_promote_ambiguous_route_confidence():
    guides = (
        _solver_guide(
            "icoFoam",
            entry_id="of10.solver.route-icofoam",
            title="Transient laminar incompressible flow solver",
        ),
        _solver_guide(
            "pisoFoam",
            entry_id="of10.solver.route-pisofoam",
            title="Transient laminar incompressible flow solver",
        ),
    )
    gateway = RecordingRouteGateway(
        RouteSuggestion(
            candidate="icoFoam",
            evidence=[
                {
                    "source": "public-task",
                    "fact": "The task asks for transient laminar flow.",
                }
            ],
            unresolved_questions=[],
        )
    )

    with pytest.raises(RoutingError) as caught:
        route_capability(
            _task(
                "Solve a transient laminar incompressible single-phase flow."
            ),
            _environment("icoFoam", "pisoFoam"),
            guides,
            gateway=gateway,
            budget=_route_budget(),
            trace=InMemoryModelTraceSink(),
        )

    assert len(gateway.requests) == 1
    assert gateway.requests[0].purpose == "route-openfoam-capability"
    assert caught.value.code == "ROUTING_UNRESOLVED"
    assert caught.value.profile.solver_executable == "icoFoam"
    assert caught.value.profile.confidence == CapabilityConfidence.LOW


def test_mesh_utility_metadata_does_not_hide_explicit_solver():
    package = Path(__file__).parents[1] / "src/foampilot"
    corpus = load_knowledge_corpus(package / "knowledge/openfoam10")

    profile = route_capability(
        _task(
            "Build the mesh with blockMesh, check it, then use "
            "SRFPimpleFoam for a rotating RANS flow."
        ),
        _environment("blockMesh", "checkMesh", "SRFPimpleFoam"),
        corpus,
    )

    assert profile.solver_executable == "SRFPimpleFoam"
    assert profile.confidence == CapabilityConfidence.HIGH


def test_multiregime_solid_solver_does_not_invent_a_registry_conflict():
    package = Path(__file__).parents[1] / "src/foampilot"
    corpus = load_knowledge_corpus(package / "knowledge/openfoam10")

    profile = route_capability(
        _task(
            "Use blockMesh and solidDisplacementFoam for a steady "
            "plane-stress elastic solid with thermal stress disabled."
        ),
        _environment("blockMesh", "solidDisplacementFoam"),
        corpus,
    )

    assert profile.solver_executable == "solidDisplacementFoam"
    assert profile.regime == "steady"
    assert profile.confidence == CapabilityConfidence.HIGH
