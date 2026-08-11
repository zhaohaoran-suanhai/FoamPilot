from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from foampilot.artifacts import ArtifactStore, NativeAgentOutcome, RunSummary
from foampilot.cli.main import build_parser, main
from foampilot.models import BackendHealth
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ResumeMetadata,
    WorkflowState,
)


def _write_task(path: Path) -> None:
    path.write_text(
        """
schema_version: 2
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

    assert "{validate,plan,solve,resume,rerun,inspect,report" in help_text


@pytest.mark.parametrize(
    "argv",
    [
        ["preflight"],
        ["plan", "task.yaml", "--output", "plan.json"],
        ["solve", "task.yaml", "--run-root", "runs"],
        ["resume", "parent", "--run-root", "runs"],
        ["rerun", "parent", "--run-root", "runs"],
        ["inspect", "task.yaml", "plan.json", "case"],
        [
            "qualify",
            "suite",
            "--suite-file",
            "suite.yaml",
            "--run-root",
            "runs",
        ],
        ["desktop"],
    ],
)
def test_runtime_options_exist_on_runtime_commands(
    argv: list[str],
) -> None:
    arguments = build_parser().parse_args(
        [
            *argv,
            "--runtime-config",
            "/tmp/runtime.toml",
            "--openfoam-root",
            "/opt/OpenFOAM/OpenFOAM-10",
            "--execution-isolation",
            "sandbox_required",
            "--bubblewrap",
            "/usr/bin/bwrap",
            "--max-mpi-ranks",
            "3",
            "--trusted-readonly-root",
            "/opt/foam-solvers",
        ]
    )

    assert arguments.runtime_config == Path("/tmp/runtime.toml")
    assert arguments.openfoam_root == Path("/opt/OpenFOAM/OpenFOAM-10")
    assert arguments.execution_isolation == "sandbox_required"
    assert arguments.max_mpi_ranks == 3
    assert arguments.trusted_readonly_root == [Path("/opt/foam-solvers")]


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


def test_rerun_command_parses_explicit_changed_input() -> None:
    arguments = build_parser().parse_args(
        [
            "rerun",
            "/tmp/runs/parent",
            "--run-root",
            "/tmp/runs/new-job",
            "--task",
            "/tmp/changed-task.yaml",
            "--change-category",
            "runtime_policy",
            "--json",
        ]
    )

    assert arguments.command == "rerun"
    assert arguments.task == Path("/tmp/changed-task.yaml")
    assert arguments.change_category == ["runtime_policy"]


def test_solve_uses_auto_backend_without_auth_argument() -> None:
    arguments = build_parser().parse_args(
        [
            "solve",
            "/tmp/task.yaml",
            "--run-root",
            "/tmp/runs",
        ]
    )

    assert arguments.backend == "auto"
    assert arguments.backend_config is None
    assert not hasattr(arguments, "auth")


def test_solve_parses_explicit_verified_plan_reuse_without_qualification_flag() -> None:
    arguments = build_parser().parse_args(
        [
            "solve",
            "/tmp/task.yaml",
            "--run-root",
            "/tmp/runs",
            "--reuse-verified-plan",
            "/tmp/source-run",
        ]
    )

    assert arguments.reuse_verified_plan == Path("/tmp/source-run")
    qualification_help = build_parser()._subparsers._group_actions[0].choices[
        "qualify"
    ].format_help()
    assert "--reuse-verified-plan" not in qualification_help


def test_solve_parses_explicit_derived_cache_without_qualification_flag() -> None:
    arguments = build_parser().parse_args(
        [
            "solve",
            "/tmp/task.yaml",
            "--run-root",
            "/tmp/runs",
            "--derived-cache",
            "/tmp/cache",
        ]
    )

    assert arguments.derived_cache == Path("/tmp/cache")
    qualification_help = build_parser()._subparsers._group_actions[0].choices[
        "qualify"
    ].format_help()
    assert "--derived-cache" not in qualification_help


def test_model_doctor_is_chinese_first_json(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "foampilot.cli.main.load_backend_registry",
        lambda path: object(),
    )
    monkeypatch.setattr(
        "foampilot.cli.main.doctor_backends",
        lambda registry: [
            BackendHealth(
                backend_id="test",
                model="test-model",
                state="available",
                message="模型后端可用。",
                recovery="无需处理。",
                elapsed_seconds=0,
            )
        ],
    )

    assert main(["model", "doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["backends"][0]["message"] == "模型后端可用。"


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


def test_report_returns_three_for_backend_deferred_run(
    tmp_path: Path,
    capsys,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    summary = RunSummary(
        task_id="cli-native",
        workflow_state=WorkflowState.DEFERRED,
        terminal_blocker=FailureRecord(
            domain=FailureDomain.BACKEND,
            code="OVERLOADED",
            retryable=True,
            detail="backend overloaded",
        ),
        resume=ResumeMetadata(
            allowed=True,
            from_stage="MODEL_GENERATION_STARTED",
            reason="retryable backend failure",
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
        lambda arguments, **kwargs: object(),
    )
    monkeypatch.setattr(
        "foampilot.cli.main._resolve_runtime",
        lambda arguments: SimpleNamespace(
            config=object(),
            provenance=object(),
        ),
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


def test_rerun_command_calls_canonical_agent_with_change_categories(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    parent = tmp_path / "runs/parent"
    changed_task = tmp_path / "changed.yaml"
    _write_task(changed_task)
    outcome = NativeAgentOutcome(
        run_dir=tmp_path / "runs/new-job/run-child",
        summary=RunSummary(
            task_id="cli-native",
            workflow_state=WorkflowState.COMPLETED,
            native_status="PUBLIC_VALIDATION_PASS",
            resume=ResumeMetadata(allowed=False, reason="completed"),
            message="passed",
        ),
    )
    observed: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def rerun(self, parent_run, **kwargs):
            observed["parent"] = parent_run
            observed.update(kwargs)
            return outcome

    monkeypatch.setattr("foampilot.cli.main.NativeAgent", FakeAgent)
    monkeypatch.setattr(
        "foampilot.cli.main._native_gateway",
        lambda arguments, **kwargs: object(),
    )
    monkeypatch.setattr(
        "foampilot.cli.main._resolve_runtime",
        lambda arguments: SimpleNamespace(config=object(), provenance=object()),
    )

    assert main(
        [
            "rerun",
            str(parent),
            "--run-root",
            str(tmp_path / "runs/new-job"),
            "--task",
            str(changed_task),
            "--change-category",
            "runtime_policy",
            "--json",
        ]
    ) == 0
    assert observed["parent"] == parent
    assert observed["change_categories"] == ["runtime_policy"]
    assert json.loads(capsys.readouterr().out)["summary"][
        "native_status"
    ] == "PUBLIC_VALIDATION_PASS"


def test_cli_validates_packaged_solver_skill_without_scenario_override(
    capsys,
) -> None:
    package_root = Path(__file__).resolve().parents[1] / "src/foampilot"
    skill = package_root / "skills/openfoam-buoyant-cht"

    assert main(["skill", "validate", str(skill), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["skill_name"] == "openfoam-buoyant-cht"
