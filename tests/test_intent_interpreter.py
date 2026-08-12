from __future__ import annotations

from foampilot.assets import AssetBundle, BundleMember, compute_bundle_manifest_sha256
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.preprocessing import (
    BoundingBox,
    InputMeshFacts,
    MeshPatchFact,
    MeshZoneFact,
)
from foampilot.simulation import FactEvidence, ResolvedValue, SimulationIntent
from foampilot.simulation.intent import interpret_intent
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload


class ScriptedIntentGateway:
    primary_backend_id = "scripted"
    primary_model = "scripted-intent"
    policy_sha256 = "a" * 64

    def __init__(self, response: SimulationIntent) -> None:
        self.response = response
        self.requests = []

    def generate_structured(self, request, schema, *, budget, trace):
        del trace
        assert budget.stage == ModelStage.INTENT_INTERPRETATION
        assert schema is SimulationIntent
        self.requests.append(request)
        return ModelResult(
            value=self.response,
            logical_request_id="intent-1",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def _task(request_text: str = "Solve laminar incompressible flow.") -> TaskSpec:
    return TaskSpec.model_validate(
        canonical_task_payload(
            {
                "schema_version": 2,
                "task_id": "intent-test",
                "title": "Intent test",
                "prompt": request_text,
                "openfoam_target": {
                    "distribution": "foundation",
                    "version": "10",
                },
                "resource_budget": {
                    "max_attempts": 1,
                    "max_wall_seconds": 60,
                    "max_mpi_ranks": 1,
                    "memory_mib": 512,
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
                "protected_paths": [],
            }
        )
    )


def _mesh_facts() -> InputMeshFacts:
    return InputMeshFacts(
        bundle_manifest_sha256="b" * 64,
        inspector_id="foampilot.mesh.poly-mesh",
        inspector_version="1.0.0",
        region=None,
        declared_length_unit="m",
        source_member_sha256={"points": "c" * 64},
        points=8,
        faces=6,
        internal_faces=0,
        cells=1,
        bounding_box_m=BoundingBox(
            minimum=(0, 0, 0),
            maximum=(1, 1, 0.01),
        ),
        patches=(
            MeshPatchFact(
                name="inlet",
                patch_type="patch",
                start_face=0,
                face_count=1,
            ),
        ),
        cell_zones=(MeshZoneFact(name="porous", element_count=1),),
        face_zones=(),
        point_zones=(),
        dimensionality_observations=("empty patch indicates two_d",),
        topology_observations=("indices valid",),
        warnings=(),
    )


def _bundle() -> AssetBundle:
    members = (
        BundleMember(
            relative_path="points",
            logical_name="points",
            sha256="c" * 64,
            bytes=1,
        ),
    )
    values = {
        "adapter_id": "foampilot.asset.openfoam-poly-mesh",
        "kind": "openfoam_poly_mesh",
        "source_path": "mesh/native",
        "install_path": "constant/polyMesh",
        "region": None,
        "members": members,
    }
    return AssetBundle(
        **values,
        manifest_sha256=compute_bundle_manifest_sha256(**values),
    )


def _response(*facts: ResolvedValue) -> SimulationIntent:
    return SimulationIntent(
        facts=facts,
        constraints=("Foundation OpenFOAM 10",),
        requested_observables=("velocity",),
        acceptance_intent=("normal completion",),
        uncertainties=(),
    )


def _window():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.INTENT_INTERPRETATION,
        stage_deadline_seconds=30,
        max_transport_attempts=1,
    )


def test_interpreter_request_contains_facts_not_raw_mesh() -> None:
    gateway = ScriptedIntentGateway(
        _response(
            ResolvedValue(
                field_path="physics.regime",
                value="laminar",
                source="model_inference",
                impact="high",
                evidence=(
                    FactEvidence(kind="model_reason", detail="interpreted"),
                ),
                confirmed=False,
            )
        )
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(_bundle(),),
        mesh_facts=(_mesh_facts(),),
        capability_kinds=("physics:incompressible", "solver:icoFoam"),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    request = gateway.requests[0]
    assert "InputMeshFacts" in request.user_prompt
    assert "FoamFile" not in request.user_prompt
    assert "FoamFile" not in request.user_prompt
    assert "(0 0 0)" not in request.user_prompt
    assert all(fact.source != "user_confirmation" for fact in intent.facts)
    assert request.purpose == "interpret-simulation-intent"


def test_false_user_text_evidence_is_downgraded() -> None:
    gateway = ScriptedIntentGateway(
        _response(
            ResolvedValue(
                field_path="physics.compressibility",
                value="incompressible",
                source="user_text",
                impact="high",
                evidence=(
                    FactEvidence(
                        kind="user_quote",
                        detail="not present verbatim",
                    ),
                ),
                confirmed=True,
            )
        )
    )

    intent = interpret_intent(
        _task("simulate a flow"),
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("physics:incompressible",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    fact = intent.fact("physics.compressibility")
    assert fact.source == "model_inference"
    assert fact.confirmed is False


def test_interpreter_rejects_self_asserted_authority_and_forbidden_decisions() -> None:
    gateway = ScriptedIntentGateway(
        _response(
            ResolvedValue(
                field_path="solver.family",
                value="icoFoam",
                source="public_asset_fact",
                impact="high",
                evidence=(
                    FactEvidence(kind="asset_fact", detail="unknown-fact-id"),
                ),
                confirmed=True,
            ),
            ResolvedValue(
                field_path="numerics.div_scheme",
                value="upwind",
                source="model_inference",
                impact="medium",
                evidence=(
                    FactEvidence(kind="model_reason", detail="selected scheme"),
                ),
                confirmed=False,
            ),
        )
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(_bundle(),),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert all(fact.field_path != "solver.family" for fact in intent.facts)
    assert all(fact.field_path != "numerics.div_scheme" for fact in intent.facts)
    assert intent.audit_warnings


def test_verified_explicit_task_fact_remains_authoritative() -> None:
    task_payload = _task().model_dump(mode="json")
    task_payload["explicit_facts"].append(
        ResolvedValue(
            field_path="materials.fluid.nu",
            value={"value": 1e-6, "unit": "m2/s"},
            source="user_text",
            impact="high",
            evidence=(
                FactEvidence(kind="user_quote", detail="nu = 1e-6 m2/s"),
            ),
            confirmed=True,
        ).model_dump(mode="json")
    )
    task = TaskSpec.model_validate(task_payload)
    gateway = ScriptedIntentGateway(_response())

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.fact("materials.fluid.nu").source == "user_text"
    assert intent.fact("materials.fluid.nu").confirmed is True
