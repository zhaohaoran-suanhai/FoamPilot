from __future__ import annotations

from foampilot.assets import (
    AssetBundle,
    BundleMember,
    compute_bundle_manifest_sha256,
)
from foampilot.models import (
    ModelBudgetLedger,
    ModelBudgetWindow,
    ModelResult,
    ModelStage,
)
from foampilot.taskbuilder import TaskIngressContext
from foampilot.tasks import PublicAsset


class RecordingExtractionGateway:
    primary_backend_id = "recording"
    primary_model = "recording-extractor"
    policy_sha256 = "a" * 64

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[object] = []

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


def task_extraction_budget() -> ModelBudgetWindow:
    return ModelBudgetLedger.start().open_stage(
        ModelStage.TASK_EXTRACTION,
        request_timeout_seconds=60,
        stage_deadline_seconds=90,
        max_transport_attempts=2,
    )


def extraction_payload(
    *,
    facts: list[dict[str, object]] | None = None,
    assumptions: list[dict[str, object]] | None = None,
    unresolved_questions: list[dict[str, object]] | None = None,
    source: str = "user_text",
    confirmed: bool = True,
) -> dict[str, object]:
    if facts is None:
        facts = [
            {
                "path": "physics.regime",
                "value": '"steady"',
                "source": source,
                "evidence": "稳态层流",
                "impact": "high",
                "confirmed": confirmed,
            }
        ]
    return {
        "schema_version": 1,
        "facts": facts,
        "assumptions": assumptions or [],
        "unresolved_questions": unresolved_questions or [],
    }


def file_ingress_context(*assets: PublicAsset) -> TaskIngressContext:
    bundles = []
    for asset in assets:
        member_name = asset.path.rsplit("/", 1)[-1]
        values = dict(
            adapter_id="foampilot.asset.public-file",
            kind="public_file",
            source_path=asset.path,
            install_path=asset.path,
            region=None,
            members=(
                BundleMember(
                    relative_path=member_name,
                    logical_name=member_name,
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


def provided_mesh_asset(
    *,
    path: str = "mesh/native",
    manifest_sha256: str = "c" * 64,
    install_path: str = "constant/polyMesh",
) -> PublicAsset:
    return PublicAsset(
        path=path,
        sha256=manifest_sha256,
        purpose="native mesh",
        kind="directory",
        install_path=install_path,
        bundle_manifest_sha256=manifest_sha256,
    )


def poly_mesh_topology_payload(
    *,
    manifest_sha256: str = "c" * 64,
    region: str | None = None,
    patches: list[dict[str, object]] | None = None,
    cell_zones: list[dict[str, object]] | None = None,
    bounds: dict[str, list[float]] | None = None,
) -> dict[str, object]:
    patch_values = patches or []
    empty_names = [
        str(patch["name"])
        for patch in patch_values
        if patch.get("patch_type") == "empty"
    ]
    return {
        "bundle_manifest_sha256": manifest_sha256,
        "inspector_id": "foampilot.mesh.poly-mesh",
        "inspector_version": "1.0.0",
        "region": region,
        "source_member_sha256": {},
        "points": 12,
        "faces": 11,
        "internal_faces": 1,
        "cells": 2,
        "unscaled_bounds": bounds
        or {
            "minimum": [0.0, 0.0, 0.0],
            "maximum": [2.0, 1.0, 0.1],
        },
        "patches": patch_values,
        "cell_zones": cell_zones or [],
        "face_zones": [],
        "point_zones": [],
        "dimensionality_observations": [
            f"empty patch {name}" for name in empty_names
        ],
        "topology_observations": [],
        "warnings": [],
    }


def provided_mesh_ingress_context(
    *topologies: dict[str, object],
) -> TaskIngressContext:
    return TaskIngressContext.model_validate(
        {"poly_mesh_topologies": list(topologies)}
    )


__all__ = [
    "RecordingExtractionGateway",
    "extraction_payload",
    "file_ingress_context",
    "poly_mesh_topology_payload",
    "provided_mesh_asset",
    "provided_mesh_ingress_context",
    "task_extraction_budget",
]
