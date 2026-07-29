"""Evaluator-owned public validation for native OpenFOAM execution."""

from __future__ import annotations

import json
import math
from pathlib import Path, PurePosixPath
import re

from foampilot.runtime import (
    PlanRunResult,
    PlanStepResult,
    parse_openfoam_log,
)
from foampilot.tasks import PublicCheck, TaskSpec

from .models import (
    FailureLayer,
    PublicValidationCheck,
    PublicValidationReport,
)


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_MESH_COMMANDS = {
    "blockMesh",
    "checkMesh",
    "snappyHexMesh",
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


def _field_values(
    text: str,
    *,
    operation: str,
    field: str,
) -> list[float]:
    expression = re.compile(
        rf"\b{re.escape(operation)}\s*"
        rf"(?:\(\s*{re.escape(field)}\s*\)"
        rf"|\(\s*\)\s+of\s+{re.escape(field)})"
        rf"\s*=\s*({_NUMBER})"
    )
    return [float(item) for item in expression.findall(text)]


def _step_text(step: PlanStepResult) -> str:
    return "\n".join(
        (
            step.stdout_path.read_text(
                encoding="utf-8",
                errors="replace",
            ),
            step.stderr_path.read_text(
                encoding="utf-8",
                errors="replace",
            ),
        )
    )


def _successful(step: PlanStepResult) -> bool:
    return step.return_code == 0 and not step.timed_out


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
    steps: list[PlanStepResult],
    texts: list[str],
    field_cache: dict[str, dict[str, list[float]]],
) -> PublicValidationCheck:
    kind = expectation.kind
    parameters = expectation.parameters
    combined = "\n".join(texts)
    summaries = [parse_openfoam_log(text) for text in texts]

    if kind == "mesh_ok":
        matched = any(
            _successful(step)
            and "checkMesh" in step.command
            and bool(re.search(r"\bMesh OK\b", text))
            for step, text in zip(steps, texts, strict=True)
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=matched,
            detail=(
                "A successful checkMesh step reports Mesh OK."
                if matched
                else "No successful checkMesh step reports Mesh OK."
            ),
            observed={"mesh_ok_marker": matched},
            limits={"return_code": 0},
        )

    if kind == "command_executed":
        configured = parameters.get("executable")
        executable = str(configured) if isinstance(configured, str) else ""
        matched = any(
            executable in step.command and _successful(step)
            for step in steps
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=bool(executable) and matched,
            detail=(
                f"{executable} executed successfully."
                if executable and matched
                else "The required command did not execute successfully."
            ),
            observed={"executable": executable, "matched": matched},
            limits={"return_code": 0},
        )

    if kind == "completion":
        all_successful = bool(steps) and all(_successful(step) for step in steps)
        solver_completion = any(
            summary.completed and summary.latest_time is not None
            for summary in summaries
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
        times = [
            summary.latest_time
            for summary in summaries
            if summary.latest_time is not None
        ]
        latest = max(times) if times else None
        passed = (
            minimum is not None
            and latest is not None
            and latest + 1e-12 >= minimum
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
            summary.last_cumulative_continuity_error
            for summary in summaries
            if summary.last_cumulative_continuity_error is not None
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
        non_finite = any(summary.non_finite for summary in summaries)
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
        parsed = PurePosixPath(relative)
        safe = (
            bool(relative)
            and not parsed.is_absolute()
            and ".." not in parsed.parts
        )
        output = (case_root / relative).resolve() if safe else case_root
        present = (
            safe
            and output.is_relative_to(case_root)
            and output.is_file()
            and output.stat().st_size > 0
        )
        return PublicValidationCheck(
            name=expectation.name,
            passed=present,
            detail=(
                "Requested output exists and is non-empty."
                if present
                else "Requested case-relative output is missing or empty."
            ),
            observed={"path": relative, "present": present},
        )

    payload = _payload(parameters, case_root)

    if kind == "bounded_field":
        field = parameters.get("field")
        minimum_values = (
            _field_values(combined, operation="min", field=field)
            if isinstance(field, str)
            else []
        )
        maximum_values = (
            _field_values(combined, operation="max", field=field)
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
            _field_values(combined, operation="volIntegrate", field=field)
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


def _failure_layer(step: PlanStepResult, text: str) -> FailureLayer:
    if re.search(r"(?m)^(?:bwrap|prlimit):", text):
        return "ENVIRONMENT_BLOCKED"
    commands = set(step.command)
    if commands & _MESH_COMMANDS:
        return "MESH_FAILED"
    if commands & _INITIALIZATION_COMMANDS:
        return "INITIALIZATION_FAILED"
    if commands & _POSTPROCESS_COMMANDS:
        return "POSTPROCESS_FAILED"
    return "SOLVER_FAILED"


def validate_native_run(
    *,
    task: TaskSpec,
    run_result: PlanRunResult,
    case_root: str | Path,
) -> PublicValidationReport:
    """Apply evaluator-owned checks to observed execution and case files."""

    root = Path(case_root).resolve()
    steps = list(run_result.steps)
    texts = [_step_text(step) for step in steps]
    by_id = {step.step_id: (step, text) for step, text in zip(steps, texts)}
    field_cache: dict[str, dict[str, list[float]]] = {}

    if run_result.failed_step_id is not None:
        failed, text = by_id[run_result.failed_step_id]
        layer = _failure_layer(failed, text)
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
            steps=steps,
            texts=texts,
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
