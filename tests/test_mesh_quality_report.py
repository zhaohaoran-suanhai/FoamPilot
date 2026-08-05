from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from foampilot.preprocessing import build_mesh_quality_report
from foampilot.runtime import PlanRunResult, PlanStepResult
from foampilot.tasks import MeshIntent


def _run(
    tmp_path: Path,
    text: str,
    *,
    return_code: int | None = 0,
    failed: bool = False,
) -> PlanRunResult:
    case = tmp_path / "case"
    logs = case / ".foampilot/logs"
    logs.mkdir(parents=True)
    stdout = logs / "check.stdout.log"
    stderr = logs / "check.stderr.log"
    stdout.write_text(text, encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    now = datetime.now(timezone.utc)
    step = PlanStepResult(
        step_id="check-mesh",
        command=["checkMesh"],
        return_code=return_code,
        started_at=now,
        finished_at=now,
        timed_out=False,
        stdout_path=stdout,
        stderr_path=stderr,
    )
    return PlanRunResult(
        case_dir=case,
        steps=[step],
        failed_step_id="check-mesh" if failed else None,
    )


def test_mesh_quality_report_parses_native_check_mesh_observations(
    tmp_path: Path,
) -> None:
    report = build_mesh_quality_report(
        _run(
            tmp_path,
            """
Mesh stats
    points:           441
    faces:            840
    cells:            400
    patches:          4
    Number of regions: 1 (OK).
Mesh non-orthogonality Max: 12.5 average: 2.1
Max skewness = 0.42 OK.
Mesh OK.
""",
        ),
        MeshIntent(
            strategy="blockMesh",
            target_cell_count={"min": 100, "max": 1000},
            quality={
                "require_check_mesh_pass": True,
                "max_non_orthogonality": 70,
                "max_skewness": 4,
            },
        ),
    )

    assert report.mesh_created is True
    assert report.check_mesh_passed is True
    assert report.points == 441
    assert report.faces == 840
    assert report.cells == 400
    assert report.regions == 1
    assert report.max_non_orthogonality == 12.5
    assert report.max_skewness == 0.42
    assert report.failed_requirements == ()
    assert report.passed


def test_mesh_quality_report_preserves_failed_check_and_thresholds(
    tmp_path: Path,
) -> None:
    report = build_mesh_quality_report(
        _run(
            tmp_path,
            """
    points: 20
    faces: 30
    cells: 10
Mesh non-orthogonality Max: 82 average: 12
Max skewness = 6.2
Failed 2 mesh checks.
""",
            failed=True,
        ),
        MeshIntent(
            strategy="snappyHexMesh",
            target_cell_count={"min": 100, "max": 1000},
            quality={
                "require_check_mesh_pass": True,
                "max_non_orthogonality": 70,
                "max_skewness": 4,
            },
        ),
    )

    assert report.check_mesh_passed is False
    assert set(report.failed_requirements) == {
        "check_mesh_pass",
        "minimum_cell_count",
        "maximum_non_orthogonality",
        "maximum_skewness",
    }
    assert not report.passed


def test_mesh_quality_report_marks_required_missing_metrics_unavailable(
    tmp_path: Path,
) -> None:
    report = build_mesh_quality_report(
        _run(tmp_path, "Mesh OK.\n"),
        MeshIntent(
            strategy="provided",
            quality={
                "require_check_mesh_pass": True,
                "max_non_orthogonality": 70,
            },
        ),
    )

    assert report.check_mesh_passed is True
    assert report.max_non_orthogonality is None
    assert report.failed_requirements == (
        "maximum_non_orthogonality_unavailable",
    )
    assert any("non-orthogonality" in item for item in report.warnings)
