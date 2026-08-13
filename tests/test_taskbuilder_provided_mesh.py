from __future__ import annotations

from foampilot.models import InMemoryModelTraceSink
from foampilot.taskbuilder import extract_task_draft
from tests.support.taskbuilder import (
    RecordingExtractionGateway,
    extraction_payload as _payload,
    poly_mesh_topology_payload,
    provided_mesh_asset,
    provided_mesh_ingress_context,
    task_extraction_budget as _budget,
)


def test_provided_mesh_reconciliation_removes_design_and_topology_questions() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"gmsh","dimensionality":"two_d",'
                    '"description":"supplied channel mesh",'
                    '"length_unit":null,"assets":[]}'
                ),
                "source": "model_inference",
                "evidence": "interpreted as an imported mesh",
                "impact": "high",
                "confirmed": False,
            },
            {
                "path": "mesh",
                "value": '{"strategy":"gmsh"}',
                "source": "model_inference",
                "evidence": "interpreted as imported",
                "impact": "high",
                "confirmed": False,
            },
        ],
        "assumptions": [],
        "unresolved_questions": [
            {
                "question_id": "q_patch_names",
                "path": "geometry",
                "kind": "blocking",
                "prompt_zh": "请提供 patch 与 zone 名称。",
                "reason_zh": "模型没有读取网格。",
            },
            {
                "question_id": "q_solver",
                "path": "physics.solver",
                "kind": "blocking",
                "prompt_zh": "请选择 solver。",
                "reason_zh": "提示词没有指定 solver。",
            },
        ],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "sideA",
                    "patch_type": "patch",
                    "start_face": 1,
                    "face_count": 2,
                },
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 3,
                    "face_count": 4,
                },
            ],
            cell_zones=[{"name": "volumeZone", "element_count": 1}],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied native mesh for a two-dimensional flow.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    facts = draft.fact_map()
    assert facts["geometry"].value["mode"] == "openfoam_mesh"
    assert facts["geometry"].value["dimensionality"] == "two_d"
    assert facts["geometry"].value["assets"] == [
        {"path": "mesh/native", "format": "openfoam_mesh", "role": "volume_mesh"}
    ]
    assert facts["mesh"].value == {"strategy": "provided"}
    assert [item.path for item in draft.unresolved_questions] == [
        "geometry.length_unit"
    ]
    assert draft.status == "incomplete"


def test_provided_mesh_keeps_user_unit_separate_from_asset_geometry() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"two_d",'
                    '"description":"supplied mesh","length_unit":"mm",'
                    '"assets":[]}'
                ),
                "source": "user_text",
                "evidence": "length unit is mm",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 3,
                    "face_count": 4,
                }
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied mesh; its length unit is mm.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    assert draft.fact_map()["geometry"].value["length_unit"] is None
    unit = draft.fact_map()["geometry.length_unit"]
    assert unit.value == "mm"
    assert unit.source == "user_text"
    assert unit.confirmed is True
    assert draft.unresolved_questions == []


def test_provided_mesh_reconciliation_rejects_model_inferred_length_unit() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"two_d",'
                    '"description":"supplied mesh","length_unit":"m",'
                    '"assets":[]}'
                ),
                "source": "model_inference",
                "evidence": "OpenFOAM commonly uses SI units",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 3,
                    "face_count": 4,
                }
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied native mesh for a two-dimensional flow.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    assert draft.fact_map()["geometry"].value["length_unit"] is None
    assert [question.path for question in draft.unresolved_questions] == [
        "geometry.length_unit"
    ]


def test_user_dimensionality_without_empty_patch_keeps_user_provenance() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"three_d",'
                    '"description":"supplied mesh","length_unit":"m",'
                    '"assets":[]}'
                ),
                "source": "user_text",
                "evidence": "three_d mesh in m",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "walls",
                    "patch_type": "wall",
                    "start_face": 3,
                    "face_count": 4,
                }
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 1.0],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied three_d mesh in m.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    assert draft.fact_map()["geometry"].value["dimensionality"] is None
    dimension = draft.fact_map()["geometry.dimensionality"]
    assert dimension.value == "three_d"
    assert dimension.source == "user_text"
    assert dimension.confirmed is True
    assert draft.unresolved_questions == []


def test_empty_patch_conflicting_with_user_three_d_is_blocking() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"three_d",'
                    '"description":"supplied mesh","length_unit":"m",'
                    '"assets":[],"patch_roles":[],"region_roles":[]}'
                ),
                "source": "user_text",
                "evidence": "three_d mesh in m",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 3,
                    "face_count": 4,
                }
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied three_d mesh in m.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    assert draft.status == "incomplete"
    assert draft.fact_map()["geometry"].value["dimensionality"] == "two_d"
    assert [item.path for item in draft.unresolved_questions] == [
        "geometry.dimensionality"
    ]


def test_provided_mesh_preserves_correlated_user_patch_and_region_roles() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"two_d",'
                    '"description":"supplied mesh","length_unit":"m",'
                    '"assets":[],"patch_roles":[{"name":"feed","role":"inlet"}],'
                    '"region_roles":[{"name":"fluid","role":"fluid"}]}'
                ),
                "source": "user_text",
                "evidence": (
                    "two_d mesh in m; patch feed is inlet; region fluid is fluid"
                ),
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            region="fluid",
            patches=[
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 3,
                    "face_count": 4,
                },
                {
                    "name": "feed",
                    "patch_type": "patch",
                    "start_face": 7,
                    "face_count": 1,
                },
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied two_d mesh in m; patch feed is inlet; region fluid is fluid.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    facts = draft.fact_map()
    assert facts["geometry.patch_roles"].value == [
        {"name": "feed", "role": "inlet"}
    ]
    assert facts["geometry.region_roles"].value == [
        {"name": "fluid", "role": "fluid"}
    ]


def test_provided_mesh_rejects_user_role_missing_from_topology() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"two_d",'
                    '"description":"mesh","length_unit":"m","assets":[],'
                    '"patch_roles":[{"name":"ghost","role":"inlet"}]}'
                ),
                "source": "user_text",
                "evidence": "two_d mesh in m; patch ghost is inlet",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "feed",
                    "patch_type": "patch",
                    "start_face": 3,
                    "face_count": 4,
                },
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 7,
                    "face_count": 4,
                },
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied two_d mesh in m; patch ghost is inlet.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    assert draft.status == "incomplete"
    assert "geometry.patch_roles" not in draft.fact_map()
    assert [item.path for item in draft.unresolved_questions] == [
        "geometry.patch_roles"
    ]


def test_provided_mesh_rejects_malformed_role_shape_without_crashing() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"openfoam_mesh","dimensionality":"two_d",'
                    '"description":"mesh","length_unit":"m","assets":[],'
                    '"patch_roles":["feed"]}'
                ),
                "source": "user_text",
                "evidence": "two_d mesh in m; patch feed is inlet",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = provided_mesh_asset()
    context = provided_mesh_ingress_context(
        poly_mesh_topology_payload(
            patches=[
                {
                    "name": "feed",
                    "patch_type": "patch",
                    "start_face": 3,
                    "face_count": 4,
                },
                {
                    "name": "thinFaces",
                    "patch_type": "empty",
                    "start_face": 7,
                    "face_count": 4,
                },
            ],
            bounds={
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [2.0, 1.0, 0.1],
            },
        )
    )

    draft = extract_task_draft(
        "Use the supplied two_d mesh in m; patch feed is inlet.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    assert "geometry.patch_roles" not in draft.fact_map()
    assert draft.status == "incomplete"
