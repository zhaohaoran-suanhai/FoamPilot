from __future__ import annotations

import json

import pytest

from foampilot.assets import (
    AssetBundle,
    BundleMember,
    compute_bundle_manifest_sha256,
)
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.taskbuilder import (
    TaskIngressContext,
    extract_task_draft,
)
from foampilot.taskbuilder.extraction import _ExtractedTaskDraft
from foampilot.tasks import PublicAsset
from foampilot.models.schema import strict_response_schema


class RecordingExtractionGateway:
    primary_backend_id = "recording"
    primary_model = "recording-extractor"
    policy_sha256 = "a" * 64

    def __init__(self, payload) -> None:
        self.payload = payload
        self.requests = []

    def generate_structured(self, request, schema, *, budget, trace):
        del trace
        assert budget.stage == ModelStage.TASK_EXTRACTION
        self.requests.append(request)
        value = schema.model_validate(self.payload)
        return ModelResult(
            value=value,
            logical_request_id="extract-1",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def _budget():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.TASK_EXTRACTION,
        request_timeout_seconds=60,
        stage_deadline_seconds=90,
        max_transport_attempts=2,
    )


def _file_ingress_context(*assets: PublicAsset) -> TaskIngressContext:
    bundles = []
    for asset in assets:
        values = dict(
            adapter_id="foampilot.asset.public-file",
            kind="public_file",
            source_path=asset.path,
            install_path=asset.path,
            region=None,
            members=(
                BundleMember(
                    relative_path=asset.path.rsplit("/", 1)[-1],
                    logical_name=asset.path.rsplit("/", 1)[-1],
                    sha256=asset.sha256,
                    bytes=1,
                ),
            ),
        )
        bundles.append(
            AssetBundle(
                **values,
                manifest_sha256=compute_bundle_manifest_sha256(**values),
            )
        )
    return TaskIngressContext(asset_bundles=tuple(bundles))


def _payload(*, source="user_text", confirmed=True):
    return {
        "schema_version": 1,
        "facts": [
            {
                "path": "physics.regime",
                "value": '"steady"',
                "source": source,
                "evidence": "稳态层流",
                "impact": "high",
                "confirmed": confirmed,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def test_extraction_response_schema_encodes_arbitrary_fact_values_as_json_text() -> None:
    schema = strict_response_schema(_ExtractedTaskDraft.model_json_schema())

    fact_schema = schema["$defs"]["_ExtractedFact"]
    assert fact_schema["properties"]["value"] == {"type": "string"}

    def empty_schemas(value):
        if isinstance(value, dict):
            if not value:
                yield value
            for item in value.values():
                yield from empty_schemas(item)
        elif isinstance(value, list):
            for item in value:
                yield from empty_schemas(item)

    assert list(empty_schemas(schema)) == []


def test_extraction_transport_model_rejects_invalid_domain_path_early() -> None:
    payload = _payload()
    payload["facts"][0]["path"] = "initial_conditions.U"

    with pytest.raises(ValueError, match="literal_error"):
        _ExtractedTaskDraft.model_validate(payload)


def test_extractor_collapses_equivalent_duplicate_fact_paths() -> None:
    payload = _payload()
    payload["facts"].append(dict(payload["facts"][0]))
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert [item.path for item in draft.facts].count("physics.regime") == 1
    assert draft.fact_map()["physics.regime"].confirmed is True


def test_extractor_downgrades_conflicting_duplicate_fact_paths() -> None:
    payload = _payload()
    conflict = dict(payload["facts"][0])
    conflict["value"] = '"transient"'
    payload["facts"].append(conflict)
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["physics.regime"]
    assert fact.source == "model_inference"
    assert fact.confirmed is False
    assert "conflicting duplicate" in fact.evidence


def test_extractor_collapses_semantically_equivalent_json_fact_values() -> None:
    payload = _payload()
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"rho":1,"unit":"kg/m3"}',
        evidence="rho 1 kg/m3",
    )
    duplicate = dict(payload["facts"][0])
    duplicate["value"] = '{"unit":"kg/m3", "rho": 1}'
    payload["facts"].append(duplicate)
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use rho 1 kg/m3.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["materials.fluid"]
    assert fact.source == "user_text"
    assert fact.confirmed is True


def test_extractor_uses_structured_stage_for_chinese_request() -> None:
    gateway = RecordingExtractionGateway(_payload())

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        protected_paths=("/private/target",),
    )

    assert draft.status == "incomplete"
    assert [item.path for item in draft.unresolved_questions] == ["geometry"]
    assert draft.facts[0].source == "user_text"
    assert gateway.requests[0].purpose == "extract-cfd-task-draft"
    assert "不得虚构" in gateway.requests[0].system_prompt
    assert "initial.conditions" in gateway.requests[0].system_prompt
    assert 'physics.regime 只能是 "steady" 或 "transient"' in (
        gateway.requests[0].system_prompt
    )
    assert "reference cell" in gateway.requests[0].system_prompt
    assert 'dimensionality="two_d"' in gateway.requests[0].system_prompt
    assert "target_cell_count" in gateway.requests[0].system_prompt
    assert '{"name":"top","role":"wall"}' in gateway.requests[0].system_prompt
    assert "patch name 不得使用中文" in gateway.requests[0].system_prompt
    assert "require_check_mesh_pass" in gateway.requests[0].system_prompt
    assert "layer_count=null" in gateway.requests[0].system_prompt
    assert "/private/target" not in gateway.requests[0].user_prompt


def test_extractor_sends_only_declared_asset_metadata() -> None:
    gateway = RecordingExtractionGateway(_payload())
    asset = PublicAsset(
        path="geometry/body.stl",
        sha256="b" * 64,
        purpose="public body surface",
    )

    draft = extract_task_draft(
        "Use the attached body surface.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.assets == [asset]
    prompt = gateway.requests[0].user_prompt
    assert "geometry/body.stl" in prompt
    assert "b" * 64 in prompt
    assert "/home/" not in prompt


def test_extractor_downgrades_invented_high_impact_property() -> None:
    payload = _payload(source="model_inference", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"value": 1e-6, "unit": "m2/s"}',
        evidence="typical water value",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve a flow, material not specified.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "incomplete"
    assert draft.facts[0].confirmed is False
    assert draft.facts[0].source == "model_inference"


def test_extractor_downgrades_user_fact_without_verbatim_evidence() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"value": 1e-6, "unit": "m2/s"}',
        evidence="typical water viscosity",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve a flow without a specified fluid property.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.facts[0].source == "model_inference"
    assert draft.facts[0].confirmed is False
    assert draft.status == "incomplete"


def test_extractor_confirms_user_fact_from_verbatim_evidence() -> None:
    gateway = RecordingExtractionGateway(_payload(confirmed=False))

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "incomplete"
    assert draft.facts[0].source == "user_text"
    assert draft.facts[0].confirmed is True


def test_extractor_does_not_confirm_value_unrelated_to_verbatim_evidence() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="physics.solver",
        value='"madeUpFoam"',
        evidence="flow",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve this flow on the supplied geometry.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    solver = draft.fact_map()["physics.solver"]
    assert solver.source == "user_text"
    assert solver.confirmed is False


def test_extractor_does_not_match_compressible_inside_incompressible() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="physics.compressibility",
        value='"compressible"',
        evidence="incompressible flow",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Solve an incompressible flow.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["physics.compressibility"]
    assert fact.confirmed is False


@pytest.mark.parametrize(
    ("path", "value", "evidence"),
    [
        ("physics.compressibility", "compressible", "不可压缩流动"),
        ("physics.regime", "steady", "非稳态启动流动"),
        ("physics.regime", "steady", "不是稳态流动"),
        ("physics.regime", "steady", "not steady flow"),
        ("boundaries", [{"role": "wall"}], "not a wall"),
    ],
)
def test_extractor_does_not_confirm_negated_chinese_alias(
    path: str,
    value: str,
    evidence: str,
) -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path=path,
        value=json.dumps(value),
        evidence=evidence,
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        f"求解{evidence}。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()[path].confirmed is False


def test_extractor_matches_equivalent_scientific_notation() -> None:
    payload = _payload(source="user_text", confirmed=False)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"nu":{"value":0.000001,"unit":"m2/s"}}',
        evidence="nu = 1e-6 m2/s",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use a fluid with nu = 1e-6 m2/s.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()["materials.fluid"].confirmed is True


def test_extractor_binds_nested_values_to_semantic_field_names() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"nu":{"value":0.000001,"unit":"m2/s"}}',
        evidence="alpha = 1e-6 m2/s",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use alpha = 1e-6 m2/s.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()["materials.fluid"].confirmed is False


def test_extractor_can_verify_explicit_boolean_user_fact() -> None:
    payload = _payload(source="user_text", confirmed=False)
    payload["facts"][0].update(
        path="boundaries",
        value='{"allow_reverse_flow":false}',
        evidence="allow_reverse_flow = false",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use the supplied geometry; allow_reverse_flow = false.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    fact = draft.fact_map()["boundaries"]
    assert fact.source == "user_text"
    assert fact.confirmed is True


def test_extractor_discards_design_owned_model_questions() -> None:
    payload = _payload()
    payload["unresolved_questions"] = [
        {
            "question_id": "q_solver",
            "path": "physics.solver",
            "kind": "blocking",
            "prompt_zh": "请选择 solver。",
            "reason_zh": "模型错误地提前追问工程设计。",
        }
    ]
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert [item.path for item in draft.unresolved_questions] == ["geometry"]


def test_extractor_recursively_binds_nested_user_fact_values() -> None:
    payload = _payload(source="user_text", confirmed=True)
    payload["facts"][0].update(
        path="materials.fluid",
        value='{"nu":{"value":0.005,"unit":"m2/s"}}',
        evidence="nu is 0.005 m2/s",
    )
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "Use a fluid whose nu is 0.005 m2/s.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.fact_map()["materials.fluid"].confirmed is True


def test_extractor_never_grants_public_asset_authority_to_model_fact() -> None:
    payload = _payload(source="public_asset", confirmed=True)
    payload["facts"][0].update(
        path="physics.solver",
        value='"madeUpFoam"',
        evidence="geometry/body.stl",
    )
    gateway = RecordingExtractionGateway(payload)
    asset = PublicAsset(
        path="geometry/body.stl",
        sha256="b" * 64,
        purpose="public body surface",
    )

    draft = extract_task_draft(
        "Solve this flow using the declared surface.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    solver = draft.fact_map()["physics.solver"]
    assert solver.source == "model_inference"
    assert solver.confirmed is False


def test_extraction_transport_rejects_fact_path_outside_declared_vocabulary() -> None:
    payload = _payload()
    payload["facts"][0]["path"] = "physics.secret_route"

    with pytest.raises(ValueError, match="literal_error"):
        _ExtractedTaskDraft.model_validate(payload)


def test_extractor_accepts_balanced_chinese_quote_around_user_evidence() -> None:
    payload = _payload(confirmed=False)
    payload["facts"][0]["evidence"] = "“稳态层流”"
    gateway = RecordingExtractionGateway(payload)

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.facts[0].source == "user_text"
    assert draft.facts[0].confirmed is True


def test_extractor_receives_compact_topology_context() -> None:
    gateway = RecordingExtractionGateway(_payload())
    context = TaskIngressContext.model_validate(
        {
            "target": {"distribution": "foundation", "version": "10"},
            "asset_bundles": [],
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 0.1],
                    },
                    "patches": [
                        {
                            "name": "frontAndBack",
                            "patch_type": "empty",
                            "start_face": 7,
                            "face_count": 4,
                        }
                    ],
                    "cell_zones": [
                        {"name": "zoneA", "element_count": 1}
                    ],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": [
                        "empty patch frontAndBack"
                    ],
                    "topology_observations": [
                        "boundary face coverage is contiguous"
                    ],
                    "warnings": [],
                }
            ],
        }
    )

    extract_task_draft(
        "Use the supplied mesh for a steady flow.",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=context,
    )

    prompt = gateway.requests[0].user_prompt
    assert "PolyMeshTopologyFacts" in prompt
    assert "frontAndBack" in prompt
    assert "zoneA" in prompt
    assert "unscaled_bounds" in prompt
    assert "FoamFile" not in prompt


def test_ingress_context_has_deterministic_serialized_size_limit() -> None:
    context = TaskIngressContext.model_validate(
        {
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 0.1],
                    },
                    "patches": [
                        {
                            "name": "p" + "x" * 270_000,
                            "patch_type": "patch",
                            "start_face": 1,
                            "face_count": 1,
                        }
                    ],
                    "cell_zones": [],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": [],
                    "topology_observations": [],
                    "warnings": [],
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="TASK_INGRESS_CONTEXT_TOO_LARGE"):
        context.agent_payload()


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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context_payload = {
        "target": {"distribution": "foundation", "version": "10"},
        "asset_bundles": [],
        "poly_mesh_topologies": [
            {
                "bundle_manifest_sha256": "c" * 64,
                "inspector_id": "foampilot.mesh.poly-mesh",
                "inspector_version": "1.0.0",
                "region": None,
                "source_member_sha256": {},
                "points": 12,
                "faces": 11,
                "internal_faces": 1,
                "cells": 2,
                "unscaled_bounds": {
                    "minimum": [0.0, 0.0, 0.0],
                    "maximum": [2.0, 1.0, 0.1],
                },
                "patches": [
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
                "cell_zones": [
                    {"name": "volumeZone", "element_count": 1}
                ],
                "face_zones": [],
                "point_zones": [],
                "dimensionality_observations": ["empty patch thinFaces"],
                "topology_observations": [
                    "boundary face coverage is contiguous"
                ],
                "warnings": [],
            }
        ],
    }

    draft = extract_task_draft(
        "Use the supplied native mesh for a two-dimensional flow.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=TaskIngressContext.model_validate(context_payload),
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context = TaskIngressContext.model_validate(
        {
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 0.1],
                    },
                    "patches": [
                        {
                            "name": "thinFaces",
                            "patch_type": "empty",
                            "start_face": 3,
                            "face_count": 4,
                        }
                    ],
                    "cell_zones": [],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": ["empty patch thinFaces"],
                    "topology_observations": [],
                    "warnings": [],
                }
            ]
        }
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context = TaskIngressContext.model_validate(
        {
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 0.1],
                    },
                    "patches": [
                        {
                            "name": "thinFaces",
                            "patch_type": "empty",
                            "start_face": 3,
                            "face_count": 4,
                        }
                    ],
                    "cell_zones": [],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": ["empty patch thinFaces"],
                    "topology_observations": [],
                    "warnings": [],
                }
            ]
        }
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context = TaskIngressContext.model_validate(
        {
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 1.0],
                    },
                    "patches": [
                        {
                            "name": "walls",
                            "patch_type": "wall",
                            "start_face": 3,
                            "face_count": 4,
                        }
                    ],
                    "cell_zones": [],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": [],
                    "topology_observations": [],
                    "warnings": [],
                }
            ]
        }
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context_payload = {
        "poly_mesh_topologies": [
            {
                "bundle_manifest_sha256": "c" * 64,
                "inspector_id": "foampilot.mesh.poly-mesh",
                "inspector_version": "1.0.0",
                "region": None,
                "source_member_sha256": {},
                "points": 12,
                "faces": 11,
                "internal_faces": 1,
                "cells": 2,
                "unscaled_bounds": {
                    "minimum": [0.0, 0.0, 0.0],
                    "maximum": [2.0, 1.0, 0.1],
                },
                "patches": [
                    {
                        "name": "thinFaces",
                        "patch_type": "empty",
                        "start_face": 3,
                        "face_count": 4,
                    }
                ],
                "cell_zones": [],
                "face_zones": [],
                "point_zones": [],
                "dimensionality_observations": ["empty patch thinFaces"],
                "topology_observations": [],
                "warnings": [],
            }
        ]
    }

    draft = extract_task_draft(
        "Use the supplied three_d mesh in m.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=TaskIngressContext.model_validate(context_payload),
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context_payload = {
        "poly_mesh_topologies": [
            {
                "bundle_manifest_sha256": "c" * 64,
                "inspector_id": "foampilot.mesh.poly-mesh",
                "inspector_version": "1.0.0",
                "region": "fluid",
                "source_member_sha256": {},
                "points": 12,
                "faces": 11,
                "internal_faces": 1,
                "cells": 2,
                "unscaled_bounds": {
                    "minimum": [0.0, 0.0, 0.0],
                    "maximum": [2.0, 1.0, 0.1],
                },
                "patches": [
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
                "cell_zones": [],
                "face_zones": [],
                "point_zones": [],
                "dimensionality_observations": ["empty patch thinFaces"],
                "topology_observations": [],
                "warnings": [],
            }
        ]
    }

    draft = extract_task_draft(
        "Use the supplied two_d mesh in m; patch feed is inlet; region fluid is fluid.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=TaskIngressContext.model_validate(context_payload),
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context = TaskIngressContext.model_validate(
        {
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 0.1],
                    },
                    "patches": [
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
                    "cell_zones": [],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": ["empty patch thinFaces"],
                    "topology_observations": [],
                    "warnings": [],
                }
            ]
        }
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
    asset = PublicAsset(
        path="mesh/native",
        sha256="c" * 64,
        purpose="native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256="c" * 64,
    )
    context = TaskIngressContext.model_validate(
        {
            "poly_mesh_topologies": [
                {
                    "bundle_manifest_sha256": "c" * 64,
                    "inspector_id": "foampilot.mesh.poly-mesh",
                    "inspector_version": "1.0.0",
                    "region": None,
                    "source_member_sha256": {},
                    "points": 12,
                    "faces": 11,
                    "internal_faces": 1,
                    "cells": 2,
                    "unscaled_bounds": {
                        "minimum": [0.0, 0.0, 0.0],
                        "maximum": [2.0, 1.0, 0.1],
                    },
                    "patches": [
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
                    "cell_zones": [],
                    "face_zones": [],
                    "point_zones": [],
                    "dimensionality_observations": ["empty patch thinFaces"],
                    "topology_observations": [],
                    "warnings": [],
                }
            ]
        }
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


@pytest.mark.parametrize(
    ("path", "mode", "format_name", "strategy"),
    [
        ("geometry/body.stl", "surface", "stl", None),
        ("geometry/channel.geo", "gmsh", "geo", "gmsh"),
    ],
)
def test_public_file_geometry_route_mints_asset_authority(
    path: str,
    mode: str,
    format_name: str,
    strategy: str | None,
) -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": json.dumps(
                    {
                        "mode": mode,
                        "dimensionality": "three_d",
                        "description": "declared geometry",
                        "length_unit": "mm",
                        "assets": [
                            {
                                "path": path,
                                "format": format_name,
                                "role": "geometry",
                            }
                        ],
                    }
                ),
                "source": "public_asset",
                "evidence": "three_d geometry in mm",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    asset = PublicAsset(
        path=path,
        sha256="b" * 64,
        purpose="declared geometry",
    )
    draft = extract_task_draft(
        f"Use the declared three_d geometry in mm from {path}.",
        [asset],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(asset),
    )

    facts = draft.fact_map()
    assert facts["geometry"].source == "public_asset"
    assert facts["geometry"].value["mode"] == mode
    assert facts["geometry.length_unit"].value == "mm"
    assert facts["geometry.dimensionality"].value == "three_d"
    if strategy is not None:
        assert facts["mesh"].value["strategy"] == strategy
    assert draft.unresolved_questions == []


def test_surface_route_ignores_auxiliary_non_geometry_asset() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"surface","dimensionality":"three_d",'
                    '"description":"body","length_unit":"m","assets":[]}'
                ),
                "source": "user_text",
                "evidence": "three_d geometry in m",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.stl", sha256="b" * 64, purpose="body"
    )
    profile = PublicAsset(
        path="data/profile.csv", sha256="d" * 64, purpose="inlet profile"
    )

    draft = extract_task_draft(
        "Use body.stl as three_d geometry in m and profile.csv as inlet data.",
        [surface, profile],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface, profile),
    )

    assert draft.fact_map()["geometry"].source == "public_asset"
    assert draft.fact_map()["geometry"].value["assets"] == [
        {"path": "geometry/body.stl", "format": "stl", "role": "surface_geometry"}
    ]


def test_surface_route_blocks_conflicting_explicit_mesh_strategy() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"surface","dimensionality":"three_d",'
                    '"description":"body","length_unit":"m","assets":[]}'
                ),
                "source": "user_text",
                "evidence": "three_d geometry in m",
                "impact": "high",
                "confirmed": False,
            },
            {
                "path": "mesh",
                "value": '{"strategy":"provided"}',
                "source": "user_text",
                "evidence": "provided mesh",
                "impact": "high",
                "confirmed": True,
            },
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.stl", sha256="b" * 64, purpose="body"
    )

    draft = extract_task_draft(
        "Use body.stl as three_d geometry in m; use the provided mesh.",
        [surface],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface),
    )

    assert draft.fact_map()["mesh"].value == {"strategy": "provided"}
    assert draft.status == "incomplete"
    assert [item.path for item in draft.unresolved_questions] == ["mesh"]


def test_surface_route_preserves_user_roles_for_later_geometry_probe() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"surface","dimensionality":"three_d",'
                    '"description":"body","length_unit":"m","assets":[],'
                    '"patch_roles":[{"name":"body","role":"wall"}]}'
                ),
                "source": "user_text",
                "evidence": "three_d geometry in m; body is wall",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.stl", sha256="b" * 64, purpose="body"
    )

    draft = extract_task_draft(
        "Use body.stl as three_d geometry in m; body is wall.",
        [surface],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface),
    )

    assert draft.fact_map()["geometry.patch_roles"].value == [
        {"name": "body", "role": "wall"}
    ]


def test_public_file_route_rebuilds_model_question_identifiers() -> None:
    payload = {
        "schema_version": 1,
        "facts": [],
        "assumptions": [],
        "unresolved_questions": [
            {
                "question_id": "q_geometry_dimensionality",
                "path": "geometry.length_unit",
                "kind": "blocking",
                "prompt_zh": "单位是什么？",
                "reason_zh": "模型未找到单位。",
            }
        ],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.obj", sha256="b" * 64, purpose="body"
    )

    draft = extract_task_draft(
        "Use body.obj as geometry.",
        [surface],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface),
    )

    assert [(item.question_id, item.path) for item in draft.unresolved_questions] == [
        ("q_geometry_length_unit", "geometry.length_unit"),
        ("q_geometry_dimensionality", "geometry.dimensionality"),
    ]


def test_resolved_question_cannot_forge_conflict_with_model_identifier() -> None:
    payload = {
        "schema_version": 1,
        "facts": [
            {
                "path": "geometry",
                "value": (
                    '{"mode":"surface","dimensionality":"three_d",'
                    '"description":"body","length_unit":"m","assets":[]}'
                ),
                "source": "user_text",
                "evidence": "three_d geometry in m",
                "impact": "high",
                "confirmed": False,
            }
        ],
        "assumptions": [],
        "unresolved_questions": [
            {
                "question_id": "q_fake_conflict",
                "path": "geometry.length_unit",
                "kind": "blocking",
                "prompt_zh": "单位是什么？",
                "reason_zh": "模型伪造冲突标识。",
            }
        ],
    }
    gateway = RecordingExtractionGateway(payload)
    surface = PublicAsset(
        path="geometry/body.stl", sha256="b" * 64, purpose="body"
    )

    draft = extract_task_draft(
        "Use body.stl as three_d geometry in m.",
        [surface],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=_file_ingress_context(surface),
    )

    assert draft.status == "confirmed"
    assert draft.unresolved_questions == []


def test_extractor_does_not_allow_model_to_claim_user_confirmation() -> None:
    gateway = RecordingExtractionGateway(
        _payload(source="user_confirmation", confirmed=True)
    )

    draft = extract_task_draft(
        "求解一个稳态层流通道。",
        [],
        gateway,
        budget=_budget(),
        trace=InMemoryModelTraceSink(),
    )

    assert draft.status == "incomplete"
    assert draft.facts[0].source == "model_inference"
    assert draft.facts[0].confirmed is False


def test_extractor_rejects_protected_path_before_model_call() -> None:
    gateway = RecordingExtractionGateway(_payload())

    try:
        extract_task_draft(
            "Read /private/target and solve it.",
            [],
            gateway,
            budget=_budget(),
            trace=InMemoryModelTraceSink(),
            protected_paths=("/private/target",),
        )
    except ValueError as error:
        assert "protected path" in str(error)
    else:
        raise AssertionError("protected request must fail")
    assert gateway.requests == []


def test_extractor_rejects_protected_path_in_model_output() -> None:
    payload = _payload()
    payload["facts"][0]["evidence"] = "/private/target"
    gateway = RecordingExtractionGateway(payload)

    try:
        extract_task_draft(
            "Solve a public flow.",
            [],
            gateway,
            budget=_budget(),
            trace=InMemoryModelTraceSink(),
            protected_paths=("/private/target",),
        )
    except ValueError as error:
        assert "protected path" in str(error)
    else:
        raise AssertionError("protected output must fail")
