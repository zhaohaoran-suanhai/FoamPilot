"""Parse bounded native mesh evidence into public quality facts."""

from __future__ import annotations

from pathlib import Path
import re

from foampilot.evidence import RunFacts
from foampilot.tasks import MeshIntent

from .models import MeshQualityReport


_LOG_LIMIT_BYTES = 256 * 1024


def _bounded_text(path: Path) -> str:
    payload = path.read_bytes()
    if len(payload) > _LOG_LIMIT_BYTES:
        payload = payload[-_LOG_LIMIT_BYTES:]
    return payload.decode("utf-8", errors="replace")


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


def mesh_quality_from_run_facts(
    run_facts: RunFacts,
    mesh_intent: MeshIntent | None,
    case_root: Path,
) -> MeshQualityReport:
    """Project canonical run observations to the existing quality contract."""

    completed = tuple(
        step.step_id
        for step in run_facts.raw_steps
        if step.return_code == 0 and not step.timed_out and not step.cancelled
    )
    check = run_facts.mesh_checks[-1] if run_facts.mesh_checks else None
    failed: list[str] = []
    warnings: list[str] = []
    if mesh_intent is not None:
        quality = mesh_intent.quality
        if quality.require_check_mesh_pass and (
            check is None or check.mesh_ok is not True
        ):
            failed.append("check_mesh_pass")
        cell_range = mesh_intent.target_cell_count
        cells = check.cells if check is not None else None
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
            observed = (
                check.max_non_orthogonality if check is not None else None
            )
            if observed is None:
                failed.append("maximum_non_orthogonality_unavailable")
                warnings.append(
                    "checkMesh did not report mesh non-orthogonality"
                )
            elif observed > quality.max_non_orthogonality:
                failed.append("maximum_non_orthogonality")
        if quality.max_skewness is not None:
            observed = check.max_skewness if check is not None else None
            if observed is None:
                failed.append("maximum_skewness_unavailable")
                warnings.append("checkMesh did not report mesh skewness")
            elif observed > quality.max_skewness:
                failed.append("maximum_skewness")

    mesh_created = (
        True
        if (case_root / "constant/polyMesh/points").is_file()
        else (
            True
            if check is not None and check.mesh_ok is True
            else (False if run_facts.mesh_checks else None)
        )
    )
    evidence_files = tuple(
        dict.fromkeys(
            path
            for step in run_facts.raw_steps
            for path in (step.stdout_path, step.stderr_path)
        )
    )
    return MeshQualityReport(
        strategy=(
            mesh_intent.strategy if mesh_intent is not None else "unspecified"
        ),
        commands_completed=completed,
        mesh_created=mesh_created,
        check_mesh_passed=(check.mesh_ok if check is not None else None),
        cells=(check.cells if check is not None else None),
        faces=(check.faces if check is not None else None),
        points=(check.points if check is not None else None),
        regions=(check.regions if check is not None else None),
        patches=_boundary_patches(case_root),
        max_non_orthogonality=(
            check.max_non_orthogonality if check is not None else None
        ),
        max_skewness=(check.max_skewness if check is not None else None),
        negative_volume_count=(
            check.negative_volume_cells if check is not None else None
        ),
        failed_requirements=tuple(failed),
        warnings=tuple(dict.fromkeys(warnings)),
        evidence_files=evidence_files,
    )
