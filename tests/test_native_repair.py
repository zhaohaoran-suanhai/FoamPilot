from __future__ import annotations

from foampilot.agent.repair import failure_fingerprint, should_stop_repair
from foampilot.evidence import RunAssessment
from tests.test_failure_classifier import _facts


def _report() -> RunAssessment:
    return RunAssessment(
        ok=False,
        failure_layer="SOLVER_FAILED",
        failed_step_id="solve",
        reason_codes=("NORMAL_SOLVER_END_MISSING",),
        detail="solver did not end normally",
    )


def test_repair_fingerprint_is_stable_and_stops_repeat() -> None:
    first = failure_fingerprint(_report(), run_facts=_facts())
    second = failure_fingerprint(
        _report().model_copy(deep=True),
        run_facts=_facts(),
    )

    assert first == second
    stop = should_stop_repair(
        fingerprints=[first, second],
        attempts_used=1,
        max_attempts=3,
        generated_bytes_changed=True,
    )
    assert stop.stop
    assert stop.reason == "REPEATED_FAILURE"


def test_repair_stops_noop_unchanged_budget_and_environment() -> None:
    no_op = should_stop_repair(
        fingerprints=["a"],
        attempts_used=1,
        max_attempts=3,
        generated_bytes_changed=True,
        patch_operation_count=0,
    )
    assert no_op.reason == "NO_OP"

    unchanged = should_stop_repair(
        fingerprints=["a"],
        attempts_used=1,
        max_attempts=3,
        generated_bytes_changed=False,
        patch_operation_count=1,
    )
    assert unchanged.reason == "UNCHANGED_BYTES"

    exhausted = should_stop_repair(
        fingerprints=["a"],
        attempts_used=3,
        max_attempts=3,
        generated_bytes_changed=True,
    )
    assert exhausted.reason == "BUDGET_EXHAUSTED"

    environment = should_stop_repair(
        fingerprints=[],
        attempts_used=0,
        max_attempts=3,
        generated_bytes_changed=True,
        environment_failure=True,
    )
    assert environment.reason == "ENVIRONMENT_FAILURE"
