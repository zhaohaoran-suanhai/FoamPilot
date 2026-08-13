from __future__ import annotations

import json

import pytest

from foampilot.models import InMemoryModelTraceSink
from foampilot.taskbuilder import extract_task_draft
from foampilot.tasks import PublicAsset
from tests.support.taskbuilder import (
    RecordingExtractionGateway,
    file_ingress_context as _file_ingress_context,
    task_extraction_budget as _budget,
)


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
