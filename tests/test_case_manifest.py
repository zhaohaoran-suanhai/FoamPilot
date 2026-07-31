from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseModels,
    CasePatch,
    CaseRegion,
)
from foampilot.plans import ExecutionPlan, GeneratedFile, NativeCommand


def _single_manifest() -> CaseManifest:
    return CaseManifest(
        solver_executable="icoFoam",
        solver_family="incompressible-laminar",
        regime="transient",
        physics_family="fluid",
        mesh_family="blockMesh",
        dimensionality="2d",
        regions=[
            CaseRegion(
                name="default",
                kind="fluid",
                path_prefix="",
            )
        ],
        fields=[
            CaseField(
                name="U",
                region="default",
                path="0/U",
                role="velocity",
                created_by="author",
            ),
            CaseField(
                name="p",
                region="default",
                path="0/p",
                role="kinematic_pressure",
                created_by="author",
            ),
        ],
        patches=[
            CasePatch(
                name="walls",
                region="default",
                mesh_type="wall",
            )
        ],
        models=CaseModels(
            transport="Newtonian",
        ),
    )


def test_execution_plan_v3_has_region_manifest_and_stage_only_on_command():
    plan = ExecutionPlan(
        schema_version=3,
        manifest=_single_manifest(),
        files=[
            GeneratedFile(
                path="system/controlDict",
                content="application icoFoam;",
            )
        ],
        commands=[
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="icoFoam",
                timeout_seconds=60,
            )
        ],
    )
    payload = plan.model_dump(mode="json")

    assert payload["schema_version"] == 3
    assert payload["manifest"]["regions"] == [
        {
            "name": "default",
            "kind": "fluid",
            "path_prefix": "",
        }
    ]
    assert payload["commands"][0]["stage"] == "solve"
    assert "command_stages" not in payload["manifest"]
    assert "command_stages" not in payload


def test_manifest_expresses_fluid_and_solid_regions_for_cht():
    manifest = CaseManifest(
        solver_executable="chtMultiRegionFoam",
        solver_family="conjugate-heat-transfer",
        regime="transient",
        physics_family="conjugate_heat_transfer",
        mesh_family="splitMeshRegions",
        dimensionality="2d",
        regions=[
            CaseRegion(name="fluid", kind="fluid", path_prefix="fluid"),
            CaseRegion(name="solid", kind="solid", path_prefix="solid"),
        ],
        fields=[
            CaseField(
                name="T",
                region="fluid",
                path="0/fluid/T",
                role="temperature",
                created_by="author",
            ),
            CaseField(
                name="T",
                region="solid",
                path="0/solid/T",
                role="temperature",
                created_by="author",
            ),
        ],
        patches=[
            CasePatch(
                name="fluid_to_solid",
                region="fluid",
                mesh_type="mappedWall",
            ),
            CasePatch(
                name="solid_to_fluid",
                region="solid",
                mesh_type="mappedWall",
            ),
        ],
        models=CaseModels(
            turbulence="kOmegaSST",
            thermophysical="fluidThermo+solidThermo",
        ),
    )

    assert {region.name for region in manifest.regions} == {"fluid", "solid"}
    assert {(field.region, field.name) for field in manifest.fields} == {
        ("fluid", "T"),
        ("solid", "T"),
    }


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (
            "regions",
            [
                {"name": "default", "kind": "fluid", "path_prefix": ""},
                {"name": "default", "kind": "solid", "path_prefix": "solid"},
            ],
        ),
        (
            "fields",
            [
                {
                    "name": "U",
                    "region": "default",
                    "path": "0/U",
                    "role": "velocity",
                    "created_by": "author",
                },
                {
                    "name": "U",
                    "region": "default",
                    "path": "0/U-copy",
                    "role": "velocity",
                    "created_by": "author",
                },
            ],
        ),
        (
            "patches",
            [
                {
                    "name": "walls",
                    "region": "default",
                    "mesh_type": "wall",
                },
                {
                    "name": "walls",
                    "region": "default",
                    "mesh_type": "patch",
                },
            ],
        ),
    ],
)
def test_manifest_rejects_duplicate_region_scoped_identities(
    field: str,
    replacement: list[dict[str, str]],
):
    payload = _single_manifest().model_dump(mode="json")
    payload[field] = replacement

    with pytest.raises(ValidationError):
        CaseManifest.model_validate(payload)


def test_manifest_rejects_field_or_patch_that_references_unknown_region():
    field_payload = _single_manifest().model_dump(mode="json")
    field_payload["fields"][0]["region"] = "missing"
    patch_payload = _single_manifest().model_dump(mode="json")
    patch_payload["patches"][0]["region"] = "missing"

    with pytest.raises(ValidationError, match="unknown region"):
        CaseManifest.model_validate(field_payload)
    with pytest.raises(ValidationError, match="unknown region"):
        CaseManifest.model_validate(patch_payload)


def test_canonical_execution_plan_rejects_v2_and_manifest_stage_mapping():
    payload = {
        "schema_version": 2,
        "files": [
            {
                "path": "system/controlDict",
                "content": "application icoFoam;",
            }
        ],
        "commands": [
            {
                "step_id": "solve",
                "executable": "icoFoam",
                "args": [],
                "mpi_ranks": 1,
                "timeout_seconds": 60,
            }
        ],
    }
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(payload)

    plan_payload = {
        "schema_version": 3,
        "manifest": {
            **_single_manifest().model_dump(mode="json"),
            "command_stages": {"solve": "solve"},
        },
        "files": payload["files"],
        "commands": [
            {
                **payload["commands"][0],
                "stage": "solve",
            }
        ],
    }
    with pytest.raises(ValidationError, match="command_stages"):
        ExecutionPlan.model_validate(plan_payload)
