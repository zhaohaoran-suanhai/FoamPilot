from __future__ import annotations

import pytest

from foampilot.taskbuilder import (
    TaskDraft,
    compile_task_draft,
    validate_task_draft,
)
from tests.test_task_draft_validation import (
    _complete_draft,
    _fact,
    _provided_mesh_draft,
    _without,
)


def _with_facts(draft: TaskDraft, *facts: dict) -> TaskDraft:
    payload = draft.model_dump(mode="json")
    payload["facts"].extend(facts)
    return TaskDraft.model_validate(payload)


def test_compiler_emits_task_v3_without_legacy_public_checks() -> None:
    compilation = compile_task_draft(
        validate_task_draft(_complete_draft())
    )

    assert compilation.task.schema_version == 3
    assert compilation.task.public_checks == []
    assert compilation.task.openfoam_target.version == "10"
    assert compilation.task.geometry is not None
    assert compilation.task.mesh is not None
    assert compilation.task.mesh.strategy == "auto"
    assert {item.path for item in compilation.assumptions} >= {
        "openfoam.version",
        "resources.max_attempts",
        "resources.max_wall_seconds",
        "resources.max_mpi_ranks",
        "resources.memory_mib",
        "mesh.strategy",
    }


def test_compiler_uses_frozen_product_target_over_extracted_target() -> None:
    payload = _complete_draft().model_dump(mode="json")
    payload["facts"].extend(
        [
            _fact("openfoam.distribution", "openfoam_com"),
            _fact("openfoam.version", "2512"),
        ]
    )
    draft = TaskDraft.model_validate(payload)

    task = compile_task_draft(validate_task_draft(draft)).task

    assert task.openfoam_target.distribution == "foundation"
    assert task.openfoam_target.version == "10"
    assert task.explicit_value("openfoam.distribution") is None
    assert task.explicit_value("openfoam.version") is None


def test_editable_draft_cannot_change_frozen_product_target() -> None:
    payload = _complete_draft().model_dump(mode="json")
    payload["ingress_context"]["target"]["version"] = "13"

    with pytest.raises(ValueError, match="Foundation OpenFOAM 10"):
        TaskDraft.model_validate(payload)


def test_transient_vof_requirements_remain_acceptance_intent_and_facts() -> None:
    draft = _complete_draft()
    payload = draft.model_dump(mode="json")
    for item in payload["facts"]:
        if item["path"] == "physics.regime":
            item["value"] = "transient"
        elif item["path"] == "physics.phase_family":
            item["value"] = "vof"
    payload["facts"].extend(
        [
            _fact("operating.end_time", {"value": 1.0, "unit": "s"}),
            _fact("physics.phase_field", "alpha.water"),
            _fact(
                "initial.phase_fraction",
                {"field": "alpha.water", "minimum": 0.0, "maximum": 1.0},
            ),
            _fact("acceptance.conservation_max", 1.0e-6),
        ]
    )
    compilation = compile_task_draft(
        validate_task_draft(TaskDraft.model_validate(payload))
    )

    assert "solver reaches the declared end time 1.0" in (
        compilation.task.acceptance_intent
    )
    assert compilation.task.explicit_value("acceptance.conservation_max") == 1.0e-6


def test_explicit_pressure_drop_without_tolerance_is_observation_only() -> None:
    draft = _with_facts(
        _complete_draft(),
        _fact(
            "outputs.metrics",
            [{"name": "pressure_drop", "between": ["inlet", "outlet"]}],
        ),
    )

    compilation = compile_task_draft(validate_task_draft(draft))

    assert "pressure_drop observation" in compilation.task.required_outputs
    assert any(
        item.code == "TASK_METRIC_TOLERANCE_MISSING"
        for item in compilation.diagnostics
    )
    assert all(item.kind != "pressure_drop" for item in compilation.task.public_checks)
    assert all("tolerance" not in item.lower() for item in compilation.task.acceptance_requirements)


def test_explicit_solver_remains_a_fact_not_a_legacy_check() -> None:
    draft = _with_facts(
        _complete_draft(),
        _fact("physics.solver", "simpleFoam"),
    )

    task = compile_task_draft(validate_task_draft(draft)).task

    assert task.explicit_value("physics.solver") == "simpleFoam"
    assert task.public_checks == []


def test_taskbuilder_source_does_not_construct_public_checks() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src/foampilot/taskbuilder"
    assert all("PublicCheck" not in path.read_text(encoding="utf-8") for path in root.rglob("*.py"))


def test_same_confirmed_draft_compiles_to_same_task_hash() -> None:
    review = validate_task_draft(_complete_draft())

    first = compile_task_draft(review)
    second = compile_task_draft(review)

    assert first.task_sha256 == second.task_sha256
    assert first.task == second.task


def test_compiler_accepts_missing_design_owned_material() -> None:
    review = validate_task_draft(
        _without(_complete_draft(), "materials.fluid")
    )

    task = compile_task_draft(review).task

    assert task.explicit_value("materials.fluid") is None


def test_compiler_builds_provided_route_without_design_values() -> None:
    task = compile_task_draft(
        validate_task_draft(_provided_mesh_draft())
    ).task

    assert task.openfoam_target.distribution == "foundation"
    assert task.openfoam_target.version == "10"
    assert task.geometry is not None
    assert task.geometry.mode == "openfoam_mesh"
    assert task.geometry.assets[0].format == "openfoam_mesh"
    assert task.mesh is not None
    assert task.mesh.strategy == "provided"
    assert task.explicit_value("physics.solver") is None
    assert task.explicit_value("materials.fluid") is None
    assert task.explicit_value("operating.end_time") is None


def test_transient_task_without_end_time_does_not_emit_none_acceptance() -> None:
    task = compile_task_draft(
        validate_task_draft(_provided_mesh_draft())
    ).task

    assert all("None" not in item for item in task.acceptance_intent)
    assert all(
        "declared end time" not in item for item in task.acceptance_intent
    )


def test_compiler_excludes_unconfirmed_model_inference() -> None:
    payload = _provided_mesh_draft().model_dump(mode="json")
    payload["facts"].append(
        _fact(
            "physics.solver",
            "icoFoam",
            source="model_inference",
            confirmed=False,
        )
    )
    draft = TaskDraft.model_validate(payload)

    task = compile_task_draft(validate_task_draft(draft)).task

    assert task.explicit_value("physics.solver") is None


def test_compiler_composes_confirmed_unit_without_relabeling_mesh_fact() -> None:
    payload = _provided_mesh_draft(length_unit=None).model_dump(mode="json")
    payload["facts"].append(
        _fact(
            "geometry.length_unit",
            "m",
            source="user_confirmation",
            confirmed=True,
        )
    )
    draft = TaskDraft.model_validate(payload)

    task = compile_task_draft(validate_task_draft(draft)).task

    assert task.geometry is not None
    assert task.geometry.length_unit == "m"
    assert task.explicit_value("geometry.length_unit") == "m"
    geometry_fact = next(
        item for item in task.explicit_facts
        if item.field_path == "geometry.input"
    )
    assert geometry_fact.source == "deterministic_rule"
