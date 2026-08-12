from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from foampilot.evidence import RawCommandEvidence, RunFacts
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelStage,
)
from foampilot.reporting import (
    FailureHypothesis,
    FailureReport,
    RepairDisposition,
    append_model_diagnostic,
)


_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
_SHA = "a" * 64


def _facts() -> RunFacts:
    return RunFacts(
        run_id="run-failed",
        attempt=2,
        plan_sha256=_SHA,
        extractor_identities={"foundation-10": "1.0.0/protocol-1"},
        raw_steps=(
            RawCommandEvidence(
                step_id="solve",
                stage="solve",
                executable="pisoFoam",
                argv=("pisoFoam",),
                return_code=136,
                started_at=_NOW,
                finished_at=_NOW + timedelta(seconds=1),
                elapsed_seconds=0.9,
                timed_out=False,
                stdout_path="attempt-02/logs/solve.out",
                stderr_path="attempt-02/logs/solve.err",
                stdout_sha256=_SHA,
                stderr_sha256=_SHA,
                execution_backend="host",
            ),
        ),
        source_sha256={
            "attempt-02/logs/solve.out": _SHA,
            "attempt-02/logs/solve.err": _SHA,
        },
    )


def _report() -> FailureReport:
    return FailureReport(
        failure_layer="solver",
        failure_code="numerical_instability",
        failed_stage="solve",
        failed_attempt=2,
        failed_step_id="solve",
        observations=(),
        confirmed_causes=(),
        hypotheses=(
            FailureHypothesis(
                code="numerical_instability",
                detail="待验证解释。",
                basis=("COMMAND_RETURNED_NONZERO",),
            ),
        ),
        automatic_repair=RepairDisposition(
            status="disabled",
            reason="disabled_by_user",
        ),
        completed_progress=("solver_started",),
        preserved_artifacts=("attempt-02/run-facts.json",),
        recommended_actions=("检查证据。",),
        evidence_paths=("attempt-02/run-facts.json",),
    )


def _budget():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.FAILURE_DIAGNOSTIC,
        request_timeout_seconds=20,
        stage_deadline_seconds=30,
        max_transport_attempts=1,
    )


class _Gateway:
    def __init__(self) -> None:
        self.request = None
        self.schema = None

    def generate_structured(self, request, schema, *, budget, trace):
        self.request = request
        self.schema = schema
        assert budget.stage == ModelStage.FAILURE_DIAGNOSTIC
        return SimpleNamespace(
            value=schema(
                summary="可能由时间步过大导致，但需要进一步验证。",
                suggested_actions=("核对 Courant 数历史",),
            )
        )


class _FailingGateway:
    def generate_structured(self, *args, **kwargs):
        raise RuntimeError("provider unavailable")


def test_model_diagnostic_is_labeled_hypothesis_and_non_authoritative() -> None:
    base = _report()
    gateway = _Gateway()

    report = append_model_diagnostic(
        base,
        _facts(),
        gateway,
        _budget(),
        InMemoryModelTraceSink(),
    )

    assert report.model_diagnostic is not None
    assert report.model_diagnostic.label == "hypothesis"
    assert report.model_diagnostic.status == "available"
    assert report.failure_code == base.failure_code
    assert report.automatic_repair == base.automatic_repair
    response_properties = gateway.schema.model_json_schema()["properties"]
    assert "confirmed_causes" not in response_properties
    assert "failure_code" not in response_properties
    assert "terminal_state" not in response_properties
    assert "attempt-02/logs/solve.out" not in gateway.request.user_prompt


def test_backend_failure_preserves_complete_base_report() -> None:
    base = _report()

    report = append_model_diagnostic(
        base,
        _facts(),
        _FailingGateway(),
        _budget(),
        InMemoryModelTraceSink(),
    )

    assert report.model_diagnostic is not None
    assert report.model_diagnostic.status == "unavailable"
    assert report.observations == base.observations
    assert report.confirmed_causes == base.confirmed_causes
    assert report.hypotheses == base.hypotheses
    assert report.failure_code == base.failure_code


def test_disabled_model_diagnostic_is_explicit_without_transport() -> None:
    base = _report()

    report = append_model_diagnostic(
        base,
        _facts(),
        None,
        _budget(),
        InMemoryModelTraceSink(),
    )

    assert report.model_diagnostic is not None
    assert report.model_diagnostic.status == "disabled"
    assert report.failure_code == base.failure_code
