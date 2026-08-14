from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from foampilot.assets import AssetBundle, BundleMember, compute_bundle_manifest_sha256
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.preprocessing import (
    BoundingBox,
    ExecutedMeshFacts,
    InputMeshFacts,
    MeshCheckFact,
    MeshPatchFact,
    MeshQualityReport,
    MeshZoneFact,
)
from foampilot.acceptance import AcceptanceRequest, AcceptanceScope
from foampilot.observations import ObservationRequest, ObservationScope, TimeSelection
from foampilot.simulation import FactEvidence, ResolvedValue, SimulationIntent
from foampilot.simulation.intent import (
    IntentUncertainty,
    interpret_intent,
    normalize_simulation_intent_input,
)
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload, resolved_fact


class ScriptedIntentGateway:
    primary_backend_id = "scripted"
    primary_model = "scripted-intent"
    policy_sha256 = "a" * 64

    def __init__(self, response: SimulationIntent) -> None:
        self.response = response
        self.requests = []

    def generate_structured(
        self,
        request,
        schema,
        *,
        budget,
        trace,
        output_normalizer=None,
    ):
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


def test_intent_input_normalizer_repairs_only_unambiguous_scope_shapes() -> None:
    payload = _acceptance_response(
        detail="continuity <= 1e-5",
        limit=1.0e-5,
    ).model_dump(mode="json")
    payload["observation_requests"][0]["scope"] = {
        "kind": "region",
        "names": ["porousBlockage"],
        "region": None,
    }
    intent, records = normalize_simulation_intent_input(
        json.dumps(payload)
    )

    assert intent.observation_requests[0].scope.region == "porousBlockage"
    assert {item.code for item in records} == {"INTENT_REGION_SCOPE_BOUND"}


def test_intent_input_normalizer_rejects_named_global_scope() -> None:
    payload = _acceptance_response(
        detail="continuity <= 1e-5",
        limit=1.0e-5,
    ).model_dump(mode="json")
    payload["acceptance_requests"][0]["observation"]["scope"] = {
        "kind": "global",
        "names": ["outlet"],
        "region": None,
    }

    with pytest.raises(ValidationError, match="global scope requires 0 names"):
        normalize_simulation_intent_input(json.dumps(payload))


def test_intent_input_normalizer_binds_omitted_names_from_region() -> None:
    payload = _acceptance_response(
        detail="continuity <= 1e-5",
        limit=1.0e-5,
    ).model_dump(mode="json")
    payload["observation_requests"][0]["scope"] = {
        "kind": "region",
        "region": "porousBlockage",
    }

    intent, records = normalize_simulation_intent_input(json.dumps(payload))

    scope = intent.observation_requests[0].scope
    assert scope.names == ("porousBlockage",)
    assert scope.region == "porousBlockage"
    assert [item.code for item in records] == ["INTENT_REGION_SCOPE_BOUND"]
    assert records[0].location == "observation_requests.0.scope.names"


def test_intent_input_normalizer_rejects_ambiguous_region_scope() -> None:
    payload = _acceptance_response(
        detail="continuity <= 1e-5",
        limit=1.0e-5,
    ).model_dump(mode="json")
    payload["observation_requests"][0]["scope"] = {
        "kind": "region",
        "names": ["porousBlockage"],
        "region": "otherRegion",
    }

    with pytest.raises(ValidationError, match="matching explicit region"):
        normalize_simulation_intent_input(json.dumps(payload))


def test_intent_input_normalizer_binds_registered_observation_aliases() -> None:
    payload = _acceptance_response(
        detail="report inlet flow",
        limit=1.0,
        quantity="continuity_error",
    ).model_dump(mode="json")
    observations = (
        payload["observation_requests"][0],
        payload["acceptance_requests"][0]["observation"],
    )
    for observation in observations:
        observation.update(
            {
                "kind": "flow_rate",
                "quantity": "Q",
                "dimension": "L^3/T",
                "scope": {
                    "kind": "patch",
                    "names": ["inlet"],
                    "region": None,
                },
            }
        )

    intent, records = normalize_simulation_intent_input(json.dumps(payload))

    normalized = (
        intent.observation_requests[0],
        intent.acceptance_requests[0].observation,
    )
    assert {
        (item.quantity, item.dimension)
        for item in normalized
    } == {("volumetric_flow_rate", "0 3 -1 0 0 0 0")}
    assert [record.code for record in records] == [
        "INTENT_QUANTITY_ALIAS_BOUND",
        "INTENT_DIMENSION_ALIAS_BOUND",
        "INTENT_QUANTITY_ALIAS_BOUND",
        "INTENT_DIMENSION_ALIAS_BOUND",
    ]


def test_intent_input_normalizer_does_not_guess_unregistered_aliases() -> None:
    payload = _acceptance_response(
        detail="continuity <= 1e-5",
        limit=1.0e-5,
        quantity="continuity_error",
    ).model_dump(mode="json")
    payload["observation_requests"][0]["quantity"] = "Continuity Error"

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        normalize_simulation_intent_input(json.dumps(payload))


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


def _executed_mesh_facts(*, mesh_ok: bool = True) -> ExecutedMeshFacts:
    return ExecutedMeshFacts(
        mesh_check=MeshCheckFact(
            executed=True,
            executable_identity="/opt/openfoam10/platforms/checkMesh",
            return_code=0 if mesh_ok else 1,
            timed_out=False,
            mesh_ok=mesh_ok,
            evidence_paths=("logs/checkMesh.log",),
        ),
        metrics=MeshQualityReport(
            strategy="provided",
            commands_completed=("checkMesh",),
            mesh_created=True,
            check_mesh_passed=mesh_ok,
            patches=(),
            failed_requirements=(),
            warnings=(),
            evidence_files=("logs/checkMesh.log",),
        ),
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


def test_intent_stage_cannot_emit_confirmation_candidates() -> None:
    with pytest.raises(ValidationError):
        IntentUncertainty.model_validate(
            {
                "question_id": "q-nu",
                "field_path": "materials.fluid.nu",
                "impact": "high",
                "kind": "confirmable",
                "prompt_zh": "确认黏度？",
                "reason_zh": "模型候选。",
                "candidates": [],
            }
        )

    uncertainty = IntentUncertainty(
        question_id="q-nu",
        field_path="materials.fluid.nu",
        impact="high",
        kind="design_required",
        prompt_zh="需要设计阶段给出黏度候选。",
        reason_zh="这是工程设计量。",
    )
    assert uncertainty.kind == "design_required"


def test_non_authoring_information_limits_become_audit_warnings() -> None:
    gateway = ScriptedIntentGateway(
        SimulationIntent(
            uncertainties=(
                IntentUncertainty(
                    question_id="info-acceptance-threshold",
                    field_path="acceptance.mesh_quality_thresholds",
                    impact="high",
                    kind="information_required",
                    prompt_zh="提供阈值。",
                    reason_zh="模型未找到阈值。",
                ),
                IntentUncertainty(
                    question_id="info-sampling-scope",
                    field_path="observations.porous_sampling_scopes",
                    impact="medium",
                    kind="information_required",
                    prompt_zh="提供采样范围。",
                    reason_zh="无法唯一构造采样面。",
                ),
            )
        )
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.uncertainties == ()
    assert set(intent.audit_warnings) == {
        "INTENT_REPORTING_LIMITATION:acceptance.mesh_quality_thresholds",
        "INTENT_REPORTING_LIMITATION:observations.porous_sampling_scopes",
    }


def test_successful_target_mesh_probe_resolves_compatibility_uncertainty() -> None:
    uncertainty = IntentUncertainty(
        question_id="confirm_mesh_compatibility",
        field_path="mesh.foundation_openfoam_10_compatibility",
        impact="high",
        kind="information_required",
        prompt_zh="确认网格与 Foundation OpenFOAM 10 兼容。",
        reason_zh="需要受信任的资产检查。",
    )
    gateway = ScriptedIntentGateway(
        SimulationIntent(uncertainties=(uncertainty,))
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        executed_mesh_facts=(_executed_mesh_facts(),),
        capability_kinds=(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.uncertainties == ()
    assert intent.audit_warnings == (
        "INTENT_UNCERTAINTY_RESOLVED_BY_MESH_PROBE:"
        "mesh.foundation_openfoam_10_compatibility",
    )


def test_mesh_probe_resolves_compact_compatibility_path_alias() -> None:
    uncertainty = IntentUncertainty(
        question_id="mesh_openfoam10_compatibility_result",
        field_path="mesh.openfoam10_compatibility",
        impact="high",
        kind="information_required",
        prompt_zh="确认 Foundation OpenFOAM 10 网格兼容性。",
        reason_zh="需要受信任的网格探针。",
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        executed_mesh_facts=(_executed_mesh_facts(),),
        capability_kinds=(),
        gateway=ScriptedIntentGateway(
            SimulationIntent(uncertainties=(uncertainty,))
        ),
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.uncertainties == ()
    assert intent.audit_warnings == (
        "INTENT_UNCERTAINTY_RESOLVED_BY_MESH_PROBE:"
        "mesh.openfoam10_compatibility",
    )


def test_unique_cell_zone_misclassified_as_region_is_reconciled() -> None:
    request = ObservationRequest(
        observation_id="porous-average-velocity",
        kind="region_average",
        quantity="velocity_magnitude",
        dimension="L/T",
        scope=ObservationScope(
            kind="region",
            names=("porous",),
            region="porous",
        ),
        time_selection=TimeSelection(kind="history"),
        provenance=(
            FactEvidence(
                kind="user_text",
                detail="Report porous cell-zone average velocity.",
            ),
        ),
    )
    gateway = ScriptedIntentGateway(
        SimulationIntent(observation_requests=(request,))
    )

    intent = interpret_intent(
        _task("Report porous cell-zone average velocity."),
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        capability_kinds=(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    scope = intent.observation_requests[0].scope
    assert scope.kind == "cell_zone"
    assert scope.names == ("porous",)
    assert scope.region is None
    assert (
        "INTENT_REGION_SCOPE_RECONCILED_TO_CELL_ZONE:"
        "porous-average-velocity"
    ) in intent.audit_warnings


def test_true_mesh_region_scope_is_not_reclassified_as_cell_zone() -> None:
    mesh = _mesh_facts().model_copy(update={"region": "fluid"})
    request = ObservationRequest(
        observation_id="fluid-average-velocity",
        kind="region_average",
        quantity="velocity_magnitude",
        dimension="L/T",
        scope=ObservationScope(
            kind="region",
            names=("fluid",),
            region="fluid",
        ),
        time_selection=TimeSelection(kind="latest"),
        provenance=(FactEvidence(kind="model_reason", detail="requested"),),
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(mesh,),
        capability_kinds=(),
        gateway=ScriptedIntentGateway(
            SimulationIntent(observation_requests=(request,))
        ),
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.observation_requests[0].scope == request.scope
    assert not any(
        warning.startswith("INTENT_REGION_SCOPE_RECONCILED_TO_CELL_ZONE:")
        for warning in intent.audit_warnings
    )


def test_ambiguous_cell_zone_name_across_mesh_regions_is_not_reclassified() -> None:
    request = ObservationRequest(
        observation_id="porous-average-velocity",
        kind="region_average",
        quantity="velocity_magnitude",
        dimension="L/T",
        scope=ObservationScope(
            kind="region",
            names=("porous",),
            region="porous",
        ),
        time_selection=TimeSelection(kind="latest"),
        provenance=(FactEvidence(kind="model_reason", detail="requested"),),
    )
    meshes = tuple(
        _mesh_facts().model_copy(update={"region": region})
        for region in ("fluid-a", "fluid-b")
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=meshes,
        capability_kinds=(),
        gateway=ScriptedIntentGateway(
            SimulationIntent(observation_requests=(request,))
        ),
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.observation_requests[0].scope == request.scope
    assert not any(
        warning.startswith("INTENT_REGION_SCOPE_RECONCILED_TO_CELL_ZONE:")
        for warning in intent.audit_warnings
    )


def test_acceptance_observation_region_scope_is_reconciled_to_cell_zone() -> None:
    statement = "porous average velocity must be finite"
    observation = ObservationRequest(
        observation_id="porous-average-velocity",
        kind="region_average",
        quantity="velocity_magnitude",
        dimension="L/T",
        scope=ObservationScope(
            kind="region",
            names=("porous",),
            region="porous",
        ),
        time_selection=TimeSelection(kind="latest"),
        provenance=(FactEvidence(kind="user_quote", detail=statement),),
    )
    acceptance = AcceptanceRequest(
        condition_id="porous-average-velocity-finite",
        observation=observation,
        operator="finite",
        unit="L/T",
        scope=AcceptanceScope(time="latest"),
        source="user_text",
        confirmed=True,
        provenance=(FactEvidence(kind="user_quote", detail=statement),),
    )
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        capability_kinds=(),
        gateway=ScriptedIntentGateway(
            SimulationIntent(acceptance_requests=(acceptance,))
        ),
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    scope = intent.acceptance_requests[0].observation.scope
    assert scope.kind == "cell_zone"
    assert scope.names == ("porous",)
    assert scope.region is None
    assert (
        "INTENT_ACCEPTANCE_REGION_SCOPE_RECONCILED_TO_CELL_ZONE:"
        "porous-average-velocity-finite"
    ) in intent.audit_warnings


def test_redundant_patch_pair_flow_balance_uses_existing_patch_flows() -> None:
    provenance = (FactEvidence(kind="model_reason", detail="requested"),)
    patch_flows = tuple(
        ObservationRequest(
            observation_id=f"{name}-flow",
            kind="flow_rate",
            quantity="volumetric_flow_rate",
            dimension="L^3/T",
            scope=ObservationScope(kind="patch", names=(name,)),
            time_selection=TimeSelection(kind="history"),
            provenance=provenance,
        )
        for name in ("inlet", "outlet")
    )
    balance = ObservationRequest(
        observation_id="inlet-outlet-flow-balance",
        kind="flow_rate",
        quantity="volumetric_flow_balance",
        dimension="L^3/T",
        scope=ObservationScope(
            kind="patch_pair",
            names=("inlet", "outlet"),
        ),
        time_selection=TimeSelection(kind="history"),
        provenance=provenance,
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        capability_kinds=(),
        gateway=ScriptedIntentGateway(
            SimulationIntent(observation_requests=(*patch_flows, balance))
        ),
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert tuple(
        request.observation_id for request in intent.observation_requests
    ) == ("inlet-flow", "outlet-flow")
    assert (
        "INTENT_REDUNDANT_FLOW_BALANCE_REPRESENTED_BY_PATCH_FLOWS:"
        "inlet-outlet-flow-balance"
    ) in intent.audit_warnings


def test_nonredundant_patch_pair_flow_rate_is_left_for_planner_to_reject() -> None:
    request = ObservationRequest(
        observation_id="inlet-outlet-flow-balance",
        kind="flow_rate",
        quantity="volumetric_flow_balance",
        dimension="L^3/T",
        scope=ObservationScope(
            kind="patch_pair",
            names=("inlet", "outlet"),
        ),
        time_selection=TimeSelection(kind="history"),
        provenance=(FactEvidence(kind="model_reason", detail="requested"),),
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        capability_kinds=(),
        gateway=ScriptedIntentGateway(
            SimulationIntent(observation_requests=(request,))
        ),
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.observation_requests == (request,)


def test_mesh_compatibility_uncertainty_remains_without_successful_probe() -> None:
    uncertainty = IntentUncertainty(
        question_id="confirm_mesh_compatibility",
        field_path="mesh.foundation_openfoam_10_compatibility",
        impact="high",
        kind="information_required",
        prompt_zh="确认网格与 Foundation OpenFOAM 10 兼容。",
        reason_zh="需要受信任的资产检查。",
    )
    gateway = ScriptedIntentGateway(
        SimulationIntent(uncertainties=(uncertainty,))
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(_mesh_facts(),),
        executed_mesh_facts=(_executed_mesh_facts(mesh_ok=False),),
        capability_kinds=(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.uncertainties == (uncertainty,)


def test_confirmed_geometry_roles_are_projected_to_canonical_facts() -> None:
    payload = canonical_task_payload(
        {
            "schema_version": 2,
            "task_id": "intent-geometry-roles",
            "title": "Geometry roles",
            "prompt": "A channel contains a porous cell zone.",
            "openfoam_target": {"distribution": "foundation", "version": "10"},
            "resource_budget": {
                "max_attempts": 1,
                "max_wall_seconds": 60,
                "max_mpi_ranks": 1,
                "memory_mib": 512,
            },
            "required_outputs": ["velocity"],
            "acceptance_requirements": ["normal completion"],
            "public_checks": [],
            "public_assets": [],
            "geometry": {
                "mode": "parametric",
                "dimensionality": "two_d",
                "description": "channel",
                "length_unit": "m",
                "parameters": {"length": {"value": 1.0, "unit": "m"}},
                "patch_roles": [{"name": "inlet", "role": "inlet"}],
                "region_roles": [
                    {"name": "porousBlockage", "role": "porous"}
                ],
            },
            "mesh": {"strategy": "blockMesh"},
            "protected_paths": [],
        }
    )
    task = TaskSpec.model_validate(payload)
    gateway = ScriptedIntentGateway(SimulationIntent())

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:pisofoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.fact("geometry.length_unit").value == "m"
    assert intent.fact("regions.porousBlockage.role").value == "porous"
    assert intent.fact("boundaries.inlet.role").value == "inlet"


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
    assert "(0 0 0)" not in request.user_prompt
    assert all(fact.source != "user_confirmation" for fact in intent.facts)
    assert request.purpose == "interpret-simulation-intent"
    assert "Use scope kind cell_zone for an OpenFOAM cellZone" in (
        request.system_prompt
    )
    assert "Reserve scope kind region for a named OpenFOAM mesh region" in (
        request.system_prompt
    )
    assert "Flow-rate observations support one patch per request" in (
        request.system_prompt
    )
    assert "lower_snake_case" in request.system_prompt
    assert "exact canonical quantity and dimension" in request.system_prompt
    request_context = json.loads(request.user_prompt)
    contracts = request_context["AvailableObservationContracts"]
    assert {
        (item["kind"], item["quantity"], item["dimension"])
        for item in contracts
    } >= {
        ("flow_rate", "volumetric_flow_rate", "0 3 -1 0 0 0 0"),
        ("region_average", "velocity", "0 1 -1 0 0 0 0"),
        ("residual", "solver_residual", "1"),
    }
    assert all("quantity_aliases" not in item for item in contracts)


def test_confirmed_task_solver_is_projected_to_canonical_solver_family() -> None:
    task = _task("Use pisoFoam for this transient flow.")
    payload = task.model_dump(mode="json")
    payload["explicit_facts"].append(
        resolved_fact("physics.solver", "pisoFoam")
    )
    gateway = ScriptedIntentGateway(_response())

    intent = interpret_intent(
        TaskSpec.model_validate(payload),
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:pisofoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    solver = intent.fact("solver.family")
    assert solver.value == "pisoFoam"
    assert solver.source == "user_text"
    assert solver.confirmed is True
    assert all(item.field_path != "physics.solver" for item in intent.facts)


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


def test_task_required_outputs_override_model_restatement() -> None:
    gateway = ScriptedIntentGateway(
        _response(
            ResolvedValue(
                field_path="execution.required_outputs",
                value=["pressure"],
                source="user_text",
                impact="high",
                evidence=(
                    FactEvidence(
                        kind="required_outputs",
                        detail="the model restated a different output",
                    ),
                ),
                confirmed=True,
            )
        )
    )

    intent = interpret_intent(
        _task(),
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    fact = intent.fact("execution.required_outputs")
    assert fact.value == ["velocity"]
    assert fact.source == "deterministic_rule"
    assert fact.confirmed is True
    assert fact.evidence[0].kind == "task_contract"
    assert "INTENT_USER_TEXT_UNVERIFIED:execution.required_outputs" not in (
        intent.audit_warnings
    )


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


def _acceptance_response(
    *,
    detail: str,
    limit: float,
    source: str = "user_text",
    confirmed: bool = True,
    observation_kind: str = "continuity",
    quantity: str = "continuity",
    operator: str = "less_equal",
    unit: str = "1",
    acceptance_scope: AcceptanceScope | None = None,
) -> SimulationIntent:
    observation = ObservationRequest(
        observation_id="continuity",
        kind=observation_kind,
        quantity=quantity,
        dimension=unit,
        scope=ObservationScope(kind="global"),
        time_selection=TimeSelection(kind="latest"),
        provenance=(FactEvidence(kind="user_quote", detail=detail),),
    )
    return SimulationIntent(
        observation_requests=(observation,),
        acceptance_requests=(
            AcceptanceRequest(
                condition_id="continuity-limit",
                observation=observation,
                operator=operator,
                limit=limit,
                unit=unit,
                scope=acceptance_scope or AcceptanceScope(time="latest"),
                source=source,
                confirmed=confirmed,
                provenance=(FactEvidence(kind="user_quote", detail=detail),),
            ),
        ),
    )


def test_model_cannot_forge_a_user_confirmed_acceptance_threshold() -> None:
    gateway = ScriptedIntentGateway(
        _acceptance_response(detail="continuity <= 1e-99", limit=1.0e-99)
    )

    intent = interpret_intent(
        _task("solve this flow"),
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    request = intent.acceptance_requests[0]
    assert request.source == "model_inference"
    assert request.confirmed is False
    assert "ACCEPTANCE_AUTHORITY_DOWNGRADED:continuity-limit" in intent.audit_warnings


def test_model_cannot_change_numeric_threshold_behind_a_real_user_quote() -> None:
    statement = "absolute cumulative continuity <= 1e-5"
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})
    gateway = ScriptedIntentGateway(
        _acceptance_response(detail=statement, limit=1.0e-99)
    )
    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )
    assert intent.acceptance_requests[0].source == "model_inference"
    assert intent.acceptance_requests[0].confirmed is False


def test_model_cannot_reverse_operator_behind_a_real_user_quote() -> None:
    statement = "absolute cumulative continuity >= 1e-5"
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})
    gateway = ScriptedIntentGateway(
        _acceptance_response(
            detail=statement,
            limit=1.0e-5,
            operator="less_equal",
        )
    )

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.acceptance_requests[0].source == "model_inference"
    assert intent.acceptance_requests[0].confirmed is False


def test_model_cannot_substitute_observable_behind_a_real_user_quote() -> None:
    statement = "absolute cumulative continuity <= 1e-5"
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})
    gateway = ScriptedIntentGateway(
        _acceptance_response(
            detail=statement,
            limit=1.0e-5,
            observation_kind="flow_rate",
            quantity="volumetric_flow_rate",
        )
    )

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.acceptance_requests[0].source == "model_inference"
    assert intent.acceptance_requests[0].confirmed is False


def test_unit_exponent_cannot_be_reused_as_a_forged_threshold() -> None:
    statement = "pressure difference <= 10 m2/s2"
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})
    gateway = ScriptedIntentGateway(
        _acceptance_response(
            detail=statement,
            limit=2.0,
            observation_kind="pressure_difference",
            quantity="pressure_difference",
            unit="m2/s2",
        )
    )

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.acceptance_requests[0].source == "model_inference"
    assert intent.acceptance_requests[0].confirmed is False


def test_unrelated_time_number_cannot_be_reused_as_threshold() -> None:
    statement = "continuity <= 1e-5 at final time 10"
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})
    gateway = ScriptedIntentGateway(
        _acceptance_response(detail=statement, limit=10.0)
    )

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.acceptance_requests[0].source == "model_inference"
    assert intent.acceptance_requests[0].confirmed is False


def test_model_cannot_narrow_an_all_time_condition_to_latest() -> None:
    statement = "continuity <= 1e-5 throughout the simulation"
    task = _task(statement).model_copy(update={"acceptance_intent": [statement]})
    gateway = ScriptedIntentGateway(
        _acceptance_response(detail=statement, limit=1.0e-5)
    )

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    assert intent.acceptance_requests[0].source == "model_inference"
    assert intent.acceptance_requests[0].confirmed is False


def test_exact_task_acceptance_statement_retains_user_authority() -> None:
    statement = "absolute cumulative continuity <= 1e-5"
    task = _task(f"Solve the flow; {statement}.").model_copy(
        update={"acceptance_intent": [statement]}
    )
    gateway = ScriptedIntentGateway(
        _acceptance_response(detail=statement, limit=1.0e-5)
    )

    intent = interpret_intent(
        task,
        asset_facts=(),
        mesh_facts=(),
        capability_kinds=("solver:icoFoam",),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )

    request = intent.acceptance_requests[0]
    assert request.source == "user_text"
    assert request.confirmed is True


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
