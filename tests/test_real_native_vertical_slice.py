from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.models import (
    CodexOAuthModelClient,
    load_codex_access_token,
)
from foampilot.runtime import RuntimeConfig
from foampilot.tasks import load_task_spec


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = PACKAGE_ROOT / "examples/tasks"
TASKS = (
    TASK_ROOT / "non-tutorial-side-driven-box.yaml",
    TASK_ROOT / "non-tutorial-two-phase-column-collapse.yaml",
)


@pytest.mark.parametrize("task_path", TASKS)
def test_real_native_task_contracts_are_valid(task_path: Path) -> None:
    task = load_task_spec(task_path)

    assert task.openfoam_target.version == "10"
    assert task.resource_budget.max_mpi_ranks == 1
    assert "/home/edwin/workplace/OpenFOAM-10/tutorials" in (
        task.protected_paths
    )


def test_two_phase_public_prompt_does_not_prescribe_application() -> None:
    task = load_task_spec(TASKS[1])
    public_text = f"{task.title}\n{task.prompt}"

    assert "interFoam" not in public_text
    assert "setFields" not in public_text


@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_MODEL") != "1",
    reason="real model/OpenFOAM integration is opt-in",
)
@pytest.mark.parametrize("task_path", TASKS)
def test_real_model_authors_and_solves_native_case(
    tmp_path: Path,
    task_path: Path,
) -> None:
    auth_path = Path(
        os.environ.get(
            "OFKIT_CODEX_AUTH",
            str(Path.home() / ".codex/auth.json"),
        )
    )
    model_name = os.environ.get("OFKIT_CODEX_MODEL", "gpt-5.6-sol")
    config = RuntimeConfig.local_foundation_v10()
    outcome = NativeAgent(
        model=CodexOAuthModelClient(
            model=model_name,
            access_token=load_codex_access_token(auth_path),
            timeout_seconds=600,
        ),
        runtime_config=config,
        artifact_store=ArtifactStore(tmp_path / "runs"),
    ).solve(load_task_spec(task_path))

    assert outcome.status == "PUBLIC_VALIDATION_PASS", outcome.summary
    assert ArtifactStore(tmp_path / "runs").verify(outcome.run_dir) == []
    assert not (outcome.run_dir / "attempt-01/case/Allrun").exists()
    trace = (
        outcome.run_dir / "attempt-01/generation-trace.json"
    ).read_text(encoding="utf-8")
    assert "deterministic_renderer" not in trace
    configuration = json.loads(
        (outcome.run_dir / "model-configuration.json").read_text(
            encoding="utf-8"
        )
    )
    assert configuration["case_bundle_calls"] == 1
    assert not (outcome.run_dir / "plan-review.json").exists()
