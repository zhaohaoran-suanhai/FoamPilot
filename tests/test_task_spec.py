from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError
import yaml

from foampilot.tasks import (
    TaskSpec,
    load_task_spec,
    stage_public_assets,
)
from foampilot.tasks.legacy import load_legacy_task_spec_from_run
from foampilot.cli.main import _declared_task_assets


POLY_MESH_FIXTURE = Path(__file__).parent / "fixtures/poly_mesh/minimal"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 3,
        "task_id": "side-driven-box",
        "title": "Side-driven enclosure",
        "request_text": "Solve a laminar incompressible side-driven box.",
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
        "acceptance_intent": ["mesh passes checkMesh"],
        "public_assets": [],
        "protected_paths": ["/private/tutorial/cavity"],
        "explicit_facts": [],
    }
    payload.update(overrides)
    return payload


def test_agent_payload_excludes_protected_paths() -> None:
    task = TaskSpec.model_validate(
        _payload(
            explicit_facts=[
                _explicit_fact(
                    "acceptance.legacy_checks.mesh-quality",
                    {
                        "name": "mesh-quality",
                        "kind": "mesh_ok",
                        "parameters": {},
                    },
                )
            ]
        )
    )

    payload = task.agent_payload()

    assert "protected_paths" not in payload
    assert "public_checks" not in payload
    assert "/private/tutorial/cavity" not in str(payload)
    assert "mesh-quality" not in str(payload)


def test_task_rejects_duplicate_explicit_fact_paths() -> None:
    fact = {
        "field_path": "physics.regime",
        "value": "laminar",
        "source": "user_text",
        "impact": "high",
        "evidence": [{"kind": "user_quote", "detail": "laminar"}],
        "confirmed": True,
    }
    with pytest.raises(ValidationError, match="duplicate explicit fact"):
        TaskSpec.model_validate(
            _payload(
                explicit_facts=[fact, fact]
            )
        )


def test_task_rejects_removed_allowed_knowledge_field() -> None:
    with pytest.raises(ValidationError, match="allowed_knowledge"):
        TaskSpec.model_validate(_payload(allowed_knowledge=["legacy.entry"]))


def test_task_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(
        "schema_version: 3\n"
        "task_id: x\n"
        "title: X\n"
        "request_text: Run a case.\n"
        "openfoam_target: {distribution: foundation, version: '10'}\n"
        "resource_budget: {max_attempts: 1, max_wall_seconds: 30, "
        "max_mpi_ranks: 1, memory_mib: 512}\n"
        "required_outputs: [fields]\n"
        "acceptance_intent: [completion]\n"
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
                request_text="Read /private/tutorial/cavity and solve it.",
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

    assert [item.destination for item in staged] == [
        destination / "inputs/geometry.stl"
    ]
    assert staged[0].destination.read_bytes() == b"solid geometry\nendsolid\n"

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


def test_legacy_file_asset_remains_valid() -> None:
    task = TaskSpec.model_validate(
        _payload(
            public_assets=[
                {
                    "path": "geometry/body.stl",
                    "sha256": "a" * 64,
                    "purpose": "public geometry",
                }
            ]
        )
    )

    assert task.public_assets[0].kind == "file"
    assert task.public_assets[0].install_path is None
    assert task.public_assets[0].bundle_manifest_sha256 is None


def test_directory_asset_requires_install_path_and_manifest_hash() -> None:
    asset = TaskSpec.model_validate(
        _payload(
            public_assets=[
                {
                    "path": "mesh/native",
                    "sha256": "1" * 64,
                    "purpose": "provided mesh",
                    "kind": "directory",
                    "install_path": "constant/polyMesh",
                    "bundle_manifest_sha256": "1" * 64,
                }
            ]
        )
    ).public_assets[0]

    assert asset.kind == "directory"
    assert asset.install_path == "constant/polyMesh"

    base = asset.model_dump(mode="json")
    for missing in ("install_path", "bundle_manifest_sha256"):
        invalid = dict(base)
        invalid[missing] = None
        with pytest.raises(ValidationError, match="directory asset requires"):
            TaskSpec.model_validate(
                _payload(public_assets=[invalid])
            )


def test_directory_asset_digest_and_install_path_are_strict() -> None:
    base = {
        "path": "mesh/native",
        "sha256": "1" * 64,
        "purpose": "provided mesh",
        "kind": "directory",
        "install_path": "constant/polyMesh",
        "bundle_manifest_sha256": "2" * 64,
    }
    with pytest.raises(ValidationError, match="must equal"):
        TaskSpec.model_validate(_payload(public_assets=[base]))

    with pytest.raises(ValidationError, match="safe relative"):
        TaskSpec.model_validate(
            _payload(
                public_assets=[
                    {
                        **base,
                        "sha256": "2" * 64,
                        "install_path": "../constant/polyMesh",
                    }
                ]
            )
        )


def test_directory_asset_is_staged_atomically_at_its_install_path(
    tmp_path: Path,
) -> None:
    public_root = tmp_path / "public"
    source = public_root / "mesh/native"
    shutil.copytree(POLY_MESH_FIXTURE, source)
    request = public_root / "request.txt"
    request.write_text("provided mesh", encoding="utf-8")
    declaration = _declared_task_assets(
        request,
        [],
        public_root,
        directory_paths=[Path("mesh/native")],
        install_paths=[Path("constant/polyMesh")],
    )[0]
    task = TaskSpec.model_validate(
        _payload(public_assets=[declaration.model_dump(mode="json")])
    )

    staged = stage_public_assets(task, public_root, tmp_path / "case")

    assert len(staged) == 1
    assert staged[0].bundle.manifest_sha256 == declaration.sha256
    assert staged[0].destination == tmp_path / "case/constant/polyMesh"
    assert (staged[0].destination / "cellZones").is_file()


def test_openfoam_mesh_input_requires_an_atomic_directory_asset() -> None:
    file_asset = {
        "path": "mesh/constant/polyMesh/points",
        "sha256": "a" * 64,
        "purpose": "incomplete mesh file",
    }

    with pytest.raises(ValidationError, match="directory asset"):
        TaskSpec.model_validate(
            _payload(
                public_assets=[file_asset],
                explicit_facts=_geometry_mesh_facts(
                    {
                        "mode": "openfoam_mesh",
                        "dimensionality": "three_d",
                        "description": "native mesh",
                        "length_unit": "m",
                        "assets": [
                            {
                                "path": file_asset["path"],
                                "format": "openfoam_mesh",
                                "role": "poly_mesh_bundle",
                            }
                        ],
                    },
                    {"strategy": "provided"},
                ),
            )
        )


def test_file_asset_rejects_directory_only_fields() -> None:
    with pytest.raises(ValidationError, match="file asset must not"):
        TaskSpec.model_validate(
            _payload(
                public_assets=[
                    {
                        "path": "geometry/body.stl",
                        "sha256": "a" * 64,
                        "purpose": "public geometry",
                        "kind": "file",
                        "install_path": "constant/polyMesh",
                    }
                ]
            )
        )


def _explicit_fact(field_path: str, value: object) -> dict[str, object]:
    return {
        "field_path": field_path,
        "value": value,
        "source": "user_text",
        "impact": "high",
        "evidence": [
            {"kind": "user_quote", "detail": f"explicit {field_path}"}
        ],
        "confirmed": True,
    }


def _geometry_mesh_facts(
    geometry: dict[str, object],
    mesh: dict[str, object],
) -> list[dict[str, object]]:
    return [
        _explicit_fact("geometry.input", geometry),
        _explicit_fact("mesh.intent", mesh),
    ]


def test_v3_accepts_parametric_surface_gmsh_and_provided_mesh_inputs() -> None:
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
            explicit_facts=_geometry_mesh_facts(
                {
                    **common_geometry,
                    "mode": "parametric",
                    "length_unit": "m",
                    "assets": [],
                    "parameters": {
                        "channel_length": {"value": 1.0, "unit": "m"}
                    },
                },
                {"strategy": "blockMesh"},
            ),
        )
    )
    surface = TaskSpec.model_validate(
        _payload(
            public_assets=[asset],
            explicit_facts=_geometry_mesh_facts(
                {
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
                {"strategy": "snappyHexMesh"},
            ),
        )
    )
    gmsh = TaskSpec.model_validate(
        _payload(
            public_assets=[{**asset, "path": "geometry/body.geo"}],
            explicit_facts=_geometry_mesh_facts(
                {
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
                {"strategy": "gmsh"},
            ),
        )
    )
    provided = TaskSpec.model_validate(
        _payload(
            public_assets=[
                {
                    "path": "mesh/native",
                    "sha256": "a" * 64,
                    "purpose": "native mesh bundle",
                    "kind": "directory",
                    "install_path": "constant/polyMesh",
                    "bundle_manifest_sha256": "a" * 64,
                }
            ],
            explicit_facts=_geometry_mesh_facts(
                {
                    **common_geometry,
                    "mode": "openfoam_mesh",
                    "length_unit": "m",
                    "assets": [
                        {
                            "path": "mesh/native",
                            "format": "openfoam_mesh",
                            "role": "poly_mesh_bundle",
                        }
                    ],
                    "parameters": {},
                },
                {"strategy": "provided"},
            ),
        )
    )

    assert parametric.geometry is not None
    assert surface.geometry.assets[0].format == "stl"
    assert gmsh.mesh is not None and gmsh.mesh.strategy == "gmsh"
    assert provided.geometry.mode == "openfoam_mesh"


def test_v3_rejects_ambiguous_units_duplicate_roles_and_undeclared_assets() -> None:
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
            _payload(
                explicit_facts=_geometry_mesh_facts(
                    surface,
                    {"strategy": "snappyHexMesh"},
                )
            )
        )
    with pytest.raises(ValidationError, match="duplicate patch role"):
        TaskSpec.model_validate(
            _payload(
                explicit_facts=_geometry_mesh_facts(
                    {
                        **surface,
                        "length_unit": "m",
                        "patch_roles": [
                            {"name": "inlet", "role": "inlet"},
                            {"name": "inlet", "role": "outlet"},
                        ],
                    },
                    {"strategy": "snappyHexMesh"},
                ),
            )
        )
    with pytest.raises(ValidationError, match="declared public asset"):
        TaskSpec.model_validate(
            _payload(
                explicit_facts=_geometry_mesh_facts(
                    {**surface, "length_unit": "m"},
                    {"strategy": "snappyHexMesh"},
                ),
            )
        )


def test_v3_rejects_openfoam_mesh_without_provided_strategy() -> None:
    geometry = {
        "mode": "openfoam_mesh",
        "dimensionality": "two_d",
        "description": "native mesh",
        "length_unit": "m",
        "assets": [
            {
                "path": "mesh/native",
                "format": "openfoam_mesh",
                "role": "volume_mesh",
            }
        ],
    }
    asset = {
        "path": "mesh/native",
        "sha256": "a" * 64,
        "purpose": "native mesh",
        "kind": "directory",
        "install_path": "constant/polyMesh",
        "bundle_manifest_sha256": "a" * 64,
    }

    with pytest.raises(ValidationError, match="incompatible"):
        TaskSpec.model_validate(
            _payload(
                public_assets=[asset],
                explicit_facts=_geometry_mesh_facts(
                    geometry,
                    {"strategy": "auto"},
                ),
            )
        )


def test_v1_is_not_accepted_by_the_canonical_task_model() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        TaskSpec.model_validate(_payload(schema_version=1))


def test_authoring_loader_rejects_task_v2(tmp_path: Path) -> None:
    path = tmp_path / "legacy-task.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "task_id": "side-driven-box",
                "title": "Side-driven enclosure",
                "prompt": "Solve a case.",
                "openfoam_target": _payload()["openfoam_target"],
                "resource_budget": _payload()["resource_budget"],
                "required_outputs": ["velocity"],
                "acceptance_requirements": ["completion"],
                "public_checks": [
                    {"name": "completion", "kind": "completion"}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="schema_version"):
        load_task_spec(path)


def test_legacy_v2_is_only_readable_through_run_adapter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "task_id": "side-driven-box",
                "title": "Side-driven enclosure",
                "prompt": "Solve a case.",
                "openfoam_target": _payload()["openfoam_target"],
                "resource_budget": _payload()["resource_budget"],
                "required_outputs": ["velocity"],
                "acceptance_requirements": ["completion"],
                "public_checks": [
                    {"name": "completion", "kind": "completion"}
                ],
            }
        ),
        encoding="utf-8",
    )

    legacy = load_legacy_task_spec_from_run(path)

    assert legacy.schema_version == 2
    assert legacy.task_id == "side-driven-box"


def test_every_repository_authoring_task_uses_v3_loader() -> None:
    project = Path(__file__).resolve().parents[1]
    paths = [
        *sorted((project / "examples/tasks").glob("*.yaml")),
        *sorted((project / "examples/qualification").glob("*.yaml")),
        *sorted(
            (project / "src/foampilot/qualification/data/tasks").glob(
                "*.yaml"
            )
        ),
    ]

    assert paths
    tasks = [load_task_spec(path) for path in paths]

    assert all(task.schema_version == 3 for task in tasks)
    assert len(tasks) == len(paths)
