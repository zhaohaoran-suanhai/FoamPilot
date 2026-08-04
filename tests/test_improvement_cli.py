from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from foampilot.cli.main import build_parser, main
from foampilot.improvement import (
    LearningCandidate,
    OfficialExampleEvidence,
    PublicEvidence,
    SourceRun,
    write_learning_candidate,
)
from foampilot.qualification.models import (
    QualificationMetric,
    QualificationReport,
    QualificationResult,
)


def _candidate_for_cli() -> LearningCandidate:
    return LearningCandidate(
        candidate_id="candidate-1",
        source_runs=[
            SourceRun(path=Path("/runs/source"), manifest_sha256="a" * 64)
        ],
        root_cause="numerics",
        public_evidence=PublicEvidence(),
        official_example=OfficialExampleEvidence(),
        generalized_lesson="Use a bounded transient time step.",
        proposed_target="knowledge",
        development_cases=["source"],
        promotion_criteria=["source_improves"],
    )


def _qualification_report_for_cli(
    *,
    source_status: str,
) -> QualificationReport:
    passed = source_status == "PASS"
    result = QualificationResult(
        case_id="source",
        status=source_status,
        native_status=(
            "PUBLIC_VALIDATION_PASS" if passed else "SOLVER_FAILED"
        ),
        run_dir=Path("/runs/source"),
        attempts=1,
        model_calls=1,
        metrics=[
            QualificationMetric(
                name="physics",
                passed=passed,
                detail="synthetic CLI evidence",
            )
        ],
        duration_seconds=1.0,
        message="synthetic",
    )
    return QualificationReport(
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        backend_id="test-backend",
        model_name="gpt-test",
        counts={
            "PASS": int(passed),
            "FAIL_AGENT": int(not passed),
            "BLOCKED_ENVIRONMENT": 0,
            "INVALID_QUALIFICATION": 0,
        },
        results=[result],
    )


def test_improve_analyze_command_parses() -> None:
    arguments = build_parser().parse_args(
        [
            "improve",
            "analyze",
            "/runs/run-1",
            "--qualification-report",
            "/runs/report.json",
            "--candidate-id",
            "candidate-1",
            "--lesson",
            "Use a bounded transient time step.",
            "--target",
            "knowledge",
            "--output",
            "/improvements/candidate.yaml",
            "--development-case",
            "source-case",
        ]
    )

    assert arguments.improve_command == "analyze"
    assert arguments.target == "knowledge"


def test_improve_compare_writes_report(
    tmp_path: Path,
    capsys,
) -> None:
    candidate_path = tmp_path / "candidate.yaml"
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    output_path = tmp_path / "promotion.json"
    write_learning_candidate(candidate_path, _candidate_for_cli())
    baseline_path.write_text(
        _qualification_report_for_cli(
            source_status="FAIL_AGENT"
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    current_path.write_text(
        _qualification_report_for_cli(
            source_status="PASS"
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "improve",
                "compare",
                str(baseline_path),
                str(current_path),
                "--candidate",
                str(candidate_path),
                "--output",
                str(output_path),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_id"] == "candidate-1"
    assert payload["eligible"] is True
    assert output_path.is_file()


def test_improve_compare_does_not_overwrite_report(
    tmp_path: Path,
    capsys,
) -> None:
    candidate_path = tmp_path / "candidate.yaml"
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    output_path = tmp_path / "promotion.json"
    write_learning_candidate(candidate_path, _candidate_for_cli())
    baseline_path.write_text(
        _qualification_report_for_cli(
            source_status="FAIL_AGENT"
        ).model_dump_json(),
        encoding="utf-8",
    )
    current_path.write_text(
        _qualification_report_for_cli(
            source_status="PASS"
        ).model_dump_json(),
        encoding="utf-8",
    )
    output_path.write_text("preserve me\n", encoding="utf-8")

    exit_code = main(
        [
            "improve",
            "compare",
            str(baseline_path),
            str(current_path),
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert exit_code == 2
    assert output_path.read_text(encoding="utf-8") == "preserve me\n"
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_INPUT"
