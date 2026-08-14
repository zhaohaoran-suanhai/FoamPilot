from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from foampilot.evidence import (
    MeshCheckFact,
    RawCommandEvidence,
    RunAssessment,
    RunFacts,
    SolverProgressFact,
    assess_native_run,
)
from foampilot.preprocessing import MeshQualityReport


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _step(
    *,
    stage: str = "solve",
    return_code: int = 0,
) -> RawCommandEvidence:
    return RawCommandEvidence(
        step_id=f"{stage}-step",
        stage=stage,
        executable="icoFoam" if stage == "solve" else "checkMesh",
        argv=("icoFoam",),
        return_code=return_code,
        timed_out=False,
        cancelled=False,
        started_at=NOW,
        finished_at=NOW,
        elapsed_seconds=0,
        stdout_path=f".foampilot/logs/{stage}.stdout.log",
        stderr_path=f".foampilot/logs/{stage}.stderr.log",
        stdout_sha256="a" * 64,
        stderr_sha256="b" * 64,
        execution_backend="bubblewrap",
    )


def _facts(*steps: RawCommandEvidence, normal_end: bool = True) -> RunFacts:
    sources = {
        path: digest
        for step in steps
        for path, digest in (
            (step.stdout_path, step.stdout_sha256),
            (step.stderr_path, step.stderr_sha256),
        )
    }
    return RunFacts(
        run_id="run-assessment",
        attempt=1,
        plan_sha256="c" * 64,
        extractor_identities={"fixture": "1"},
        raw_steps=steps,
        solver_progress=(
            (
                SolverProgressFact(
                    step_id="solve-step",
                    simulation_time=1,
                    completed_normally=normal_end,
                ),
            )
            if any(step.step_id == "solve-step" for step in steps)
            else ()
        ),
        source_sha256=sources,
    )


def test_normal_execution_assessment_has_no_acceptance_thresholds() -> None:
    assessment = assess_native_run(_facts(_step()))

    assert assessment.ok
    assert assessment.failure_layer is None
    assert assessment.reason_codes == ("NORMAL_SOLVER_END",)
    assert "limit" not in assessment.model_dump(mode="json")


def test_failed_step_is_classified_from_typed_stage() -> None:
    assessment = assess_native_run(
        _facts(_step(stage="mesh", return_code=1), normal_end=False)
    )

    assert not assessment.ok
    assert assessment.failure_layer == "MESH_FAILED"
    assert assessment.failed_step_id == "mesh-step"
    assert assessment.reason_codes == ("COMMAND_FAILED",)


def test_missing_normal_solver_end_is_a_solver_failure() -> None:
    assessment = assess_native_run(_facts(_step(), normal_end=False))

    assert not assessment.ok
    assert assessment.failure_layer == "SOLVER_FAILED"
    assert assessment.reason_codes == ("NORMAL_SOLVER_END_MISSING",)


def test_case_only_check_is_not_reported_as_solver_completion() -> None:
    step = _step(stage="check")
    facts = _facts(step).model_copy(
        update={
            "mesh_checks": (
                MeshCheckFact(
                    step_id=step.step_id,
                    executed=True,
                    mesh_ok=True,
                ),
            )
        }
    )

    assessment = assess_native_run(facts)

    assert assessment.ok
    assert assessment.reason_codes == ("CASE_AUTHORING_CHECKS_PASSED",)
    assert "solver" not in assessment.detail.casefold()


def test_case_only_requires_a_successful_mesh_check() -> None:
    step = _step(stage="check")
    facts = _facts(step).model_copy(
        update={
            "mesh_checks": (
                MeshCheckFact(
                    step_id=step.step_id,
                    executed=True,
                    mesh_ok=False,
                ),
            )
        }
    )

    assessment = assess_native_run(facts)

    assert not assessment.ok
    assert assessment.failure_layer == "MESH_FAILED"
    assert assessment.reason_codes == ("MESH_CHECK_NOT_PASSED",)


def test_mesh_quality_failure_is_assessed_without_user_acceptance() -> None:
    quality = MeshQualityReport(
        strategy="provided",
        commands_completed=("checkMesh",),
        mesh_created=True,
        check_mesh_passed=True,
        patches=(),
        failed_requirements=("max_non_orthogonality",),
        warnings=(),
        evidence_files=(),
    )

    assessment = assess_native_run(_facts(_step()), mesh_quality=quality)

    assert not assessment.ok
    assert assessment.failure_layer == "MESH_QUALITY_FAILED"
    assert assessment.reason_codes == ("MESH_INTENT_NOT_SATISFIED",)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "ok": True,
            "failure_layer": "SOLVER_FAILED",
            "reason_codes": ("NORMAL_SOLVER_END",),
            "detail": "contradictory pass",
        },
        {
            "ok": False,
            "reason_codes": ("NORMAL_SOLVER_END_MISSING",),
            "detail": "failure without a layer",
        },
    ],
)
def test_assessment_rejects_contradictory_terminal_truth(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RunAssessment.model_validate(payload)
