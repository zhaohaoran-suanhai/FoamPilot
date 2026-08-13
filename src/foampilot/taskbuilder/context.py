"""Deterministic public facts available before task extraction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from foampilot.assets import (
    AssetBundle,
    OpenFOAMPolyMeshAdapter,
    PublicFileAdapter,
)
from foampilot.preprocessing import PolyMeshTopologyFacts, inspect_poly_mesh_topology
from foampilot.tasks import OpenFOAMTarget, PublicAsset


class TaskIngressContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    target: OpenFOAMTarget = OpenFOAMTarget(
        distribution="foundation",
        version="10",
    )
    asset_bundles: tuple[AssetBundle, ...] = ()
    poly_mesh_topologies: tuple[PolyMeshTopologyFacts, ...] = ()

    @model_validator(mode="after")
    def validate_frozen_product_target(self):
        if (
            self.target.distribution != "foundation"
            or self.target.version != "10"
        ):
            raise ValueError(
                "task ingress target must be Foundation OpenFOAM 10"
            )
        return self

    def agent_payload(self) -> dict[str, object]:
        """Return bounded public facts without raw mesh or member content."""

        entity_count = sum(
            len(facts.patches)
            + len(facts.cell_zones)
            + len(facts.face_zones)
            + len(facts.point_zones)
            for facts in self.poly_mesh_topologies
        )
        if entity_count > 4096:
            raise ValueError(
                "TASK_INGRESS_CONTEXT_TOO_LARGE: topology has more than "
                "4096 named patch/zone entities"
            )
        payload = {
            "target": self.target.model_dump(mode="json"),
            "AssetFacts": [
                {
                    "fact_id": f"asset:{bundle.manifest_sha256}",
                    "kind": bundle.kind,
                    "source_path": bundle.source_path,
                    "install_path": bundle.install_path,
                    "region": bundle.region,
                    "manifest_sha256": bundle.manifest_sha256,
                    "member_count": len(bundle.members),
                }
                for bundle in self.asset_bundles
            ],
            "PolyMeshTopologyFacts": [
                facts.model_dump(mode="json")
                for facts in self.poly_mesh_topologies
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 256 * 1024:
            raise ValueError(
                "TASK_INGRESS_CONTEXT_TOO_LARGE: compact public context "
                "exceeds 256 KiB"
            )
        return payload


def build_task_ingress_context(
    assets: list[PublicAsset],
    asset_root: str | Path,
) -> TaskIngressContext:
    """Validate declared assets and inspect native mesh topology."""

    root = Path(asset_root).resolve()
    bundles: list[AssetBundle] = []
    topologies: list[PolyMeshTopologyFacts] = []
    poly_mesh_adapter = OpenFOAMPolyMeshAdapter()
    public_file_adapter = PublicFileAdapter()
    for asset in assets:
        if asset.kind == "file":
            bundles.append(public_file_adapter.inspect(root, asset))
            continue
        if asset.kind != "directory":
            continue
        bundle = poly_mesh_adapter.inspect(root, asset)
        bundles.append(bundle)
        topologies.append(
            inspect_poly_mesh_topology(root / bundle.source_path, bundle)
        )
    return TaskIngressContext(
        asset_bundles=tuple(bundles),
        poly_mesh_topologies=tuple(topologies),
    )


__all__ = ["TaskIngressContext", "build_task_ingress_context"]
