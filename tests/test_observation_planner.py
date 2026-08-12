from __future__ import annotations

import pytest

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


def _request(
    kind: str,
    *,
    names: tuple[str, ...] = (),
    scope: str = "global",
    time: str = "latest",
) -> ObservationRequest:
    return ObservationRequest(
        observation_id=(kind + ("-" + "-".join(names) if names else "")),
        kind=kind,
        quantity=kind,
        dimension="1",
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
