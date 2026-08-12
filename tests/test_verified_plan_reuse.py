from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.plans import NativeCommand
from foampilot.runtime import PlanRunResult, PlanStepResult

from tests.test_native_agent_state_machine import _runtime_config
from tests.test_native_agent_state_machine import (
    POLY_MESH_FIXTURE,
    ProvidedMeshRunner,
    _provided_task,
)
from tests.test_native_case_generation import (
    RecordingModel,
    _environment,
    _plan,
    _task,
)
from tests.support.runtime import synthetic_execution_evidence


class VerifiedPlanRunner:
    def __init__(self, *, mesh_ok: bool = True) -> None:
        self.mesh_ok = mesh_ok
        self.calls: list[list[NativeCommand]] = []

    def run(
        self,
        *,
        case_dir,
        commands,
        budget,
        risk_report,
        protected_paths,
        execution_seconds_used=0.0,
    ):
        del budget, risk_report, execution_seconds_used
        case = Path(case_dir)
        self.calls.append(list(commands))
        log_dir = case / ".foampilot/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        steps = []
        for index, command in enumerate(commands, start=1):
            stdout = log_dir / f"{index:02d}-{command.step_id}.stdout.log"
            stderr = log_dir / f"{index:02d}-{command.step_id}.stderr.log"
            if command.executable == "checkMesh":
                stdout.write_text(
                    "cells: 10\n" + ("Mesh OK.\n" if self.mesh_ok else ""),
                    encoding="utf-8",
                )
            elif command.stage == "solve":
                stdout.write_text("Time = 1\nEnd\n", encoding="utf-8")
            else:
                stdout.write_text("End\n", encoding="utf-8")
            stderr.write_text("", encoding="utf-8")
            steps.append(
                PlanStepResult(
                    step_id=command.step_id,
                    command=[command.executable, *command.args],
                    return_code=0,
                    started_at=now,
                    finished_at=now,
                    elapsed_seconds=0.0,
                    timed_out=False,
                    stdout_path=stdout,
                    stderr_path=stderr,
                    execution_backend="host",
                )
            )
        return PlanRunResult(
            case_dir=case,
            steps=steps,
            **synthetic_execution_evidence(protected_paths),
        )


class VerifiedPlanRunnerWithProbe(VerifiedPlanRunner):
    def __init__(self) -> None:
        super().__init__()
        self._probe = ProvidedMeshRunner()

    def probe_provided_mesh(self, **kwargs):
        return self._probe.probe_provided_mesh(**kwargs)


def _verified_plan():
    plan = _plan()
    return plan.model_copy(
        update={
            "commands": [
                plan.commands[0],
                NativeCommand(
                    step_id="check-mesh",
                    stage="check",
                    executable="checkMesh",
                    timeout_seconds=30,
                ),
                plan.commands[1],
            ]
        }
    )


def _source_run(tmp_path: Path):
    store = ArtifactStore(tmp_path / "runs")
    model = RecordingModel([_verified_plan()])
    outcome = NativeAgent(
        gateway=model,
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment(
            "blockMesh",
            "checkMesh",
            "icoFoam",
        ),
        runner=VerifiedPlanRunner(),
    ).solve(_task())
    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert store.verify(outcome.run_dir) == []
    return store, outcome, model


def test_exact_verified_plan_reuse_executes_without_model_request(
    tmp_path: Path,
) -> None:
    store, source, source_model = _source_run(tmp_path)
    warm_runner = VerifiedPlanRunner()

    outcome = NativeAgent(
        gateway=None,
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment(
            "blockMesh",
            "checkMesh",
            "icoFoam",
        ),
        runner=warm_runner,
    ).solve(_task(), reuse_verified_plan=source.run_dir)

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert len(source_model.requests) == 1
    assert len(warm_runner.calls) == 1
    assert not (outcome.run_dir / "model-attempts.jsonl").exists()
    model_configuration = json.loads(
        (outcome.run_dir / "model-configuration.json").read_text(
            encoding="utf-8"
        )
    )
    assert model_configuration["logical_model_requests"] == 0
    assert model_configuration["transport_attempts"] == 0
    reuse = json.loads(
        (outcome.run_dir / "plan-reuse.json").read_text(encoding="utf-8")
    )
    assert reuse["status"] == "hit"
    assert reuse["source_run_id"] == source.run_dir.name
    performance = json.loads(
        (outcome.run_dir / "performance-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert performance["path_kind"] == "warm_plan"
    assert performance["reuse"]["plan"] == "hit"
    assert store.verify(outcome.run_dir) == []


def test_verified_plan_reuse_accepts_provided_mesh_without_mesh_command(
    tmp_path: Path,
) -> None:
    public_root = tmp_path / "public"
    shutil.copytree(POLY_MESH_FIXTURE, public_root / "mesh/native")
    task = _provided_task(public_root)
    original = _verified_plan()
    plan = original.model_copy(
        update={
            "manifest": original.manifest.model_copy(
                update={"mesh_family": "provided"}
            ),
            "commands": [
                command
                for command in original.commands
                if command.stage != "mesh"
            ]
        }
    )
    store = ArtifactStore(tmp_path / "runs")
    source = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment("checkMesh", "icoFoam"),
        runner=VerifiedPlanRunnerWithProbe(),
    ).solve(task, public_asset_root=public_root)
    assert source.status == "PUBLIC_VALIDATION_PASS"

    outcome = NativeAgent(
        gateway=None,
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment("checkMesh", "icoFoam"),
        runner=VerifiedPlanRunnerWithProbe(),
    ).solve(
        task,
        public_asset_root=public_root,
        reuse_verified_plan=source.run_dir,
    )

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert json.loads(
        (outcome.run_dir / "plan-reuse.json").read_text(encoding="utf-8")
    )["status"] == "hit"


def test_verified_plan_reuse_rejects_changed_task_before_materialization(
    tmp_path: Path,
) -> None:
    store, source, _ = _source_run(tmp_path)
    changed = _task().model_copy(update={"title": "Changed task title"})
    runner = VerifiedPlanRunner()

    outcome = NativeAgent(
        gateway=None,
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment(
            "blockMesh",
            "checkMesh",
            "icoFoam",
        ),
        runner=runner,
    ).solve(changed, reuse_verified_plan=source.run_dir)

    assert outcome.status == "PLAN_REUSE_REJECTED"
    assert runner.calls == []
    assert not list(outcome.run_dir.glob("attempt-*/case"))
    assert "TASK_SHA256_MISMATCH" in outcome.summary.message


def test_verified_plan_reuse_rejects_mutated_source_manifest(
    tmp_path: Path,
) -> None:
    store, source, _ = _source_run(tmp_path)
    (source.run_dir / "execution-plan.json").write_text(
        "{}\n", encoding="utf-8"
    )

    outcome = NativeAgent(
        gateway=None,
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment(
            "blockMesh",
            "checkMesh",
            "icoFoam",
        ),
        runner=VerifiedPlanRunner(),
    ).solve(_task(), reuse_verified_plan=source.run_dir)

    assert outcome.status == "PLAN_REUSE_REJECTED"
    assert "SOURCE_MANIFEST_INVALID" in outcome.summary.message


def test_verified_plan_reuse_rejects_incompatible_source_environment(
    tmp_path: Path,
) -> None:
    store, source, _ = _source_run(tmp_path)
    incompatible = store.root / "source-openfoam-9"
    shutil.copytree(source.run_dir, incompatible)
    (incompatible / store.manifest_name).unlink()
    environment_path = incompatible / "environment.json"
    payload = json.loads(environment_path.read_text(encoding="utf-8"))
    payload["version"] = "9"
    environment_path.write_text(json.dumps(payload), encoding="utf-8")
    store.finalize(incompatible)

    outcome = NativeAgent(
        gateway=None,
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=_environment(
            "blockMesh",
            "checkMesh",
            "icoFoam",
        ),
        runner=VerifiedPlanRunner(),
    ).solve(_task(), reuse_verified_plan=incompatible)

    assert outcome.status == "PLAN_REUSE_REJECTED"
    assert "SOURCE_OPENFOAM_MISMATCH" in outcome.summary.message
