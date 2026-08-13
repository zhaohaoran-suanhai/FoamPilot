from __future__ import annotations

from pathlib import Path

from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.taskbuilder import TaskDraft, validate_task_draft


def _fact(path: str, value, *, source="user_text", confirmed=True):
    return {
        "path": path,
        "value": value,
        "source": source,
        "evidence": "request excerpt",
        "impact": "high",
        "confirmed": confirmed,
    }


def _complete_draft(**updates) -> TaskDraft:
    facts = [
        _fact("physics.regime", "steady"),
        _fact("physics.compressibility", "incompressible"),
        _fact("physics.phase_family", "single_phase"),
        _fact("physics.energy", "disabled"),
        _fact("physics.turbulence", "laminar"),
        _fact(
            "geometry",
            {
                "mode": "parametric",
                "dimensionality": "two_d",
                "description": "1 m by 0.1 m channel",
                "length_unit": "m",
                "parameters": {
                    "length": {"value": 1.0, "unit": "m"},
                    "height": {"value": 0.1, "unit": "m"},
                },
                "patch_roles": [
                    {"name": "inlet", "role": "inlet"},
                    {"name": "outlet", "role": "outlet"},
                    {"name": "walls", "role": "wall"},
                ],
            },
        ),
        _fact(
            "materials.fluid",
            {"kinematic_viscosity": {"value": 1e-6, "unit": "m2/s"}},
        ),
        _fact(
            "boundaries",
            [
                {"role": "inlet", "velocity": {"value": 1, "unit": "m/s"}},
                {"role": "outlet", "pressure": {"value": 0, "unit": "m2/s2"}},
                {"role": "wall", "condition": "no-slip"},
            ],
        ),
        _fact("outputs.required", ["velocity field", "pressure field"]),
    ]
    payload = {
        "draft_id": "draft-complete-channel",
        "request_text": "Solve a steady laminar incompressible channel flow.",
        "facts": facts,
        "assets": [],
        "protected_paths": ["/private/taskbuilder-target"],
        "status": "confirmed",
    }
    payload.update(updates)
    return TaskDraft.model_validate(payload)


def _without(draft: TaskDraft, *paths: str) -> TaskDraft:
    payload = draft.model_dump(mode="json")
    payload["facts"] = [
        item for item in payload["facts"] if item["path"] not in paths
    ]
    return TaskDraft.model_validate(payload)


def _environment(*executables: str) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=Path("/opt/openfoam10"),
        tutorial_root=Path("/private/tutorials"),
        workspace_root=Path("/runs"),
        workspace_writable=True,
        commands=[
            CommandFact(name=name, path=Path("/opt/openfoam10/bin") / name)
            for name in executables
        ],
        mpi_launcher=None,
        gmsh=None,
        max_mpi_ranks=4,
    )


def _provided_mesh_draft(*, length_unit: str | None = "m") -> TaskDraft:
    geometry = {
        "mode": "openfoam_mesh",
        "dimensionality": "two_d",
        "description": "supplied native OpenFOAM mesh",
        "length_unit": length_unit,
        "assets": [
            {
                "path": "mesh/native",
                "format": "openfoam_mesh",
                "role": "volume_mesh",
            }
        ],
    }
    return TaskDraft.model_validate(
        {
            "draft_id": "draft-provided-mesh",
            "request_text": (
                "Solve a transient incompressible laminar flow on the "
                "supplied mesh and choose suitable engineering parameters."
            ),
            "facts": [
                _fact("geometry", geometry),
                _fact("mesh", {"strategy": "provided"}),
                _fact("physics.regime", "transient"),
                _fact("physics.compressibility", "incompressible"),
                _fact("physics.phase_family", "single_phase"),
                _fact("physics.turbulence", "laminar"),
            ],
            "assets": [
                {
                    "path": "mesh/native",
                    "sha256": "d" * 64,
                    "purpose": "native mesh",
                    "kind": "directory",
                    "install_path": "constant/polyMesh",
                    "bundle_manifest_sha256": "d" * 64,
                }
            ],
            "status": "confirmed",
        }
    )


def test_complete_confirmed_request_compiles_with_only_advisories() -> None:
    review = validate_task_draft(_complete_draft())

    assert review.can_compile
    assert not {
        item.severity for item in review.issues
    } & {"blocking", "confirmable"}
    assert any(item.severity == "advisory" for item in review.issues)


def test_task_draft_v1_migrates_to_v2_with_default_ingress_context() -> None:
    payload = _complete_draft().model_dump(mode="json")
    payload["schema_version"] = 1
    payload.pop("ingress_context")

    migrated = TaskDraft.model_validate(payload)

    assert migrated.schema_version == 2
    assert migrated.ingress_context.target.version == "10"


def test_missing_geometry_unit_is_blocking() -> None:
    payload = _complete_draft().model_dump(mode="json")
    geometry = next(
        item for item in payload["facts"] if item["path"] == "geometry"
    )
    del geometry["value"]["length_unit"]

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert any(item.code == "TASK_UNIT_AMBIGUOUS" for item in review.issues)
    assert not review.can_compile


def test_geometry_and_mesh_must_match_compiler_contract_before_compile() -> None:
    payload = _complete_draft().model_dump(mode="json")
    geometry = next(
        item for item in payload["facts"] if item["path"] == "geometry"
    )
    geometry["value"]["patch_roles"] = {"top": "wall"}
    payload["facts"].append(
        _fact(
            "mesh",
            {
                "strategy": "blockMesh",
                "quality": {"distribution": "uniform"},
            },
        )
    )

    review = validate_task_draft(TaskDraft.model_validate(payload))

    blocking_paths = {
        item.field_path for item in review.issues if item.severity == "blocking"
    }
    assert {"geometry", "mesh"} <= blocking_paths
    assert not review.can_compile


def test_missing_fluid_material_and_boundaries_are_deferred_to_design() -> None:
    draft = _without(
        _complete_draft(),
        "materials.fluid",
        "boundaries",
    )

    review = validate_task_draft(draft)

    blocking_paths = {
        item.field_path for item in review.issues if item.severity == "blocking"
    }
    assert not {"materials.fluid", "boundaries"} & blocking_paths


def test_design_owned_values_do_not_block_task_ingress() -> None:
    draft = _provided_mesh_draft()

    review = validate_task_draft(draft)

    assert review.can_compile is True
    assert not {
        "physics.solver",
        "materials.fluid",
        "boundaries",
        "operating.end_time",
        "operating.time_step",
    } & {
        item.field_path
        for item in review.issues
        if item.severity in {"blocking", "confirmable"}
    }


def test_provided_mesh_without_length_unit_remains_blocked() -> None:
    review = validate_task_draft(_provided_mesh_draft(length_unit=None))

    assert review.can_compile is False
    assert [
        item.field_path
        for item in review.issues
        if item.severity == "blocking"
    ] == ["geometry.length_unit"]


def test_explicit_unit_question_does_not_duplicate_specific_unit_issue() -> None:
    payload = _provided_mesh_draft(length_unit=None).model_dump(mode="json")
    payload["unresolved_questions"] = [
        {
            "question_id": "q_geometry_length_unit",
            "path": "geometry.length_unit",
            "kind": "blocking",
            "prompt_zh": "该网格的坐标长度单位是什么？",
            "reason_zh": "物理尺度不能由坐标猜测。",
        }
    ]
    payload["status"] = "incomplete"

    review = validate_task_draft(TaskDraft.model_validate(payload))

    blocking = [item for item in review.issues if item.severity == "blocking"]
    assert [(item.code, item.field_path) for item in blocking] == [
        ("TASK_UNIT_AMBIGUOUS", "geometry.length_unit")
    ]


def test_separately_confirmed_length_unit_completes_atomic_mesh_geometry() -> None:
    payload = _provided_mesh_draft(length_unit=None).model_dump(mode="json")
    payload["facts"].append(
        _fact(
            "geometry.length_unit",
            "m",
            source="user_confirmation",
            confirmed=True,
        )
    )

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert review.can_compile is True


def test_missing_regime_is_deferred_to_intent_interpretation() -> None:
    review = validate_task_draft(
        _without(_complete_draft(), "physics.regime")
    )

    assert not any(item.field_path == "physics.regime" for item in review.issues)


def test_geometry_asset_must_be_declared() -> None:
    payload = _complete_draft().model_dump(mode="json")
    geometry = next(
        item for item in payload["facts"] if item["path"] == "geometry"
    )
    geometry["value"] = {
        "mode": "surface",
        "dimensionality": "three_d",
        "description": "public body surface",
        "length_unit": "mm",
        "assets": [
            {"path": "geometry/body.stl", "format": "stl", "role": "body"}
        ],
    }

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert any(item.code == "TASK_ASSET_UNRESOLVED" for item in review.issues)


def test_validation_blocks_incompatible_geometry_and_mesh_strategy() -> None:
    payload = _complete_draft().model_dump(mode="json")
    geometry = next(
        item for item in payload["facts"] if item["path"] == "geometry"
    )
    geometry["value"] = {
        "mode": "surface",
        "dimensionality": "three_d",
        "description": "public body surface",
        "length_unit": "mm",
        "assets": [
            {"path": "geometry/body.stl", "format": "stl", "role": "body"}
        ],
    }
    payload["assets"] = [
        {
            "path": "geometry/body.stl",
            "sha256": "d" * 64,
            "purpose": "body",
        }
    ]
    payload["facts"].append(_fact("mesh", {"strategy": "provided"}))

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert any(
        item.severity == "blocking" and item.field_path == "mesh"
        for item in review.issues
    )


def test_openfoam_mesh_requires_explicit_provided_strategy() -> None:
    payload = _provided_mesh_draft().model_dump(mode="json")
    payload["facts"] = [
        item for item in payload["facts"] if item["path"] != "mesh"
    ]

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert review.can_compile is False
    assert any(
        item.severity == "blocking" and item.field_path == "mesh"
        for item in review.issues
    )


def test_taskbuilder_defers_solver_capability_to_later_risk_gate() -> None:
    payload = _complete_draft().model_dump(mode="json")
    payload["facts"].append(_fact("physics.solver", "madeUpFoam"))
    review = validate_task_draft(
        TaskDraft.model_validate(payload),
        environment=_environment("icoFoam", "simpleFoam"),
    )

    assert not any(item.code == "TASK_CAPABILITY_UNAVAILABLE" for item in review.issues)
    assert review.can_compile is True


def test_unconfirmed_model_physics_is_audit_only_at_task_ingress() -> None:
    payload = _complete_draft().model_dump(mode="json")
    regime = next(
        item for item in payload["facts"] if item["path"] == "physics.regime"
    )
    regime.update(source="model_inference", confirmed=False)
    payload["status"] = "ready_for_confirmation"

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert not any(item.field_path == "physics.regime" for item in review.issues)


def test_high_impact_model_assumption_is_audit_only_at_task_ingress() -> None:
    payload = _complete_draft().model_dump(mode="json")
    payload["assumptions"] = [
        {
            "assumption_id": "guessed-viscosity",
            "path": "materials.fluid.kinematic_viscosity",
            "value": {"value": 1e-6, "unit": "m2/s"},
            "source": "model_inference",
            "impact": "high",
            "explanation_zh": "模型按常见流体猜测。",
        }
    ]

    review = validate_task_draft(TaskDraft.model_validate(payload))

    assert not any(
        item.field_path == "materials.fluid.kinematic_viscosity"
        for item in review.issues
    )
    assert review.can_compile
