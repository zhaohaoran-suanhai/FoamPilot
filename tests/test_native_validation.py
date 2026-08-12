from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyvista as pv
import pytest

from foampilot.evidence import OpenFOAM10EvidenceExtractor, RunFacts
from foampilot.plans import NativeCommand
from foampilot.runtime import PlanRunResult, PlanStepResult, ReusedStepResult
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload
from foampilot.validation.native import validate_native_run
from tests.test_execution_plan import valid_plan


def _task(
    *,
    checks: list[dict[str, object]] | None = None,
) -> TaskSpec:
    return TaskSpec.model_validate(
        canonical_task_payload({
            "schema_version": 2,
            "task_id": "native-validation",
            "title": "Native validation",
            "prompt": "Solve a transient two-fluid case.",
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 2,
                "max_wall_seconds": 120,
                "max_mpi_ranks": 1,
                "memory_mib": 1024,
            },
            "required_outputs": ["velocity"],
            "acceptance_requirements": ["completion"],
            "public_checks": checks
            or [
                {"name": "mesh", "kind": "mesh_ok", "parameters": {}},
                {
                    "name": "initialized",
                    "kind": "command_executed",
                    "parameters": {"executable": "setFields"},
                },
                {
                    "name": "completion",
                    "kind": "completion",
                    "parameters": {},
                },
                {
                    "name": "final-time",
                    "kind": "final_time",
                    "parameters": {"minimum": 1.0},
                },
                {
                    "name": "continuity",
                    "kind": "continuity",
                    "parameters": {"max_abs_cumulative": 1e-5},
                },
                {
                    "name": "finite",
                    "kind": "finite_fields",
                    "parameters": {},
                },
                {
                    "name": "alpha-bounds",
                    "kind": "bounded_field",
                    "parameters": {
                        "field": "alpha.water",
                        "minimum": -1e-8,
                        "maximum": 1.00000001,
                    },
                },
                {
                    "name": "phase-volume",
                    "kind": "conservation",
                    "parameters": {
                        "field": "alpha.water",
                        "maximum_normalized_error": 0.01,
                    },
                },
                {
                    "name": "velocity-output",
                    "kind": "requested_output",
                    "parameters": {"path": "1/U"},
                },
            ],
            "public_assets": [],
            "protected_paths": [],
        })
    )


def _step(
    root: Path,
    *,
    step_id: str,
    executable: str,
    return_code: int,
    stdout: str,
    timed_out: bool = False,
) -> PlanStepResult:
    now = datetime.now(timezone.utc)
    stdout_path = root / f"{step_id}.stdout.log"
    stderr_path = root / f"{step_id}.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    return PlanStepResult(
        step_id=step_id,
        command=[executable],
        return_code=return_code,
        started_at=now,
        finished_at=now,
        elapsed_seconds=0.0,
        timed_out=timed_out,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def _successful_result(root: Path, *, final_time: float = 1.0) -> PlanRunResult:
    mesh = _step(
        root,
        step_id="mesh",
        executable="checkMesh",
        return_code=0,
        stdout="Mesh OK.\nEnd\n",
    )
    initialize = _step(
        root,
        step_id="initialize",
        executable="setFields",
        return_code=0,
        stdout="Setting field values\nEnd\n",
    )
    solve = _step(
        root,
        step_id="solve",
        executable="interFoam",
        return_code=0,
        stdout=(
            f"Time = {final_time}\n"
            "time step continuity errors : sum local = 1e-8, "
            "global = 0, cumulative = 2e-8\n"
            "min() of alpha.water = -2e-10 at location (0 0 0)\n"
            "max() of alpha.water = 1.000000001 at location (0 0 0)\n"
            "volIntegrate() of alpha.water = 0.0028800\n"
            "volIntegrate() of alpha.water = 0.0028795\n"
            "End\n"
        ),
    )
    return PlanRunResult(
        case_dir=root,
        steps=[mesh, initialize, solve],
    )


def _facts(result: PlanRunResult) -> RunFacts:
    commands = []
    for step in result.steps:
        executable = Path(step.command[0]).name
        stage = (
            "check"
            if executable == "checkMesh"
            else "initialize"
            if executable == "setFields"
            else "solve"
        )
        commands.append(
            NativeCommand(
                step_id=step.step_id,
                stage=stage,
                executable=executable,
                timeout_seconds=30,
            )
        )
    plan = valid_plan().model_copy(update={"commands": commands})
    return OpenFOAM10EvidenceExtractor().extract(
        result,
        plan,
        result.case_dir,
    )


def test_task_owned_checks_validate_all_gate_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "1/U"
    output.parent.mkdir()
    output.write_text("velocity", encoding="utf-8")

    report = validate_native_run(
        _task(),
        _facts(_successful_result(tmp_path)),
        tmp_path,
    )

    assert report.passed
    assert {check.name for check in report.checks} == {
        "mesh",
        "initialized",
        "completion",
        "final-time",
        "continuity",
        "finite",
        "alpha-bounds",
        "phase-volume",
        "velocity-output",
    }
    conservation = next(
        check for check in report.checks if check.name == "phase-volume"
    )
    assert conservation.observed["sample_count"] == 2


def test_command_check_accepts_audited_reused_step(
    tmp_path: Path,
) -> None:
    result = _successful_result(tmp_path)
    result.steps = [result.steps[0], result.steps[2]]
    result.reused_steps = [
        ReusedStepResult(
            step_id="initialize",
            stage="initialize",
            executable="setFields",
            source_kind="parent_attempt",
            source_id="attempt-01",
            reason_codes=["REPAIR_DEPENDENCY_UNCHANGED"],
        )
    ]

    report = validate_native_run(
        _task(
            checks=[
                {
                    "name": "initialized",
                    "kind": "command_executed",
                    "parameters": {"executable": "setFields"},
                }
            ]
        ),
        _facts(result),
        tmp_path,
    )

    assert report.passed
    assert report.checks[0].observed["evidence_source"] == "reused_step"


def test_validation_does_not_reopen_native_logs(tmp_path: Path) -> None:
    facts = _facts(_successful_result(tmp_path))
    for step in facts.raw_steps:
        (tmp_path / step.stdout_path).unlink()
        (tmp_path / step.stderr_path).unlink()

    report = validate_native_run(
        _task(
            checks=[
                {"name": "completion", "kind": "completion", "parameters": {}},
                {
                    "name": "final-time",
                    "kind": "final_time",
                    "parameters": {"minimum": 1.0},
                },
            ]
        ),
        facts,
        tmp_path,
    )

    assert report.passed


def test_failed_step_classification_uses_run_facts(tmp_path: Path) -> None:
    failed = _step(
        tmp_path,
        step_id="solve",
        executable="interFoam",
        return_code=1,
        stdout="FOAM FATAL ERROR\n",
    )

    report = validate_native_run(
        _task(),
        _facts(
            PlanRunResult(
                case_dir=tmp_path,
                steps=[failed],
                failed_step_id="solve",
            )
        ),
        tmp_path,
    )

    assert report.failure_layer == "SOLVER_FAILED"


def test_failed_mesh_check_exposes_bounded_checkmesh_diagnostics(
    tmp_path: Path,
) -> None:
    mesh = _step(
        tmp_path,
        step_id="mesh",
        executable="checkMesh",
        return_code=0,
        stdout=(
            "Checking geometry...\n"
            "***Total number of faces on empty patches is not divisible "
            "by the number of cells in the mesh.\n"
            "Failed 1 mesh checks.\n"
            "End\n"
        ),
    )

    report = validate_native_run(
        _task(
            checks=[
                {"name": "mesh", "kind": "mesh_ok", "parameters": {}},
            ]
        ),
        _facts(PlanRunResult(case_dir=tmp_path, steps=[mesh])),
        tmp_path,
    )

    check = report.checks[0]
    assert not check.passed
    assert "empty patches" in check.detail
    assert "Failed 1 mesh checks" in check.detail
    assert len(check.detail) <= 1200


def test_validation_matches_canonical_executable_paths(tmp_path: Path) -> None:
    (tmp_path / "1").mkdir()
    (tmp_path / "1/U").write_text("velocity\n", encoding="utf-8")
    result = _successful_result(tmp_path)
    result.steps[0].command = ["/opt/OpenFOAM-10/bin/checkMesh"]
    result.steps[1].command = ["/opt/OpenFOAM-10/bin/setFields"]
    result.steps[2].command = ["/opt/OpenFOAM-10/bin/interFoam"]

    report = validate_native_run(
        _task(),
        _facts(result),
        tmp_path,
    )

    assert report.passed


def test_field_checks_fall_back_to_written_openfoam_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "1/U"
    output.parent.mkdir()
    output.write_text("velocity", encoding="utf-8")
    run_result = _successful_result(tmp_path)
    run_result.steps[-1].stdout_path.write_text(
        "Time = 1\n"
        "time step continuity errors : sum local = 1e-8, "
        "global = 0, cumulative = 2e-8\n"
        "End\n",
        encoding="utf-8",
    )

    class FakeMesh:
        def __init__(self, values: list[float]) -> None:
            self.cell_data = {
                "alpha.water": np.asarray(values, dtype=float),
            }

        def compute_cell_sizes(
            self,
            *,
            length: bool,
            area: bool,
            volume: bool,
        ) -> SimpleNamespace:
            assert (length, area, volume) == (False, False, True)
            return SimpleNamespace(
                cell_data={"Volume": np.asarray([1.0, 1.0])}
            )

    class FakeReader:
        time_values = (0.0, 1.0)

        def __init__(self, marker: str) -> None:
            assert marker.endswith("foampilot-evaluator.foam")
            self.time_value = 0.0

        def set_active_time_value(self, value: float) -> None:
            self.time_value = value

        def read(self) -> dict[str, FakeMesh]:
            values = (
                [1.0, 0.0]
                if self.time_value == 0.0
                else [0.99, 0.005]
            )
            return {"internalMesh": FakeMesh(values)}

    monkeypatch.setattr(pv, "OpenFOAMReader", FakeReader)

    report = validate_native_run(
        _task(),
        _facts(run_result),
        tmp_path,
    )

    assert report.passed
    checks = {check.name: check for check in report.checks}
    assert checks["alpha-bounds"].observed == {
        "minimum": 0.0,
        "maximum": 1.0,
        "evidence_source": "written_fields",
    }
    assert checks["phase-volume"].observed == {
        "normalized_error": pytest.approx(0.005),
        "initial": 1.0,
        "final": 0.995,
        "sample_count": 2,
        "evidence_source": "written_fields",
    }
    assert not (tmp_path / "foampilot-evaluator.foam").exists()


def test_unknown_evaluator_kind_fails_without_invalidating_plan(
    tmp_path: Path,
) -> None:
    report = validate_native_run(
        _task(
            checks=[
                {
                    "name": "future-check",
                    "kind": "future_metric",
                    "parameters": {},
                }
            ]
        ),
        _facts(_successful_result(tmp_path)),
        tmp_path,
    )

    assert report.failure_layer == "PUBLIC_VALIDATION_FAILED"
    assert report.checks[0].passed is False
    assert "Unsupported evaluator check" in report.checks[0].detail


def test_failed_setfields_is_classified_as_initialization(
    tmp_path: Path,
) -> None:
    failed = _step(
        tmp_path,
        step_id="initialize",
        executable="setFields",
        return_code=2,
        stdout="FOAM FATAL ERROR\n",
    )

    report = validate_native_run(
        _task(),
        _facts(
            PlanRunResult(
                case_dir=tmp_path,
                steps=[failed],
                failed_step_id="initialize",
            )
        ),
        tmp_path,
    )

    assert report.failure_layer == "INITIALIZATION_FAILED"
    assert report.failed_step_id == "initialize"


def test_requested_output_rejects_path_escape(tmp_path: Path) -> None:
    report = validate_native_run(
        _task(
            checks=[
                {
                    "name": "escaped",
                    "kind": "requested_output",
                    "parameters": {"path": "../private/U"},
                }
            ]
        ),
        _facts(_successful_result(tmp_path)),
        tmp_path,
    )

    assert not report.passed
    assert report.checks[0].observed["present"] is False


def test_numeric_time_checks_accept_roundoff_equivalent_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "9.99999999996/D"
    output.parent.mkdir()
    output.write_text("displacement", encoding="utf-8")
    solve = _step(
        tmp_path,
        step_id="solve-solid",
        executable="solidEquilibriumDisplacementFoam",
        return_code=0,
        stdout="Iteration: 9.99999999996\nEnd\n",
    )
    report = validate_native_run(
        _task(
            checks=[
                {
                    "name": "completion",
                    "kind": "completion",
                    "parameters": {},
                },
                {
                    "name": "final-time",
                    "kind": "final_time",
                    "parameters": {"minimum": 10.0},
                },
                {
                    "name": "displacement",
                    "kind": "requested_output",
                    "parameters": {"path": "10/D"},
                },
            ]
        ),
        _facts(PlanRunResult(case_dir=tmp_path, steps=[solve])),
        tmp_path,
    )

    assert report.passed
    output_check = next(
        check for check in report.checks if check.name == "displacement"
    )
    assert output_check.observed["resolved_path"] == "9.99999999996/D"
