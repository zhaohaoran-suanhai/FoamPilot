from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from foampilot.workflow import (
    WorkflowEvent,
    WorkflowStage,
    WorkflowStore,
)


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def test_workflow_store_appends_ordered_events(tmp_path: Path) -> None:
    observed: list[WorkflowEvent] = []
    store = WorkflowStore(
        run_dir=tmp_path,
        event_listener=observed.append,
    )
    store.record(
        WorkflowEvent.started(
            stage=WorkflowStage.MODEL_GENERATION_STARTED,
            sequence=1,
            occurred_at=NOW,
        )
    )
    store.record(
        WorkflowEvent.completed(
            stage=WorkflowStage.PLAN_READY,
            sequence=2,
            occurred_at=NOW,
        )
    )

    events = [
        json.loads(line)
        for line in (tmp_path / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [item["sequence"] for item in events] == [1, 2]
    assert [item["state"] for item in events] == [
        "started",
        "completed",
    ]
    assert observed[1].stage == WorkflowStage.PLAN_READY


def test_workflow_store_rejects_non_contiguous_sequence(
    tmp_path: Path,
) -> None:
    store = WorkflowStore(run_dir=tmp_path)

    with pytest.raises(ValueError, match="contiguous"):
        store.record(
            WorkflowEvent.started(
                stage=WorkflowStage.TASK_VALIDATED,
                sequence=2,
                occurred_at=NOW,
            )
        )


def test_checkpoint_is_exclusive_and_content_addressed(
    tmp_path: Path,
) -> None:
    store = WorkflowStore(run_dir=tmp_path)

    path = store.checkpoint("active-plan", {"value": 1})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["payload"] == {"value": 1}
    assert len(payload["sha256"]) == 64
    with pytest.raises(FileExistsError):
        store.checkpoint("active-plan", {"value": 2})


def test_finish_writes_summary_without_artifact_manifest(
    tmp_path: Path,
) -> None:
    store = WorkflowStore(run_dir=tmp_path)

    path = store.finish({"schema_version": 99, "status": "test"})

    assert path == tmp_path / "summary.json"
    assert not (tmp_path / "artifact-manifest.json").exists()
