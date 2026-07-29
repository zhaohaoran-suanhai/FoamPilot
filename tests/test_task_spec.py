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
        "schema_version": 1,
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
        "schema_version: 1\n"
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
