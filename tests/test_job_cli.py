from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import time

from foampilot.cli.main import main
from foampilot.jobs import LocalJobStore, build_job_spec, launch_local_job
from foampilot.jobs import JobState


def _store(tmp_path: Path) -> LocalJobStore:
    project = tmp_path / "project"
    root = project / "runs/job-cli"
    root.mkdir(parents=True)
    task = project / "task.yaml"
    task.write_text("task_id: cli\n", encoding="utf-8")
    store = LocalJobStore(root)
    store.create(
        build_job_spec(
            job_root=root,
            project_root=project,
            operation="solve",
            arguments=("solve", str(task), "--run-root", str(root), "--json"),
        )
    )
    store.initialize_status()
    return store


def test_job_status_and_cancel_cli_are_json_and_idempotent(
    tmp_path: Path,
    capsys,
) -> None:
    store = _store(tmp_path)

    assert main(["job", "status", str(store.root), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "SUBMITTED"

    assert main(["job", "cancel", str(store.root), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["job", "cancel", str(store.root), "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second


def test_job_reconcile_cli_reports_allowed_actions(
    tmp_path: Path,
    capsys,
) -> None:
    store = _store(tmp_path)

    assert main(["job", "reconcile", str(store.root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["state"] == "ORPHANED_STOPPED"
    assert payload["code"] == "JOB_ORPHANED_STOPPED_WITHOUT_RUN"
    assert payload["allowed_actions"] == ["inspect"]


def test_job_cancel_cli_rejects_terminal_or_orphaned_job(
    tmp_path: Path,
    capsys,
) -> None:
    store = _store(tmp_path)
    store.update_status(state=JobState.FAILED, terminal_code="FAILED")

    assert main(["job", "cancel", str(store.root), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INVALID_INPUT"
    assert "cannot be cancelled" in payload["error"]
    assert not store.cancel_path.exists()


def test_job_cancel_cli_reports_terminal_race_truthfully(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    store = _store(tmp_path)
    original = LocalJobStore.request_cancel

    def finish_during_cancel(self, *, requested_by):
        request = original(self, requested_by=requested_by)
        self.update_status(state=JobState.FAILED, terminal_code="CLI_EXIT_5")
        return request

    monkeypatch.setattr(
        LocalJobStore,
        "request_cancel",
        finish_during_cancel,
    )

    assert main(["job", "cancel", str(store.root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ALREADY_TERMINAL"
    assert payload["job_state"] == "FAILED"
    assert payload["code"] == "JOB_TERMINAL_RUN_MISSING"


def test_job_recover_finalize_cli_freezes_partial_run(
    tmp_path: Path,
    capsys,
) -> None:
    store = _store(tmp_path)
    run_dir = store.root / "run-partial"
    run_dir.mkdir()
    (run_dir / "task.yaml").write_text(
        "schema_version: 2\ntask_id: interrupted-cli\n",
        encoding="utf-8",
    )
    store.update_status(state=JobState.RUNNING, run_dir=run_dir.name)

    assert main(["job", "recover-finalize", str(store.root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["state"] == "FINALIZED"
    assert store.read_status().state == JobState.INTERRUPTED
    assert (run_dir / "interruption.json").is_file()


def test_worker_cli_delegates_to_local_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _store(tmp_path)
    observed: list[Path] = []
    monkeypatch.setattr(
        "foampilot.jobs.run_local_job",
        lambda root: observed.append(Path(root)) or 7,
    )

    assert main(["worker", "run", str(store.root)]) == 7
    assert observed == [store.root]


def test_detached_launcher_uses_fixed_session_argv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _store(tmp_path)
    observed: dict[str, object] = {}

    def fake_popen(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr("foampilot.jobs.worker.subprocess.Popen", fake_popen)

    assert launch_local_job(store.root, program="/safe/python") == 12345
    assert observed["argv"] == [
        "/safe/python",
        "-m",
        "foampilot.cli.main",
        "worker",
        "run",
        str(store.root),
    ]
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["start_new_session"] is True


def test_real_detached_worker_finishes_after_launcher_returns(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    root = project / "runs/job-detached"
    root.mkdir(parents=True)
    task = project / "task.yaml"
    task.write_text("not: a-valid-task\n", encoding="utf-8")
    output = project / "plan.json"
    store = LocalJobStore(root)
    store.create(
        build_job_spec(
            job_root=root,
            project_root=project,
            operation="plan",
            arguments=(
                "plan",
                str(task),
                "--output",
                str(output),
                "--json",
            ),
        )
    )
    store.initialize_status()

    pid = launch_local_job(store.root)
    deadline = time.monotonic() + 5
    while store.read_status().state not in {
        JobState.COMPLETED,
        JobState.FAILED,
    }:
        assert time.monotonic() < deadline
        time.sleep(0.02)

    status = store.read_status()
    assert status.state == JobState.COMPLETED
    assert status.worker is not None
    assert status.worker.pid == pid
    assert status.terminal_code == "CLI_EXIT_2"
    assert "INVALID_INPUT" in (store.root / "worker.stdout.log").read_text()
