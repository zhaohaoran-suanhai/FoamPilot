from __future__ import annotations

import json
from pathlib import Path

from foampilot.artifacts import ArtifactStore, RunSummary
from foampilot.cli.main import build_parser, main


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

    assert "{validate,plan,solve,inspect,report" in help_text


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
        status="PUBLIC_VALIDATION_PASS",
        message="passed",
    )
    (run_dir / "summary.json").write_text(
        summary.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store.finalize(run_dir)

    assert main(["report", str(run_dir), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["manifest_issues"] == []


def test_cli_validates_packaged_solver_skill_without_scenario_override(
    capsys,
) -> None:
    package_root = Path(__file__).resolve().parents[1] / "src/foampilot"
    skill = package_root / "skills/openfoam-buoyant-case"

    assert main(["skill", "validate", str(skill), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["skill_name"] == "openfoam-buoyant-case"
