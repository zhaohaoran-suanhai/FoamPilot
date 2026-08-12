"""Parse bounded native mesh evidence into public quality facts."""

from __future__ import annotations

from pathlib import Path
import re

from foampilot.runtime import PlanRunResult
from foampilot.tasks import MeshIntent

from .models import MeshQualityReport


_LOG_LIMIT_BYTES = 256 * 1024
_MESH_GENERATORS = {
    "blockMesh",
    "snappyHexMesh",
    "gmshToFoam",
    "cfMesh",
}


def _bounded_text(path: Path) -> str:
    payload = path.read_bytes()
    if len(payload) > _LOG_LIMIT_BYTES:
        payload = payload[-_LOG_LIMIT_BYTES:]
    return payload.decode("utf-8", errors="replace")


def _number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return float(match.group(1)) if match is not None else None


def _integer(text: str, name: str) -> int | None:
    value = _number(text, rf"^\s*{re.escape(name)}\s*:\s*(\d+)\b")
    return int(value) if value is not None else None


def _boundary_patches(case_root: Path) -> tuple[str, ...]:
    boundary = case_root / "constant/polyMesh/boundary"
    if not boundary.is_file():
        return ()
    text = _bounded_text(boundary)
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return ()
    body = text[start + 1 : end]
    return tuple(
        dict.fromkeys(
            re.findall(
                r"(?m)^\s*([A-Za-z0-9_.:-]+)\s*\n\s*\{",
                body,
            )
        )
    )


def _evidence_path(path: Path, case_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(case_root).as_posix()
    except ValueError:
        return path.name


def build_mesh_quality_report(
    plan_run: PlanRunResult,
    mesh_intent: MeshIntent | None,
) -> MeshQualityReport:
    """Build observations and evaluate only explicitly declared mesh intent."""

    case_root = plan_run.case_dir.resolve()
    completed = tuple(
        step.step_id
        for step in plan_run.steps
        if step.return_code == 0 and not step.timed_out
    )
    evidence = tuple(
        dict.fromkeys(
            _evidence_path(path, case_root)
            for step in plan_run.steps
            for path in (step.stdout_path, step.stderr_path)
        )
    )
    check_steps = [
        step
        for step in plan_run.steps
        if any(Path(token).name == "checkMesh" for token in step.command)
    ]
    check_text = "\n".join(
        _bounded_text(path)
        for step in check_steps
        for path in (step.stdout_path, step.stderr_path)
        if path.is_file()
    )
    check_passed: bool | None = None
    if check_steps:
        check_passed = any(
            step.return_code == 0 and not step.timed_out
            for step in check_steps
        ) and bool(re.search(r"\bMesh OK\b", check_text))

    cells = _integer(check_text, "cells")
    faces = _integer(check_text, "faces")
    points = _integer(check_text, "points")
    regions_value = _number(
        check_text,
        r"^\s*Number of regions\s*:\s*(\d+)\b",
    )
    non_orthogonality = _number(
        check_text,
        r"Mesh non-orthogonality\s+Max\s*:\s*"
        r"([-+0-9.eE]+)",
    )
    skewness = _number(
        check_text,
        r"Max skewness\s*=\s*([-+0-9.eE]+)",
    )
    negative_volumes_value = _number(
        check_text,
        r"(?:negative volume cells|cells with negative volume)\s*:\s*(\d+)",
    )

    command_names = {
        Path(token).name
        for step in plan_run.steps
        for token in step.command
    }
    successful_names = {
        Path(token).name
        for step in plan_run.steps
        if step.return_code == 0 and not step.timed_out
        for token in step.command
    }
    mesh_created: bool | None
    if (case_root / "constant/polyMesh/points").is_file():
        mesh_created = True
    elif check_passed is True or successful_names & _MESH_GENERATORS:
        mesh_created = True
    elif command_names & (_MESH_GENERATORS | {"checkMesh"}):
        mesh_created = False
    else:
        mesh_created = None

    failed: list[str] = []
    warnings: list[str] = []
    if mesh_intent is not None:
        quality = mesh_intent.quality
        if quality.require_check_mesh_pass and check_passed is not True:
            failed.append("check_mesh_pass")
        cell_range = mesh_intent.target_cell_count
        if cell_range is not None:
            if cells is None:
                failed.append("cell_count_unavailable")
                warnings.append("checkMesh did not report the cell count")
            else:
                if cells < cell_range.min:
                    failed.append("minimum_cell_count")
                if cells > cell_range.max:
                    failed.append("maximum_cell_count")
        if quality.max_non_orthogonality is not None:
            if non_orthogonality is None:
                failed.append("maximum_non_orthogonality_unavailable")
                warnings.append(
                    "checkMesh did not report mesh non-orthogonality"
                )
            elif non_orthogonality > quality.max_non_orthogonality:
                failed.append("maximum_non_orthogonality")
        if quality.max_skewness is not None:
            if skewness is None:
                failed.append("maximum_skewness_unavailable")
                warnings.append("checkMesh did not report mesh skewness")
            elif skewness > quality.max_skewness:
                failed.append("maximum_skewness")

    return MeshQualityReport(
        strategy=(mesh_intent.strategy if mesh_intent is not None else "unspecified"),
        commands_completed=completed,
        mesh_created=mesh_created,
        check_mesh_passed=check_passed,
        cells=cells,
        faces=faces,
        points=points,
        regions=(int(regions_value) if regions_value is not None else None),
        patches=_boundary_patches(case_root),
        max_non_orthogonality=non_orthogonality,
        max_skewness=skewness,
        negative_volume_count=(
            int(negative_volumes_value)
            if negative_volumes_value is not None
            else None
        ),
        failed_requirements=tuple(failed),
        warnings=tuple(dict.fromkeys(warnings)),
        evidence_files=evidence,
    )
