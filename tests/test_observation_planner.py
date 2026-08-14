from __future__ import annotations

import pytest

from foampilot.acceptance import AcceptanceCompiler, AcceptanceRequest, AcceptanceScope
from foampilot.observations import (
    ObservationPlanner,
    ObservationPlanningError,
    ObservationRequest,
    ObservationScope,
    TimeSelection,
    first_party_observation_registry,
)
from foampilot.preprocessing import BoundingBox, InputMeshFacts, MeshPatchFact, MeshZoneFact
from foampilot.simulation import FactEvidence, SimulationIntent
from types import SimpleNamespace


def _request(
    kind: str,
    *,
    names: tuple[str, ...] = (),
    scope: str = "global",
    time: str = "latest",
) -> ObservationRequest:
    quantity = {
        "continuity": "continuity_error",
        "flow_rate": "volumetric_flow_rate",
        "residual": "solver_residual",
        "pressure_difference": "pressure_difference",
        "region_average": "velocity",
    }.get(kind, kind)
    dimension = {
        "continuity": "1",
        "flow_rate": "0 3 -1 0 0 0 0",
        "force": "1 1 -2 0 0 0 0",
        "heat_flux": "1 0 -3 0 0 0 0",
        "pressure_difference": "0 2 -2 0 0 0 0",
        "region_average": "0 1 -1 0 0 0 0",
        "residual": "1",
    }.get(kind, "1")
    return ObservationRequest(
        observation_id=(kind + ("-" + "-".join(names) if names else "")),
        kind=kind,
        quantity=quantity,
        dimension=dimension,
        scope=ObservationScope(kind=scope, names=names),
        time_selection=TimeSelection(kind=time),
        provenance=(FactEvidence(kind="user_quote", detail=kind),),
    )


def _mesh() -> InputMeshFacts:
    return InputMeshFacts(
        bundle_manifest_sha256="a" * 64,
        inspector_id="test",
        inspector_version="1",
        region=None,
        declared_length_unit="m",
        source_member_sha256={"boundary": "b" * 64},
        points=8,
        faces=12,
        internal_faces=4,
        cells=4,
        bounding_box_m=BoundingBox(minimum=(0, 0, 0), maximum=(1, 1, 0.1)),
        patches=(
            MeshPatchFact(name="inlet", patch_type="patch", start_face=4, face_count=2),
            MeshPatchFact(name="outlet", patch_type="patch", start_face=6, face_count=2),
        ),
        cell_zones=(MeshZoneFact(name="porous", element_count=2),),
        face_zones=(),
        point_zones=(),
        dimensionality_observations=("two_d",),
        topology_observations=(),
        warnings=(),
    )


def _region_mesh(region: str) -> InputMeshFacts:
    return _mesh().model_copy(update={"region": region})


def _compile(*requests: ObservationRequest):
    return ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=requests),
        design=None,
        mesh_facts=(_mesh(),),
        registry=first_party_observation_registry(),
    )


def test_continuity_and_residuals_reuse_run_facts() -> None:
    plan = _compile(_request("continuity"), _request("residual"))

    assert [item.evidence_strategy.kind for item in plan.items] == [
        "continuity" and "run_facts",
        "run_facts",
    ]


def test_requested_flow_history_requires_runtime_collection() -> None:
    plan = _compile(
        _request("flow_rate", names=("inlet",), scope="patch", time="history")
    )

    assert plan.items[0].evidence_strategy.kind == "runtime_configuration"
    assert plan.items[0].evidence_strategy.collector_id


def test_final_flow_uses_postprocess_not_runtime_collection() -> None:
    plan = _compile(
        _request("flow_rate", names=("outlet",), scope="patch", time="final")
    )

    assert plan.items[0].evidence_strategy.kind == "postprocess_command"


@pytest.mark.parametrize("kind", ["force", "heat_flux"])
def test_unimplemented_collectors_are_truthfully_unavailable(kind: str) -> None:
    plan = _compile(
        _request(kind, names=("outlet",), scope="patch", time="history")
    )

    strategy = plan.items[0].evidence_strategy
    assert strategy.kind == "unavailable"
    assert strategy.reason == (
        f"Foundation OpenFOAM 10 collector for {kind} is not implemented"
    )


def test_unknown_quantity_dimension_contract_is_unavailable_before_authoring() -> None:
    request = _request(
        "region_average",
        names=("porous",),
        scope="cell_zone",
    ).model_copy(
        update={"quantity": "arbitrary_field", "dimension": "9 9 9 9 9 9 9"}
    )

    strategy = _compile(request).items[0].evidence_strategy

    assert strategy.kind == "unavailable"
    assert "quantity/dimension" in strategy.reason


@pytest.mark.parametrize("kind", ["residual", "continuity"])
def test_run_fact_observations_reject_unknown_request_contracts(kind: str) -> None:
    request = _request(kind).model_copy(
        update={"quantity": "arbitrary_metric", "dimension": "9 9 9 9 9 9 9"}
    )

    strategy = _compile(request).items[0].evidence_strategy

    assert strategy.kind == "unavailable"
    assert "quantity/dimension" in strategy.reason


def test_planner_requires_canonical_contract_after_intent_normalization() -> None:
    request = _request("flow_rate", names=("inlet",), scope="patch").model_copy(
        update={"dimension": "L^3/T"}
    )

    strategy = _compile(request).items[0].evidence_strategy

    assert strategy.kind == "unavailable"
    assert "quantity/dimension" in strategy.reason


@pytest.mark.parametrize(
    ("kind", "quantity", "dimension", "canonical_quantity", "canonical_dimension"),
    [
        (
            "flow_rate",
            "Q",
            "L^3/T",
            "volumetric_flow_rate",
            "0 3 -1 0 0 0 0",
        ),
        (
            "pressure_difference",
            "kinematic_pressure",
            "L^2/T^2",
            "pressure_difference",
            "0 2 -2 0 0 0 0",
        ),
        ("region_average", "U", "L/T", "velocity", "0 1 -1 0 0 0 0"),
        (
            "region_average",
            "p",
            "L^2/T^2",
            "kinematic_pressure",
            "0 2 -2 0 0 0 0",
        ),
        ("residual", "solver_residual", "dimensionless", "solver_residual", "1"),
        ("continuity", "continuity_error", "dimensionless", "continuity_error", "1"),
    ],
)
def test_first_party_request_contracts_resolve_only_registered_exact_aliases(
    kind: str,
    quantity: str,
    dimension: str,
    canonical_quantity: str,
    canonical_dimension: str,
) -> None:
    descriptor = first_party_observation_registry().resolve(kind)

    contract = descriptor.resolve_request_contract(quantity, dimension)

    assert contract is not None
    assert contract.quantity == canonical_quantity
    assert contract.dimension == canonical_dimension


def test_first_party_request_contracts_do_not_guess_unknown_aliases() -> None:
    descriptor = first_party_observation_registry().resolve("region_average")

    assert descriptor.resolve_request_contract("Velocity Magnitude", "m/s") is None


def test_first_party_request_contract_projection_is_unique_and_canonical() -> None:
    contracts = first_party_observation_registry().request_contracts()
    keys = [
        (kind, contract.quantity, contract.dimension)
        for kind, contract in contracts
    ]

    assert len(keys) == len(set(keys))
    assert (
        "flow_rate",
        "volumetric_flow_rate",
        "0 3 -1 0 0 0 0",
    ) in keys
    assert ("residual", "solver_residual", "1") in keys
    assert ("force", "force", "1 1 -2 0 0 0 0") in keys
    assert ("heat_flux", "heat_flux", "1 0 -3 0 0 0 0") in keys


def test_named_region_is_validated_against_authoritative_mesh_facts() -> None:
    request = _request("region_average").model_copy(
        update={
            "quantity": "temperature",
            "dimension": "0 0 0 1 0 0 0",
            "scope": ObservationScope(
                kind="region",
                names=("solid",),
                region="solid",
            ),
        }
    )
    plan = ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=(request,)),
        design=None,
        mesh_facts=(_region_mesh("fluid"), _region_mesh("solid")),
        registry=first_party_observation_registry(),
    )
    assert plan.items[0].scope.region == "solid"

    missing = request.model_copy(
        update={
            "scope": ObservationScope(
                kind="region",
                names=("missing",),
                region="missing",
            )
        }
    )
    with pytest.raises(ObservationPlanningError, match="MESH_REGION_UNKNOWN"):
        ObservationPlanner().compile(
            intent=SimulationIntent(observation_requests=(missing,)),
            design=None,
            mesh_facts=(_region_mesh("fluid"), _region_mesh("solid")),
            registry=first_party_observation_registry(),
        )


def test_patch_and_zone_are_validated_within_bound_region() -> None:
    fluid = _region_mesh("fluid")
    solid = _region_mesh("solid").model_copy(
        update={
            "patches": (),
            "cell_zones": (MeshZoneFact(name="heater", element_count=2),),
        }
    )
    request = _request(
        "flow_rate",
        names=("inlet",),
        scope="patch",
    ).model_copy(
        update={
            "scope": ObservationScope(
                kind="patch",
                names=("inlet",),
                region="fluid",
            )
        }
    )
    plan = ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=(request,)),
        design=None,
        mesh_facts=(fluid, solid),
        registry=first_party_observation_registry(),
    )
    assert plan.items[0].scope.region == "fluid"

    wrong = request.model_copy(
        update={
            "scope": ObservationScope(
                kind="patch",
                names=("inlet",),
                region="solid",
            )
        }
    )
    with pytest.raises(ObservationPlanningError, match="MESH_SCOPE_UNKNOWN"):
        ObservationPlanner().compile(
            intent=SimulationIntent(observation_requests=(wrong,)),
            design=None,
            mesh_facts=(fluid, solid),
            registry=first_party_observation_registry(),
        )


def test_multiregion_scope_without_region_binding_is_rejected() -> None:
    request = _request(
        "flow_rate",
        names=("inlet",),
        scope="patch",
    )
    with pytest.raises(ObservationPlanningError, match="MESH_REGION_REQUIRED"):
        ObservationPlanner().compile(
            intent=SimulationIntent(observation_requests=(request,)),
            design=None,
            mesh_facts=(_region_mesh("fluid"), _region_mesh("solid")),
            registry=first_party_observation_registry(),
        )


def test_quantity_contract_is_compatible_with_frozen_solver() -> None:
    compressible = SimpleNamespace(
        proposal=SimpleNamespace(
            solver_family=SimpleNamespace(value="rhoPimpleFoam")
        )
    )
    request = _request(
        "flow_rate",
        names=("inlet",),
        scope="patch",
    )
    plan = ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=(request,)),
        design=compressible,
        mesh_facts=(_mesh(),),
        registry=first_party_observation_registry(),
    )
    assert plan.items[0].evidence_strategy.kind == "unavailable"
    assert "solver/field" in plan.items[0].evidence_strategy.reason

    mass = request.model_copy(
        update={
            "quantity": "mass_flow_rate",
            "dimension": "1 0 -1 0 0 0 0",
        }
    )
    plan = ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=(mass,)),
        design=compressible,
        mesh_facts=(_mesh(),),
        registry=first_party_observation_registry(),
    )
    assert plan.items[0].evidence_strategy.kind == "postprocess_command"


def test_unknown_patch_or_zone_is_blocking_and_never_guessed() -> None:
    with pytest.raises(ObservationPlanningError, match="MESH_SCOPE_UNKNOWN"):
        _compile(
            _request("flow_rate", names=("missing",), scope="patch"),
        )
    with pytest.raises(ObservationPlanningError, match="MESH_SCOPE_UNKNOWN"):
        _compile(
            _request("region_average", names=("missing",), scope="cell_zone"),
        )


def test_duplicate_requests_are_deduplicated_by_identity() -> None:
    request = _request("continuity")
    plan = _compile(request, request)
    assert len(plan.items) == 1


def test_acceptance_observation_with_added_provenance_is_merged() -> None:
    request = _request("residual").model_copy(
        update={"time_selection": TimeSelection(kind="history")}
    )
    acceptance_observation = request.model_copy(
        update={
            "provenance": (
                FactEvidence(
                    kind="user_quote",
                    detail="residuals must remain finite",
                ),
            )
        }
    )
    acceptance = AcceptanceCompiler().compile(
        observation_requests=(request,),
        condition_requests=(
            AcceptanceRequest(
                condition_id="residuals-finite",
                observation=acceptance_observation,
                operator="finite",
                unit="1",
                scope=AcceptanceScope(time="all"),
                source="model_inference",
                confirmed=False,
                provenance=acceptance_observation.provenance,
            ),
        ),
    )

    plan = ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=(request,)),
        design=None,
        mesh_facts=(_mesh(),),
        registry=first_party_observation_registry(),
        acceptance_plan=acceptance,
    )

    assert plan.items[0].provenance == (
        *request.provenance,
        *acceptance_observation.provenance,
    )


def test_history_acceptance_observation_covers_final_intent_request() -> None:
    request = _request("continuity").model_copy(
        update={"time_selection": TimeSelection(kind="final")}
    )
    acceptance_observation = request.model_copy(
        update={
            "time_selection": TimeSelection(kind="history"),
            "provenance": (
                FactEvidence(
                    kind="user_quote",
                    detail="continuity must remain finite for all times",
                ),
            ),
        }
    )
    acceptance = AcceptanceCompiler().compile(
        observation_requests=(request,),
        condition_requests=(
            AcceptanceRequest(
                condition_id="continuity-finite",
                observation=acceptance_observation,
                operator="finite",
                unit="1",
                scope=AcceptanceScope(time="all"),
                source="user_text",
                confirmed=True,
                provenance=acceptance_observation.provenance,
            ),
        ),
    )

    plan = ObservationPlanner().compile(
        intent=SimulationIntent(observation_requests=(request,)),
        design=None,
        mesh_facts=(_mesh(),),
        registry=first_party_observation_registry(),
        acceptance_plan=acceptance,
    )

    assert plan.items[0].time_selection.kind == "history"


def test_acceptance_observation_with_distinct_semantics_still_conflicts() -> None:
    request = _request("continuity")
    conflicting = request.model_copy(
        update={"quantity": "distinct_quantity"}
    )
    acceptance = AcceptanceCompiler().compile(
        observation_requests=(),
        condition_requests=(
            AcceptanceRequest(
                condition_id="continuity-finite",
                observation=conflicting,
                operator="finite",
                unit="1",
                scope=AcceptanceScope(time="all"),
                source="model_inference",
                confirmed=False,
                provenance=conflicting.provenance,
            ),
        ),
    )

    with pytest.raises(
        ObservationPlanningError,
        match="OBSERVATION_ID_CONFLICT: continuity",
    ):
        ObservationPlanner().compile(
            intent=SimulationIntent(observation_requests=(request,)),
            design=None,
            mesh_facts=(_mesh(),),
            registry=first_party_observation_registry(),
            acceptance_plan=acceptance,
        )


def test_acceptance_condition_forces_observable_into_plan() -> None:
    request = _request("continuity")
    acceptance = AcceptanceCompiler().compile(
        observation_requests=(),
        condition_requests=(
            AcceptanceRequest(
                condition_id="continuity-limit",
                observation=request,
                operator="less_equal",
                limit=1.0e-5,
                unit="1",
                scope=AcceptanceScope(time="latest"),
                source="user_text",
                confirmed=True,
                provenance=request.provenance,
            ),
        ),
    )

    plan = ObservationPlanner().compile(
        intent=SimulationIntent(),
        design=None,
        mesh_facts=(_mesh(),),
        registry=first_party_observation_registry(),
        acceptance_plan=acceptance,
    )

    assert plan.items[0].observation_id == "continuity"
    assert plan.items[0].required_for_condition_ids == ("continuity-limit",)
