from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from foampilot.evidence import (
    ContinuityFact,
    CourantFact,
    MeshCheckFact,
    NativeErrorFact,
    RawCommandEvidence,
    ResidualFact,
    RunFacts,
    SolverProgressFact,
)


_SHA = "a" * 64
_START = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _raw_step(step_id: str = "solve") -> RawCommandEvidence:
    return RawCommandEvidence(
        step_id=step_id,
        stage="solve",
        executable="pisoFoam",
        argv=("pisoFoam",),
        return_code=0,
        started_at=_START,
        finished_at=_START + timedelta(seconds=2),
        elapsed_seconds=1.5,
        timed_out=False,
        cancelled=False,
        stdout_path="attempt-01/logs/solve.stdout.log",
        stderr_path="attempt-01/logs/solve.stderr.log",
        stdout_sha256=_SHA,
        stderr_sha256=_SHA,
        execution_backend="host",
    )


def _defaults() -> dict[str, object]:
    return {
        "run_id": "run-20260813T000000000000Z-a1b2c3d4",
        "attempt": 1,
        "plan_sha256": _SHA,
        "extractor_identities": {"foundation-10": "1.0.0/protocol-1"},
        "raw_steps": (_raw_step(),),
        "source_sha256": {
            "attempt-01/logs/solve.stdout.log": _SHA,
            "attempt-01/logs/solve.stderr.log": _SHA,
        },
    }


def test_run_facts_reject_duplicate_step_ids() -> None:
    step = _raw_step()
    with pytest.raises(ValidationError, match="raw step ids"):
        RunFacts(**{**_defaults(), "raw_steps": (step, step)})


def test_raw_command_evidence_requires_coherent_monotonic_timing() -> None:
    with pytest.raises(ValidationError, match="finished_at"):
        _raw_step().model_copy(
            update={"finished_at": _START - timedelta(seconds=1)}
        ).__class__.model_validate(
            {
                **_raw_step().model_dump(),
                "finished_at": _START - timedelta(seconds=1),
            }
        )
    with pytest.raises(ValidationError):
        RawCommandEvidence.model_validate(
            {**_raw_step().model_dump(), "elapsed_seconds": -0.01}
        )


def test_raw_command_evidence_accepts_legacy_elapsed_absence() -> None:
    step = RawCommandEvidence.model_validate(
        {**_raw_step().model_dump(), "elapsed_seconds": None}
    )
    assert step.elapsed_seconds is None


def test_fact_numbers_must_be_finite_and_physically_bounded() -> None:
    with pytest.raises(ValidationError):
        ResidualFact(
            step_id="solve",
            field="p",
            initial=float("nan"),
            final=1e-6,
            iterations=2,
        )
    with pytest.raises(ValidationError):
        ResidualFact(
            step_id="solve",
            field="p",
            initial=-1.0,
            final=1e-6,
            iterations=2,
        )
    with pytest.raises(ValidationError):
        CourantFact(step_id="solve", mean=0.1, maximum=float("inf"))


def test_solver_progress_and_written_times_are_ordered() -> None:
    # Ordering is source ordering and must never be silently sorted.
    with pytest.raises(ValidationError, match="solver time"):
        RunFacts(
            **{
                **_defaults(),
                "solver_progress": (
                    SolverProgressFact(
                        step_id="solve", simulation_time=0.2
                    ),
                    SolverProgressFact(
                        step_id="solve", simulation_time=0.1
                    ),
                ),
            }
        )

    with pytest.raises(ValidationError, match="written times"):
        RunFacts(**{**_defaults(), "written_times": (0.2, 0.1)})


def test_run_facts_validate_log_hashes_and_references() -> None:
    with pytest.raises(ValidationError):
        RawCommandEvidence.model_validate(
            {**_raw_step().model_dump(), "stdout_sha256": "BAD"}
        )
    with pytest.raises(ValidationError, match="unknown step"):
        RunFacts(
            **{
                **_defaults(),
                "residuals": (
                    ResidualFact(
                        step_id="missing",
                        field="p",
                        initial=1.0,
                        final=0.1,
                        iterations=1,
                    ),
                ),
            }
        )


def test_run_facts_hold_observations_without_hypotheses() -> None:
    facts = RunFacts(
        **{
            **_defaults(),
            "mesh_checks": (
                MeshCheckFact(
                    step_id="solve",
                    executed=True,
                    mesh_ok=None,
                ),
            ),
            "residuals": (
                ResidualFact(
                    step_id="solve",
                    simulation_time=0.1,
                    field="p",
                    initial=1.0,
                    final=0.01,
                    iterations=2,
                ),
            ),
            "continuity": (
                ContinuityFact(
                    step_id="solve",
                    simulation_time=0.1,
                    local=1e-8,
                    global_value=-1e-9,
                    cumulative=1e-7,
                ),
            ),
            "courant": (
                CourantFact(
                    step_id="solve",
                    simulation_time=0.1,
                    mean=0.02,
                    maximum=0.1,
                ),
            ),
            "native_errors": (
                NativeErrorFact(
                    step_id="solve",
                    code="FLOATING_POINT_EXCEPTION",
                    detail="signal raised",
                    line_number=42,
                ),
            ),
            "written_times": (0.1,),
            "output_files": ("0.1/U", "0.1/p"),
        }
    )

    assert facts.residuals[0].field == "p"
    assert "hypotheses" not in RunFacts.model_fields
    with pytest.raises(ValidationError):
        facts.attempt = 2
