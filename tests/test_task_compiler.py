from __future__ import annotations

import pytest

from foampilot.taskbuilder import (
    TaskDraft,
    compile_task_draft,
    validate_task_draft,
)
from tests.test_task_draft_validation import _complete_draft, _fact, _without


def _with_facts(draft: TaskDraft, *facts: dict) -> TaskDraft:
    payload = draft.model_dump(mode="json")
    payload["facts"].extend(facts)
    return TaskDraft.model_validate(payload)


def test_compiler_adds_only_supported_universal_public_checks() -> None:
    compilation = compile_task_draft(
        validate_task_draft(_complete_draft())
    )

    assert compilation.task.schema_version == 2
    assert {item.kind for item in compilation.task.public_checks} >= {
        "mesh_ok",
        "completion",
        "finite_fields",
    }
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


def test_transient_vof_registry_adds_time_bounds_and_conservation() -> None:
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

    checks = {item.kind: item for item in compilation.task.public_checks}
    assert checks["final_time"].parameters["minimum"] == 1.0
    assert checks["bounded_field"].parameters == {
        "field": "alpha.water",
        "minimum": 0.0,
        "maximum": 1.0,
    }
    assert checks["conservation"].parameters["field"] == "alpha.water"


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


def test_explicit_solver_adds_command_executed_check() -> None:
    draft = _with_facts(
        _complete_draft(),
        _fact("physics.solver", "simpleFoam"),
    )

    task = compile_task_draft(validate_task_draft(draft)).task

    check = next(item for item in task.public_checks if item.kind == "command_executed")
    assert check.parameters == {"executable": "simpleFoam"}


def test_same_confirmed_draft_compiles_to_same_task_hash() -> None:
    review = validate_task_draft(_complete_draft())

    first = compile_task_draft(review)
    second = compile_task_draft(review)

    assert first.task_sha256 == second.task_sha256
    assert first.task == second.task


def test_compiler_refuses_blocking_or_unconfirmed_review() -> None:
    review = validate_task_draft(
        _without(_complete_draft(), "materials.fluid")
    )

    with pytest.raises(ValueError, match="TASK_COMPILATION_FAILED"):
        compile_task_draft(review)
