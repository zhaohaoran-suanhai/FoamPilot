from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path

from foampilot.activity import ActivityEvent
from foampilot.cli.main import _activity_reporter, build_parser, main
from foampilot.desktop.repository import RunRepository
from foampilot.workflow import WorkflowEvent, WorkflowStage


def test_long_running_commands_accept_progress_mode() -> None:
    parser = build_parser()

    solve = parser.parse_args(
        [
            "solve",
            "task.yaml",
            "--run-root",
            "runs",
            "--progress",
            "jsonl",
        ]
    )
    draft = parser.parse_args(
        [
            "task",
            "draft",
            "--request-file",
            "request.md",
            "--output",
            "draft.yaml",
            "--progress",
            "plain",
        ]
    )

    assert solve.progress == "jsonl"
    assert draft.progress == "plain"


def test_jsonl_progress_is_structured_and_does_not_use_stdout() -> None:
    stderr = io.StringIO()
    stdout = io.StringIO()
    reporter = _activity_reporter(
        argparse.Namespace(progress="jsonl"),
        stderr=stderr,
    )

    reporter.emit(
        kind="stage",
        state="started",
        source="workflow",
        message="solve started",
    )
    print(json.dumps({"status": "PASS"}), file=stdout)

    event = ActivityEvent.model_validate_json(stderr.getvalue().strip())
    assert event.message == "solve started"
    assert json.loads(stdout.getvalue()) == {"status": "PASS"}


def test_progress_command_matches_desktop_projection(
    tmp_path: Path,
    capsys,
) -> None:
    run = tmp_path / "run-active"
    run.mkdir()
    (run / "workflow-events.jsonl").write_text(
        WorkflowEvent.started(
            stage=WorkflowStage.EXECUTING,
            sequence=1,
            occurred_at=datetime.now(timezone.utc),
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    assert main(["progress", str(run), "--json"]) == 0

    cli_payload = json.loads(capsys.readouterr().out)
    desktop_payload = RunRepository().open(run).projection.model_dump(
        mode="json"
    )
    assert cli_payload == desktop_payload
