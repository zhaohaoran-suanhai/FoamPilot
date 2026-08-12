"""Evaluator-owned public validation for native OpenFOAM execution."""

from __future__ import annotations

import json
import math
from pathlib import Path, PurePosixPath

from foampilot.evidence import (
    RawCommandEvidence,
    RunFacts,
)
from foampilot.tasks import PublicCheck, TaskSpec

from .models import (
    FailureLayer,
    PublicValidationCheck,
    PublicValidationReport,
)


_MESH_COMMANDS = {
    "blockMesh",
    "checkMesh",
    "surfaceCheck",
    "surfaceFeatureExtract",
    "snappyHexMesh",
    "gmsh",
    "gmshToFoam",
    "cfMesh",
}
_INITIALIZATION_COMMANDS = {
    "setFields",
    "setExprFields",
    "potentialFoam",
}
_POSTPROCESS_COMMANDS = {
    "postProcess",
    "foamCalc",
    "foamToVTK",
    "sample",
}
_MPI_LAUNCHERS = {"mpirun", "mpiexec", "orterun"}


def _command_executable(command: list[str]) -> str:
    if not command:
        return ""
    executable = Path(command[0]).name
    if (
        executable in _MPI_LAUNCHERS
        and len(command) >= 4
        and command[1] in {"-n", "-np"}
    ):
        return Path(command[3]).name
    return executable


def _field_values(
    run_facts: RunFacts,
    *,
    operation: str,
    field: str,
) -> list[float]:
    return [
        item.value
        for item in run_facts.field_operations
        if item.operation == operation and item.field == field
    ]


def _successful(step: RawCommandEvidence) -> bool:
    return (
        step.return_code == 0
        and not step.timed_out
        and not step.cancelled
    )


def _nonempty_file(path: Path, root: Path) -> bool:
    return (
        path.is_relative_to(root)
        and path.is_file()
        and path.stat().st_size > 0
    )


def _requested_output_path(
    case_root: Path,
    relative: str,
) -> Path | None:
    parsed = PurePosixPath(relative)
    safe = (
        bool(relative)
        and not parsed.is_absolute()
        and ".." not in parsed.parts
    )
    if not safe:
        return None
    exact = (case_root / relative).resolve()
    if _nonempty_file(exact, case_root):
        return exact
    if len(parsed.parts) < 2:
        return None
    try:
        requested_time = float(parsed.parts[0])
    except ValueError:
        return None
    candidates: list[tuple[float, Path]] = []
    for time_dir in case_root.iterdir():
        if not time_dir.is_dir():
            continue
        try:
            observed_time = float(time_dir.name)
        except ValueError:
            continue
        if not math.isclose(
            observed_time,
            requested_time,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            continue
        candidate = time_dir.joinpath(*parsed.parts[1:]).resolve()
        if _nonempty_file(candidate, case_root):
            candidates.append(
                (abs(observed_time - requested_time), candidate)
            )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _check_mesh_diagnostics(
    run_facts: RunFacts,
) -> str | None:
    diagnostics = list(
        dict.fromkeys(
            item
            for check in reversed(run_facts.mesh_checks)
            for item in check.diagnostics
        )
    )[:3]
    if not diagnostics:
        return None
    return "; ".join(diagnostics)[:1000]


def _payload(parameters: dict[str, object], case_root: Path) -> dict:
    configured = parameters.get("evidence_file")
    relative = str(configured) if isinstance(configured, str) else ""
    parsed = PurePosixPath(relative)
    safe = (
        bool(relative)
        and not parsed.is_absolute()
        and ".." not in parsed.parts
    )
    path = (case_root / relative).resolve() if safe else case_root
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _written_scalar_field_observations(
    case_root: Path,
    field: str,
) -> dict[str, list[float]]:
    import numpy as np
    import pyvista as pv

    marker = case_root / "foampilot-evaluator.foam"
    remove_marker = not marker.exists()
    marker.touch(exist_ok=True)
    try:
        reader = pv.OpenFOAMReader(str(marker))
        times = sorted({float(value) for value in reader.time_values})
        minima: list[float] = []
        maxima: list[float] = []
        integrals: list[float] = []
        for time_value in times:
            reader.set_active_time_value(time_value)
            output = reader.read()
            if "internalMesh" not in output.keys():
                return {}
            mesh = output["internalMesh"]
            if field not in mesh.cell_data:
                return {}
            values = np.asarray(mesh.cell_data[field], dtype=float)
            if values.ndim != 1 or values.size == 0:
                return {}
            volumes = np.asarray(
                mesh.compute_cell_sizes(
                    length=False,
                    area=False,
                    volume=True,
                ).cell_data["Volume"],
                dtype=float,
            )
            if volumes.shape != values.shape:
                return {}
            minima.append(float(np.min(values)))
            maxima.append(float(np.max(values)))
            integrals.append(float(np.sum(values * volumes)))
        return {
            "minima": minima,
            "maxima": maxima,
            "integrals": integrals,
        }
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {}
    finally:
        if remove_marker:
            marker.unlink(missing_ok=True)


def _written_field_observations(
    case_root: Path,
    field: str,
    cache: dict[str, dict[str, list[float]]],
) -> dict[str, list[float]]:
    if field not in cache:
        cache[field] = _written_scalar_field_observations(case_root, field)
    return cache[field]


def _check(
    expectation: PublicCheck,
    *,
    case_root: Path,
    run_facts: RunFacts,
    steps: list[RawCommandEvidence],
    reused_executables: set[str],
    field_cache: dict[str, dict[str, list[float]]],
) -> PublicValidationCheck:
    kind = expectation.kind
    parameters = expectation.parameters

    if kind == "mesh_ok":
        matched = any(check.mesh_ok is True for check in run_facts.mesh_checks)
        diagnostic = (
            None if matched else _check_mesh_diagnostics(run_facts)
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=matched,
            detail=(
                "A successful mesh-check step reports the canonical pass fact."
                if matched
                else (
                    "No successful mesh-check step reports the canonical pass fact. "
                    f"checkMesh diagnostics: {diagnostic}"
                    if diagnostic
                    else "No successful mesh-check step reports the canonical pass fact."
                )
            ),
            observed={
                "mesh_ok_marker": matched,
                "diagnostic": diagnostic,
            },
            limits={"return_code": 0},
        )

    if kind == "command_executed":
        configured = parameters.get("executable")
        executable = str(configured) if isinstance(configured, str) else ""
        executed = any(
            executable == step.executable
            and _successful(step)
            for step in steps
        )
        reused = executable in reused_executables
        matched = executed or reused
        return PublicValidationCheck(
            name=expectation.name,
            passed=bool(executable) and matched,
            detail=(
                f"{executable} executed successfully."
                if executable and matched
                else "The required command did not execute successfully."
            ),
            observed={
                "executable": executable,
                "matched": matched,
                "evidence_source": (
                    "executed_step"
                    if executed
                    else "reused_step" if reused else "missing"
                ),
            },
            limits={"return_code": 0},
        )

    if kind == "completion":
        all_successful = bool(steps) and all(_successful(step) for step in steps)
        solver_completion = any(
            progress.completed_normally is True
            for progress in run_facts.solver_progress
        )
        passed = all_successful and solver_completion
        return PublicValidationCheck(
            name=expectation.name,
            passed=passed,
            detail=(
                "All commands succeeded and a solver reports normal completion."
                if passed
                else "A command failed or no timed solver log ends normally."
            ),
            observed={
                "all_commands_successful": all_successful,
                "normal_solver_end": solver_completion,
            },
            limits={"normal_solver_end": True},
        )

    if kind == "final_time":
        configured = parameters.get("minimum")
        minimum = (
            float(configured)
            if isinstance(configured, (int, float))
            else None
        )
        times = [item.simulation_time for item in run_facts.solver_progress]
        latest = max(times) if times else None
        passed = (
            minimum is not None
            and latest is not None
            and (
                latest >= minimum
                or math.isclose(
                    latest,
                    minimum,
                    rel_tol=1e-9,
                    abs_tol=1e-12,
                )
            )
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=passed,
            detail=(
                "Requested final time is reached."
                if passed
                else "The requested final-time threshold is not reached."
            ),
            observed={"latest_time": latest},
            limits={"minimum": minimum},
        )

    if kind == "continuity":
        configured = parameters.get("max_abs_cumulative")
        limit = (
            float(configured)
            if isinstance(configured, (int, float))
            else None
        )
        values = [
            item.cumulative
            for item in run_facts.continuity
            if item.cumulative is not None
        ]
        observed = values[-1] if values else None
        passed = (
            observed is not None
            and limit is not None
            and math.isfinite(observed)
            and abs(observed) <= limit
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=passed,
            detail=(
                "Cumulative continuity error is within the public limit."
                if passed
                else "Continuity evidence is missing or exceeds the limit."
            ),
            observed={"cumulative": observed},
            limits={"max_abs_cumulative": limit},
        )

    if kind == "finite_fields":
        non_finite = any(
            item.code
            in {"NON_FINITE_VALUE", "FLOATING_POINT_EXCEPTION"}
            for item in run_facts.native_errors
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=bool(steps) and not non_finite,
            detail=(
                "No non-finite marker is present."
                if steps and not non_finite
                else "Execution contains non-finite evidence or no logs."
            ),
            observed={"non_finite": non_finite},
            limits={"non_finite": False},
        )

    if kind == "requested_output":
        configured = parameters.get("path")
        relative = str(configured) if isinstance(configured, str) else ""
        output = _requested_output_path(case_root, relative)
        present = output is not None
        return PublicValidationCheck(
            name=expectation.name,
            passed=present,
            detail=(
                "Requested output exists and is non-empty."
                if present
                else "Requested case-relative output is missing or empty."
            ),
            observed={
                "path": relative,
                "present": present,
                "resolved_path": (
                    output.relative_to(case_root).as_posix()
                    if output is not None
                    else None
                ),
            },
        )

    payload = _payload(parameters, case_root)

    if kind == "bounded_field":
        field = parameters.get("field")
        minimum_values = (
            _field_values(run_facts, operation="min", field=field)
            if isinstance(field, str)
            else []
        )
        maximum_values = (
            _field_values(run_facts, operation="max", field=field)
            if isinstance(field, str)
            else []
        )
        evidence_source = "solver_log"
        if (
            isinstance(field, str)
            and (not minimum_values or not maximum_values)
        ):
            written = _written_field_observations(
                case_root,
                field,
                field_cache,
            )
            minimum_values = written.get("minima", [])
            maximum_values = written.get("maxima", [])
            evidence_source = "written_fields"
        observed_minimum = payload.get(
            "minimum",
            min(minimum_values) if minimum_values else None,
        )
        observed_maximum = payload.get(
            "maximum",
            max(maximum_values) if maximum_values else None,
        )
        if "minimum" in payload or "maximum" in payload:
            evidence_source = "evidence_file"
        elif not minimum_values or not maximum_values:
            evidence_source = "missing"
        configured_minimum = parameters.get("minimum")
        configured_maximum = parameters.get("maximum")
        numeric = all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in (
                observed_minimum,
                observed_maximum,
                configured_minimum,
                configured_maximum,
            )
        )
        passed = numeric and (
            float(observed_minimum) >= float(configured_minimum)
            and float(observed_maximum) <= float(configured_maximum)
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=passed,
            detail=(
                "Field bounds satisfy the public limits."
                if passed
                else "Bounded-field evidence is missing or outside limits."
            ),
            observed={
                "minimum": observed_minimum,
                "maximum": observed_maximum,
                "evidence_source": evidence_source,
            },
            limits={
                "minimum": configured_minimum,
                "maximum": configured_maximum,
            },
        )

    if kind == "conservation":
        field = parameters.get("field")
        samples = (
            _field_values(run_facts, operation="volIntegrate", field=field)
            if isinstance(field, str)
            else []
        )
        evidence_source = "solver_log"
        if isinstance(field, str) and len(samples) < 2:
            written = _written_field_observations(
                case_root,
                field,
                field_cache,
            )
            samples = written.get("integrals", [])
            evidence_source = "written_fields"
        initial = samples[0] if samples else None
        final = samples[-1] if samples else None
        calculated = (
            abs(final - initial) / abs(initial)
            if initial not in (None, 0.0) and final is not None
            else None
        )
        observed = payload.get("normalized_error", calculated)
        if "normalized_error" in payload:
            evidence_source = "evidence_file"
        elif len(samples) < 2:
            evidence_source = "missing"
        configured = parameters.get("maximum_normalized_error")
        limit = (
            float(configured)
            if isinstance(configured, (int, float))
            else None
        )
        passed = (
            isinstance(observed, (int, float))
            and limit is not None
            and math.isfinite(float(observed))
            and float(observed) <= limit
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=passed,
            detail=(
                "Conservation error satisfies the public limit."
                if passed
                else "Conservation evidence is missing or exceeds the limit."
            ),
            observed={
                "normalized_error": observed,
                "initial": initial,
                "final": final,
                "sample_count": len(samples),
                "evidence_source": evidence_source,
            },
            limits={"maximum_normalized_error": limit},
        )

    return PublicValidationCheck(
        name=expectation.name,
        passed=False,
        detail=f"Unsupported evaluator check kind: {kind}.",
        observed={"kind": kind},
    )


def _failure_layer(
    step: RawCommandEvidence,
    run_facts: RunFacts,
) -> FailureLayer:
    if any(
        item.step_id == step.step_id
        and item.code == "EXECUTION_BACKEND_ERROR"
        for item in run_facts.native_errors
    ):
        return "ENVIRONMENT_BLOCKED"
    if step.stage == "mesh" or step.stage == "check":
        return "MESH_FAILED"
    if step.stage == "initialize":
        return "INITIALIZATION_FAILED"
    if step.stage == "postprocess":
        return "POSTPROCESS_FAILED"
    executable = step.executable
    if executable in _MESH_COMMANDS:
        return "MESH_FAILED"
    if executable in _INITIALIZATION_COMMANDS:
        return "INITIALIZATION_FAILED"
    if executable in _POSTPROCESS_COMMANDS:
        return "POSTPROCESS_FAILED"
    return "SOLVER_FAILED"


def validate_native_run(
    task: TaskSpec,
    run_facts: RunFacts,
    case_root: str | Path,
) -> PublicValidationReport:
    """Apply evaluator-owned checks to observed execution and case files."""

    root = Path(case_root).resolve()
    steps = list(run_facts.raw_steps)
    reused_executables = {
        step.executable for step in run_facts.reused_steps
    }
    field_cache: dict[str, dict[str, list[float]]] = {}

    failed = next((step for step in steps if not _successful(step)), None)
    if failed is not None:
        layer = _failure_layer(failed, run_facts)
        return PublicValidationReport(
            checks=[
                PublicValidationCheck(
                    name=f"step:{failed.step_id}",
                    passed=False,
                    detail=f"Command step {failed.step_id} failed.",
                    observed={
                        "return_code": failed.return_code,
                        "timed_out": failed.timed_out,
                    },
                    limits={"return_code": 0, "timed_out": False},
                )
            ],
            failure_layer=layer,
            failed_step_id=failed.step_id,
        )

    checks = [
        _check(
            expectation,
            case_root=root,
            run_facts=run_facts,
            steps=steps,
            reused_executables=reused_executables,
            field_cache=field_cache,
        )
        for expectation in task.public_checks
    ]
    return PublicValidationReport(
        checks=checks,
        failure_layer=(
            None
            if checks and all(check.passed for check in checks)
            else "PUBLIC_VALIDATION_FAILED"
        ),
    )
