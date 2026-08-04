from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from foampilot.models import (
    BackendFailureKind,
    BackendMode,
    BackendRegistry,
    GatewayRequestError,
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelGateway,
    ModelRequest,
    ModelStage,
    SharedCircuitBreaker,
)
from foampilot.qualification.models import QualificationReport
from foampilot.qualification.runner import run_qualification_suite
from foampilot.qualification.suites import (
    QualificationSuite,
    SuiteCase,
    SuiteRole,
)
from tests.support.model_gateway import (
    FakeClock,
    ScriptedBackend,
    backend_error,
)


def test_suite_passes_one_injected_gateway_to_every_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    suite = QualificationSuite(
        protocol_id="gateway-sharing-test",
        max_workers=2,
        cases=[
            SuiteCase(
                case_id="laminar-cavity",
                role=SuiteRole.REGRESSION,
            ),
            SuiteCase(
                case_id="potential-cylinder",
                role=SuiteRole.REGRESSION,
            ),
        ],
    )
    sentinel_gateway = object()
    observed: list[object] = []

    def fake_run_one(case_id, *, run_root, gateway):
        del run_root
        observed.append(gateway)
        return {"case_id": case_id}

    def fake_build(
        raw_results,
        *,
        backend_id,
        model_name,
        protocol_id,
        case_order,
    ):
        del raw_results, case_order
        return QualificationReport(
            protocol_id=protocol_id,
            created_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            backend_id=backend_id,
            model_name=model_name,
            counts={
                "PASS": 0,
                "FAIL_AGENT": 0,
                "DEFERRED_BACKEND": 0,
                "BLOCKED_ENVIRONMENT": 0,
                "INVALID_QUALIFICATION": 0,
            },
            results=[],
        )

    monkeypatch.setattr(
        "foampilot.qualification.runner._run_one",
        fake_run_one,
    )
    monkeypatch.setattr(
        "foampilot.qualification.runner.build_qualification_report",
        fake_build,
    )
    monkeypatch.setattr(
        "foampilot.qualification.runner.write_qualification_report",
        lambda report, run_root: (
            run_root / "report.json",
            run_root / "report.md",
        ),
    )

    run_qualification_suite(
        suite=suite,
        run_root=tmp_path,
        workers=2,
        backend_id="fake",
        model_name="fake-model",
        gateway=sentinel_gateway,
    )

    assert observed == [sentinel_gateway, sentinel_gateway]


def test_shared_breaker_defers_later_task_without_http(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Output(BaseModel):
        value: str

    suite = QualificationSuite(
        protocol_id="breaker-sharing-test",
        max_workers=1,
        cases=[
            SuiteCase(
                case_id="laminar-cavity",
                role=SuiteRole.REGRESSION,
            ),
            SuiteCase(
                case_id="potential-cylinder",
                role=SuiteRole.REGRESSION,
            ),
        ],
    )
    clock = FakeClock()
    backend = ScriptedBackend(
        [
            backend_error(
                BackendFailureKind.OVERLOADED,
                retryable=True,
            )
            for _ in range(3)
        ]
    )
    registry = BackendRegistry()
    registry.register(backend, priority=10)
    gateway = ModelGateway(
        registry=registry,
        mode=BackendMode.QUALIFICATION,
        pinned_backend_id="fake",
        pinned_model="fake-model",
        circuit_breaker=SharedCircuitBreaker(
            failure_threshold=1,
            cooldown_seconds=120,
            monotonic=clock.monotonic,
        ),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )
    deferred_by_circuit: list[bool] = []

    def fake_run_one(case_id, *, run_root, gateway):
        del run_root
        ledger = ModelBudgetLedger.start(
            now=clock.monotonic,
        )
        try:
            gateway.generate_structured(
                ModelRequest(
                    purpose="qualification-test",
                    system_prompt="test",
                    user_prompt=case_id,
                ),
                Output,
                budget=ledger.open_stage(
                    ModelStage.GENERATION,
                    stage_deadline_seconds=360,
                    now=clock.monotonic,
                ),
                trace=InMemoryModelTraceSink(),
            )
        except GatewayRequestError as error:
            deferred_by_circuit.append(error.deferred_by_circuit)
        return {"case_id": case_id}

    monkeypatch.setattr(
        "foampilot.qualification.runner._run_one",
        fake_run_one,
    )
    monkeypatch.setattr(
        "foampilot.qualification.runner.build_qualification_report",
        lambda raw_results, **kwargs: QualificationReport(
            protocol_id=kwargs["protocol_id"],
            created_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            backend_id=kwargs["backend_id"],
            model_name=kwargs["model_name"],
            counts={
                "PASS": 0,
                "FAIL_AGENT": 0,
                "DEFERRED_BACKEND": 0,
                "BLOCKED_ENVIRONMENT": 0,
                "INVALID_QUALIFICATION": 0,
            },
            results=[],
        ),
    )
    monkeypatch.setattr(
        "foampilot.qualification.runner.write_qualification_report",
        lambda report, run_root: (
            run_root / "report.json",
            run_root / "report.md",
        ),
    )

    run_qualification_suite(
        suite=suite,
        run_root=tmp_path,
        workers=1,
        backend_id="fake",
        model_name="fake-model",
        gateway=gateway,
    )

    assert deferred_by_circuit == [False, True]
    assert len(backend.timeouts) == 3
