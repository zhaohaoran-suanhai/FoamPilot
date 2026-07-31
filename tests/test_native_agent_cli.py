from __future__ import annotations

import json
from pathlib import Path

from foampilot.artifacts import ArtifactStore, NativeAgentOutcome, RunSummary
from foampilot.cli.main import build_parser, main
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ResumeMetadata,
    WorkflowState,
)


def _write_task(path: Path) -> None:
    path.write_text(
        """
schema_version: 1
task_id: cli-native
title: CLI native task
prompt: Solve a small laminar flow case.
openfoam_target:
  distribution: foundation
  version: "10"
resource_budget:
  max_attempts: 1
  max_wall_seconds: 60
  max_mpi_ranks: 1
  memory_mib: 512
required_outputs: [velocity]
acceptance_requirements: [normal completion]
public_checks:
  - name: completion
    kind: completion
    parameters: {}
public_assets: []
protected_paths: []
""".lstrip(),
        encoding="utf-8",
    )


def test_cli_exposes_native_validate_plan_and_solve_commands() -> None:
    help_text = build_parser().format_help()

    assert "{validate,plan,solve,resume,inspect,report" in help_text


def test_resume_command_parses_strict_parent_and_run_root() -> None:
    arguments = build_parser().parse_args(
        [
            "resume",
            "/tmp/runs/parent",
            "--run-root",
            "/tmp/runs",
            "--max-mpi-ranks",
            "4",
            "--json",
        ]
    )

    assert arguments.command == "resume"
    assert arguments.parent_run == Path("/tmp/runs/parent")
    assert arguments.run_root == Path("/tmp/runs")
    assert arguments.max_mpi_ranks == 4


def test_cli_validates_minimal_task_as_json(
    tmp_path: Path,
    capsys,
) -> None:
    task = tmp_path / "task.yaml"
    _write_task(task)

    assert main(["validate", str(task), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "PASS", "task_id": "cli-native"}


def test_report_returns_zero_for_verified_public_validation_pass(
    tmp_path: Path,
    capsys,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    summary = RunSummary(
        task_id="cli-native",
        workflow_state=WorkflowState.COMPLETED,
        native_status="PUBLIC_VALIDATION_PASS",
        resume=ResumeMetadata(
            allowed=False,
            reason="completed runs do not resume",
        ),
        message="passed",
    )
    (run_dir / "summary.json").write_text(
        summary.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store.finalize(run_dir)

    assert main(["report", str(run_dir), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["manifest_issues"] == []


def test_report_returns_three_for_provider_deferred_run(
    tmp_path: Path,
    capsys,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    summary = RunSummary(
        task_id="cli-native",
        workflow_state=WorkflowState.DEFERRED,
        terminal_blocker=FailureRecord(
            domain=FailureDomain.PROVIDER,
            code="PROVIDER_OVERLOADED",
            retryable=True,
            detail="provider overloaded",
        ),
        resume=ResumeMetadata(
            allowed=True,
            from_stage="MODEL_GENERATION_STARTED",
            reason="retryable provider failure",
        ),
        message="deferred",
    )
    (run_dir / "summary.json").write_text(
        summary.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store.finalize(run_dir)

    assert main(["report", str(run_dir), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["workflow_state"] == "DEFERRED"


def test_resume_command_returns_zero_for_success(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    parent = tmp_path / "runs/parent"
    outcome = NativeAgentOutcome(
        run_dir=tmp_path / "runs/child",
        summary=RunSummary(
            task_id="cli-native",
            workflow_state=WorkflowState.COMPLETED,
            native_status="PUBLIC_VALIDATION_PASS",
            resume=ResumeMetadata(
                allowed=False,
                reason="completed",
            ),
            message="passed",
        ),
    )

    class FakeAgent:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def resume(self, parent_run):
            assert parent_run == parent
            return outcome

    monkeypatch.setattr("foampilot.cli.main.NativeAgent", FakeAgent)
    monkeypatch.setattr(
        "foampilot.cli.main._native_gateway",
        lambda arguments: object(),
    )

    assert (
        main(
            [
                "resume",
                str(parent),
                "--run-root",
                str(tmp_path / "runs"),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["summary"][
        "native_status"
    ] == "PUBLIC_VALIDATION_PASS"


def test_cli_validates_packaged_solver_skill_without_scenario_override(
    capsys,
) -> None:
    package_root = Path(__file__).resolve().parents[1] / "src/foampilot"
    skill = package_root / "skills/openfoam-buoyant-case"

    assert main(["skill", "validate", str(skill), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["skill_name"] == "openfoam-buoyant-case"
