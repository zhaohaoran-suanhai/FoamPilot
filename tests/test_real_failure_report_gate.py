from __future__ import annotations

import json
from pathlib import Path

from foampilot.cli.main import cli_progress_payload
from foampilot.desktop.repository import RunRepository
from tests.support.tasks import canonical_task_payload
from tests.test_native_agent_state_machine import SequencePlanRunner, _agent
from tests.test_native_case_generation import RecordingModel, _plan, _task


def test_real_numerical_failure_is_visible_with_disabled_repair(
    tmp_path: Path,
) -> None:
    payload = _task().model_dump(mode="json")
    payload["repair_policy"] = {
        "automatic_numerical_repair": False,
        "model_diagnostic": False,
    }
    task = _task().model_validate(canonical_task_payload(payload))
    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_plan()]),
        runner=SequencePlanRunner(
            [(1, "Courant Number mean: 2 max: 10\n", "Floating point exception\n")]
        ),
    ).solve(task)

    report_path = outcome.run_dir / "failure-report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["failed_stage"] == "solve"
    assert report["observations"]
    assert report["confirmed_causes"] == []
    assert report["hypotheses"][0]["label"] == "hypothesis"
    assert report["automatic_repair"]["status"] == "disabled"
    assert report["automatic_repair"]["reason"] == "disabled_by_user"
    assert report["recommended_actions"]
    assert report["evidence_paths"]

    cli = cli_progress_payload(outcome.run_dir)
    desktop = RunRepository().open(outcome.run_dir).projection.model_dump(
        mode="json"
    )
    assert cli == desktop
    assert cli["failure_summary"]["code"] == "numerical_instability"
    assert cli["failure_summary"]["automatic_repair_reason"] == (
        "disabled_by_user"
    )
