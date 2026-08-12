from __future__ import annotations

from datetime import datetime, timedelta, timezone

from foampilot.agent.failure import (
    FailureEvidence,
    FailureScopeHints,
    NativeFailureClassification,
)
from foampilot.evidence import (
    ContinuityFact,
    CourantFact,
    NativeErrorFact,
    RawCommandEvidence,
    ResidualFact,
    RunFacts,
    SolverProgressFact,
)
from foampilot.repair import RepairDecision
from foampilot.reporting import build_failure_report
from foampilot.workflow import FailureDomain


_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
_SHA = "a" * 64


def _facts() -> RunFacts:
    step = RawCommandEvidence(
        step_id="solve",
        stage="solve",
        executable="pisoFoam",
        argv=("pisoFoam",),
        return_code=136,
        started_at=_NOW,
        finished_at=_NOW + timedelta(seconds=5),
        elapsed_seconds=4.8,
        timed_out=False,
        stdout_path="attempt-01/logs/solve.out",
        stderr_path="attempt-01/logs/solve.err",
        stdout_sha256=_SHA,
        stderr_sha256=_SHA,
        execution_backend="host",
    )
    return RunFacts(
        run_id="run-failed",
        attempt=1,
        plan_sha256=_SHA,
        extractor_identities={"foundation-10": "1.0.0/protocol-1"},
        raw_steps=(step,),
        solver_progress=(
            SolverProgressFact(step_id="solve", simulation_time=0.1),
            SolverProgressFact(
                step_id="solve",
                simulation_time=0.2,
                completed_normally=False,
            ),
        ),
        residuals=(
            ResidualFact(
                step_id="solve",
                simulation_time=0.1,
                field="p",
                initial=0.1,
                final=0.01,
                iterations=2,
            ),
            ResidualFact(
                step_id="solve",
                simulation_time=0.2,
                field="p",
                initial=12,
                final=8,
                iterations=100,
            ),
        ),
        continuity=(
            ContinuityFact(
                step_id="solve", simulation_time=0.1, cumulative=0.01
            ),
            ContinuityFact(
                step_id="solve", simulation_time=0.2, cumulative=20
            ),
        ),
        courant=(
            CourantFact(
                step_id="solve", simulation_time=0.1, mean=0.1, maximum=0.5
            ),
            CourantFact(
                step_id="solve", simulation_time=0.2, mean=4, maximum=92
            ),
        ),
        native_errors=(
            NativeErrorFact(
                step_id="solve",
                code="FLOATING_POINT_EXCEPTION",
                detail="Floating point exception",
                line_number=20,
            ),
        ),
        written_times=(0.1,),
        output_files=("0.1/U", "0.1/p"),
        source_sha256={
            "attempt-01/logs/solve.out": _SHA,
            "attempt-01/logs/solve.err": _SHA,
        },
    )


def _classification(
    *,
    code: str = "numerical_instability",
    confidence: str = "low",
) -> NativeFailureClassification:
    return NativeFailureClassification(
        domain=FailureDomain.SOLVER,
        code=code,
        confidence=confidence,
        failed_stage="solve",
        failed_step_id="solve",
        evidence=[FailureEvidence(kind="log_pattern", value=code)],
        scope_hints=FailureScopeHints(
            files=["system/controlDict"],
            commands=["solve"],
        ),
        allowed_operations=["replace_file"],
    )


def test_divergence_report_does_not_promote_hypothesis_to_cause() -> None:
    report = build_failure_report(
        _facts(),
        _classification(),
        repair_decision=None,
        progress=("mesh_checked", "solver_started"),
        artifacts=("attempt-01/run-facts.json", "attempt-01/0.1/U"),
    )

    assert {item.code for item in report.observations} >= {
        "COURANT_GROWTH",
        "RESIDUAL_GROWTH",
        "FLOATING_POINT_EXCEPTION",
        "NORMAL_END_MISSING",
    }
    assert report.confirmed_causes == ()
    assert report.hypotheses[0].label == "hypothesis"
    assert report.failed_attempt == 1
    assert report.failed_step_id == "solve"
    assert report.completed_progress == ("mesh_checked", "solver_started")
    assert "attempt-01/run-facts.json" in report.evidence_paths


def test_disabled_repair_reason_is_explicit() -> None:
    report = build_failure_report(
        _facts(),
        _classification(confidence="high"),
        repair_decision=RepairDecision(
            state="FINALIZE_FAILED",
            reason_codes=("AUTOMATIC_NUMERICAL_REPAIR_DISABLED",),
        ),
    )

    assert report.automatic_repair.status == "disabled"
    assert report.automatic_repair.reason == "disabled_by_user"
    assert any("自动数值修复" in action for action in report.recommended_actions)
    assert report.confirmed_causes == ()


def test_explicit_mechanical_error_can_be_a_confirmed_cause() -> None:
    report = build_failure_report(
        _facts(),
        _classification(
            code="missing_dictionary_keyword",
            confidence="high",
        ),
        repair_decision=RepairDecision(
            state="MECHANICAL_PATCH",
            reason_codes=("DETERMINISTIC_MECHANICAL_REPAIR",),
        ),
    )

    assert report.confirmed_causes[0].code == "missing_dictionary_keyword"
    assert report.confirmed_causes[0].evidence_paths
    assert report.hypotheses == ()
    assert report.automatic_repair.status == "authorized"


def test_unknown_cause_and_confirmation_disposition_remain_truthful() -> None:
    report = build_failure_report(
        _facts(),
        _classification(
            code="unclassified_native_failure",
            confidence="low",
        ),
        repair_decision=RepairDecision(
            state="CONFIRMATION_REQUIRED",
            reason_codes=("PHYSICAL_CHANGE_REQUIRES_CONFIRMATION",),
            confirmation_paths=("materials.fluid.nu",),
        ),
    )

    assert report.confirmed_causes == ()
    assert report.hypotheses[0].code == "unclassified_native_failure"
    assert report.automatic_repair.status == "confirmation_required"
    assert any("确认" in action for action in report.recommended_actions)


def test_cancel_timeout_and_backend_layers_are_not_conflated() -> None:
    cancelled_step = _facts().raw_steps[0].model_copy(
        update={"cancelled": True, "return_code": -15}
    )
    facts = _facts().model_copy(update={"raw_steps": (cancelled_step,)})
    report = build_failure_report(
        facts,
        _classification(code="unclassified_native_failure"),
        repair_decision=None,
    )

    assert "COMMAND_CANCELLED" in {item.code for item in report.observations}
    assert report.failure_layer == "solver"
    assert report.failure_code == "unclassified_native_failure"
