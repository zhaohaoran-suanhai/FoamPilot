from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from foampilot.tasks import (
    TaskSpec,
    load_task_spec,
    stage_public_assets,
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2,
        "task_id": "side-driven-box",
        "title": "Side-driven enclosure",
        "prompt": "Solve a laminar incompressible side-driven box.",
        "openfoam_target": {
            "distribution": "foundation",
            "version": "10",
        },
        "resource_budget": {
            "max_attempts": 2,
            "max_wall_seconds": 120,
            "max_mpi_ranks": 2,
            "memory_mib": 2048,
        },
        "required_outputs": ["velocity field", "pressure field"],
        "acceptance_requirements": ["mesh passes checkMesh"],
        "public_checks": [
            {
                "name": "mesh-quality",
                "kind": "mesh_ok",
                "parameters": {},
            }
        ],
        "public_assets": [],
        "protected_paths": ["/private/tutorial/cavity"],
    }
    payload.update(overrides)
    return payload


def test_agent_payload_excludes_protected_paths() -> None:
    task = TaskSpec.model_validate(_payload())

    payload = task.agent_payload()

    assert "protected_paths" not in payload
    assert "public_checks" not in payload
    assert "/private/tutorial/cavity" not in str(payload)
    assert "mesh-quality" not in str(payload)


def test_task_rejects_duplicate_public_check_names() -> None:
    with pytest.raises(ValidationError, match="duplicate public check"):
        TaskSpec.model_validate(
            _payload(
                public_checks=[
                    {"name": "completion", "kind": "completion"},
                    {"name": "completion", "kind": "final_time"},
                ]
            )
        )


def test_task_rejects_removed_allowed_knowledge_field() -> None:
    with pytest.raises(ValidationError, match="allowed_knowledge"):
        TaskSpec.model_validate(_payload(allowed_knowledge=["legacy.entry"]))


def test_task_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(
        "schema_version: 2\n"
        "task_id: x\n"
        "title: X\n"
        "prompt: Run a case.\n"
        "openfoam_target: {distribution: foundation, version: '10'}\n"
        "resource_budget: {max_attempts: 1, max_wall_seconds: 30, "
        "max_mpi_ranks: 1, memory_mib: 512}\n"
        "required_outputs: [fields]\n"
        "acceptance_requirements: [completion]\n"
        "public_checks:\n"
        "  - {name: completion, kind: completion, parameters: {}}\n"
        "public_assets: []\n"
        "protected_paths: []\n"
        "unexpected: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unexpected"):
        load_task_spec(path)


def test_task_rejects_duplicate_requirements_and_unsafe_paths() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        TaskSpec.model_validate(
            _payload(required_outputs=["velocity", "velocity"])
        )

    with pytest.raises(ValidationError, match="absolute"):
        TaskSpec.model_validate(_payload(protected_paths=["relative/golden"]))

    with pytest.raises(ValidationError, match="safe relative"):
        TaskSpec.model_validate(
            _payload(
                public_assets=[
                    {
                        "path": "../private/geometry.stl",
                        "sha256": "a" * 64,
                        "purpose": "geometry",
                    }
                ]
            )
        )

    with pytest.raises(ValidationError, match="agent-visible"):
        TaskSpec.model_validate(
            _payload(
                prompt="Read /private/tutorial/cavity and solve it.",
            )
        )


def test_public_asset_is_hash_verified_before_staging(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    asset = source / "inputs/geometry.stl"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"solid geometry\nendsolid\n")
    digest = sha256(asset.read_bytes()).hexdigest()
    task = TaskSpec.model_validate(
        _payload(
            public_assets=[
                {
                    "path": "inputs/geometry.stl",
                    "sha256": digest,
                    "purpose": "public geometry",
                }
            ]
        )
    )
    destination = tmp_path / "case"

    staged = stage_public_assets(task, source, destination)

    assert staged == [destination / "inputs/geometry.stl"]
    assert staged[0].read_bytes() == b"solid geometry\nendsolid\n"

    asset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA256"):
        stage_public_assets(task, source, tmp_path / "other-case")


def test_public_asset_rejects_internal_foampilot_namespace() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        TaskSpec.model_validate(
            _payload(
                public_assets=[
                    {
                        "path": ".foampilot/host-home/.OpenFOAM/10/prefs.sh",
                        "sha256": "a" * 64,
                        "purpose": "host startup override",
                    }
                ]
            )
        )


def test_v2_accepts_parametric_surface_gmsh_and_provided_mesh_inputs() -> None:
    asset = {
        "path": "geometry/body.stl",
        "sha256": "a" * 64,
        "purpose": "public geometry",
    }
    common_geometry = {
        "dimensionality": "three_d",
        "description": "公开几何",
        "patch_roles": [
            {"name": "inletSurface", "role": "inlet"},
            {"name": "bodySurface", "role": "wall"},
        ],
        "region_roles": [{"name": "fluid", "role": "fluid"}],
    }
    parametric = TaskSpec.model_validate(
        _payload(
            schema_version=2,
            geometry={
                **common_geometry,
                "mode": "parametric",
                "length_unit": "m",
                "assets": [],
                "parameters": {
                    "channel_length": {"value": 1.0, "unit": "m"}
                },
            },
            mesh={"strategy": "blockMesh"},
        )
    )
    surface = TaskSpec.model_validate(
        _payload(
            schema_version=2,
            public_assets=[asset],
            geometry={
                **common_geometry,
                "mode": "surface",
                "length_unit": "mm",
                "assets": [
                    {
                        "path": "geometry/body.stl",
                        "format": "stl",
                        "role": "closed_body_surface",
                    }
                ],
                "parameters": {},
            },
            mesh={"strategy": "snappyHexMesh"},
        )
    )
    gmsh = TaskSpec.model_validate(
        _payload(
            schema_version=2,
            public_assets=[{**asset, "path": "geometry/body.geo"}],
            geometry={
                **common_geometry,
                "mode": "gmsh",
                "length_unit": "cm",
                "assets": [
                    {
                        "path": "geometry/body.geo",
                        "format": "geo",
                        "role": "gmsh_geometry",
                    }
                ],
                "parameters": {},
            },
            mesh={"strategy": "gmsh"},
        )
    )
    provided = TaskSpec.model_validate(
        _payload(
            schema_version=2,
            public_assets=[
                {**asset, "path": "mesh/constant/polyMesh/points"}
            ],
            geometry={
                **common_geometry,
                "mode": "openfoam_mesh",
                "length_unit": "m",
                "assets": [
                    {
                        "path": "mesh/constant/polyMesh/points",
                        "format": "openfoam_mesh",
                        "role": "poly_mesh_file",
                    }
                ],
                "parameters": {},
            },
            mesh={"strategy": "provided"},
        )
    )

    assert parametric.geometry is not None
    assert surface.geometry.assets[0].format == "stl"
    assert gmsh.mesh is not None and gmsh.mesh.strategy == "gmsh"
    assert provided.geometry.mode == "openfoam_mesh"


def test_v2_rejects_ambiguous_units_duplicate_roles_and_undeclared_assets() -> None:
    surface = {
        "mode": "surface",
        "dimensionality": "three_d",
        "description": "Surface body",
        "assets": [
            {
                "path": "geometry/body.stl",
                "format": "stl",
                "role": "closed_body_surface",
            }
        ],
        "parameters": {},
        "patch_roles": [],
        "region_roles": [],
    }
    with pytest.raises(ValidationError, match="length_unit"):
        TaskSpec.model_validate(
            _payload(schema_version=2, geometry=surface)
        )
    with pytest.raises(ValidationError, match="duplicate patch role"):
        TaskSpec.model_validate(
            _payload(
                schema_version=2,
                geometry={
                    **surface,
                    "length_unit": "m",
                    "patch_roles": [
                        {"name": "inlet", "role": "inlet"},
                        {"name": "inlet", "role": "outlet"},
                    ],
                },
            )
        )
    with pytest.raises(ValidationError, match="declared public asset"):
        TaskSpec.model_validate(
            _payload(
                schema_version=2,
                geometry={**surface, "length_unit": "m"},
            )
        )


def test_v1_is_not_accepted_by_the_canonical_task_model() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        TaskSpec.model_validate(_payload(schema_version=1))
