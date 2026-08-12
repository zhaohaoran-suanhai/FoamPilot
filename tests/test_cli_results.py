from __future__ import annotations

import json
from pathlib import Path

from foampilot.acceptance import ConditionResult, ObservationResult, ResultReport
from foampilot.cli.main import main
from foampilot.desktop.repository import RunRepository
from foampilot.observations import ObservationScope
from foampilot.postprocessing import DerivedMetrics, MetricSample, MetricSeries


def _write_results(run: Path) -> None:
    metrics = DerivedMetrics(
        run_facts_sha256="a" * 64,
        observation_plan_sha256="b" * 64,
        series=(
            MetricSeries(
                observation_id="continuity",
                quantity="continuity",
                dimension="1",
                scope=ObservationScope(kind="global"),
                status="AVAILABLE",
                samples=(
                    MetricSample(
                        time=1.0,
                        value=3.0e-8,
                        unit="1",
                        evidence_refs=("attempt-01/run-facts.json",),
                    ),
                ),
            ),
        ),
    )
    report = ResultReport(
        verdict="PASS",
        acceptance_plan_sha256="c" * 64,
        observation_plan_sha256=metrics.observation_plan_sha256,
        run_facts_sha256=metrics.run_facts_sha256,
        derived_metrics_sha256=metrics.canonical_sha256(),
        conditions=(
            ConditionResult(
                condition_id="continuity-limit",
                observation_id="continuity",
                status="PASS",
                observed_value=3.0e-8,
                unit="1",
                detail="condition satisfied",
                evidence_refs=("attempt-01/run-facts.json",),
            ),
        ),
        observations=(
            ObservationResult(
                observation_id="continuity",
                status="AVAILABLE",
                latest_value=3.0e-8,
                unit="1",
                evidence_refs=("attempt-01/run-facts.json",),
            ),
        ),
    )
    (run / "derived-metrics.json").write_text(
        metrics.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (run / "result-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def test_results_command_matches_desktop_projection(
    tmp_path: Path,
    capsys,
) -> None:
    run = tmp_path / "run-results"
    run.mkdir()
    _write_results(run)

    assert main(["results", str(run), "--json"]) == 0

    cli = json.loads(capsys.readouterr().out)
    desktop = RunRepository().open(run).projection
    assert cli["result_report"] == desktop.result_report.model_dump(mode="json")
    assert cli["derived_metrics"] == desktop.derived_metrics.model_dump(mode="json")


def test_results_command_reports_legacy_run_without_recomputing(
    tmp_path: Path,
    capsys,
) -> None:
    run = tmp_path / "run-legacy"
    run.mkdir()

    assert main(["results", str(run), "--json"]) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["result_report"] is None
    assert payload["derived_metrics"] is None


def test_malformed_result_artifact_is_hidden_with_diagnostic(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run-malformed"
    run.mkdir()
    (run / "result-report.json").write_text("{bad", encoding="utf-8")

    projection = RunRepository().open(run).projection

    assert projection.result_report is None
    assert "RESULT_REPORT_INVALID" in projection.warnings
