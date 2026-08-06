from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from foampilot.agent import NativeAgent
from foampilot.agent.repair import RepairDecision
from foampilot.artifacts import ArtifactStore
from foampilot.performance import classify_repair_rerun
from foampilot.plans import GeneratedFile, NativeCommand
from foampilot.runtime import PlanRunResult, PlanStepResult

from tests.test_native_agent_state_machine import _runtime_config
from tests.test_native_case_generation import (
    RecordingModel,
    _environment,
    _plan,
    _task,
)


def _decision(*, files=(), commands=()) -> RepairDecision:
    return RepairDecision(
        because="The public failure evidence identifies one dependency family.",
        evidence=["the failed solver step reports a dictionary error"],
        cause="one declared dictionary is inconsistent",
        changed_files=list(files),
        changed_commands=list(commands),
        expected_check="The rerun reaches normal completion.",
        stable_control="Unrelated geometry and mesh dependencies remain unchanged.",
    )


def _plan_with_check():
    plan = _plan(
        files=[
            GeneratedFile(
                path="system/blockMeshDict",
                content="FoamFile{}\nconvertToMeters 1;\n",
            )
        ]
    )
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


def test_repair_dependency_classifier_selects_earliest_safe_stage() -> None:
    plan = _plan_with_check()
    assert classify_repair_rerun(
        plan,
        _decision(
            files=[
                GeneratedFile(
                    path="system/fvSolution",
                    content="FoamFile{}\nsolvers {}\n",
                )
            ]
        ),
    ).earliest_rerun_stage == "solve"
    assert classify_repair_rerun(
        plan,
        _decision(
            files=[
                GeneratedFile(
                    path="0/U",
                    content="FoamFile { class volVectorField; object U; }\n",
                )
            ]
        ),
    ).earliest_rerun_stage == "initialize"
    assert classify_repair_rerun(
        plan,
        _decision(
            files=[
                GeneratedFile(
                    path="system/blockMeshDict",
                    content="FoamFile{}\nconvertToMeters 0.5;\n",
                )
            ]
        ),
    ).earliest_rerun_stage == "mesh"
    assert classify_repair_rerun(
        plan,
        _decision(
            commands=[
                NativeCommand(
                    step_id="solve",
                    stage="solve",
                    executable="icoFoam",
                    args=["-latestTime"],
                    timeout_seconds=60,
                )
            ]
        ),
    ).earliest_rerun_stage == "solve"


class RepairReuseRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, *, case_dir, commands, budget):
        del budget
        case = Path(case_dir)
        self.calls.append([item.executable for item in commands])
        call_number = len(self.calls)
        log_dir = case / ".foampilot/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        steps = []
        failed_step_id = None
        for index, command in enumerate(commands, start=1):
            if command.stage == "mesh":
                poly_mesh = case / "constant/polyMesh"
                poly_mesh.mkdir(parents=True, exist_ok=True)
                (poly_mesh / "points").write_text(
                    "immutable mesh\n", encoding="utf-8"
                )
                (poly_mesh / "boundary").write_text(
                    "FoamFile{}\n0\n(\n)\n", encoding="utf-8"
                )
            stdout = log_dir / f"{index:02d}-{command.step_id}.stdout.log"
            stderr = log_dir / f"{index:02d}-{command.step_id}.stderr.log"
            return_code = 0
            if command.executable == "checkMesh":
                stdout.write_text(
                    "points: 8\nfaces: 12\ncells: 4\nMesh OK.\n",
                    encoding="utf-8",
                )
            elif command.stage == "solve" and call_number == 1:
                stdout.write_text("Time = 0.1\n", encoding="utf-8")
                stderr.write_text("solver dictionary error\n", encoding="utf-8")
                return_code = 1
                failed_step_id = command.step_id
            elif command.stage == "solve":
                stdout.write_text("Time = 1\nEnd\n", encoding="utf-8")
            else:
                stdout.write_text("End\n", encoding="utf-8")
            if not stderr.exists():
                stderr.write_text("", encoding="utf-8")
            now = datetime.now(timezone.utc)
            steps.append(
                PlanStepResult(
                    step_id=command.step_id,
                    command=[command.executable, *command.args],
                    return_code=return_code,
                    started_at=now,
                    finished_at=now,
                    timed_out=False,
                    stdout_path=stdout,
                    stderr_path=stderr,
                    execution_backend="host",
                )
            )
            if return_code != 0:
                break
        return PlanRunResult(
            case_dir=case,
            steps=steps,
            failed_step_id=failed_step_id,
        )


def test_solver_dictionary_repair_reuses_mesh_and_keeps_parent_immutable(
    tmp_path: Path,
) -> None:
    plan = _plan_with_check()
    repair = _decision(
        files=[
            GeneratedFile(
                path="system/fvSolution",
                content="FoamFile { class dictionary; object fvSolution; }\nsolvers {}\n",
            )
        ]
    )
    runner = RepairReuseRunner()
    outcome = NativeAgent(
        gateway=RecordingModel([plan, repair]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment(
            "blockMesh", "checkMesh", "icoFoam"
        ),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert runner.calls == [
        ["blockMesh", "checkMesh", "icoFoam"],
        ["checkMesh", "icoFoam"],
    ]
    parent_mesh = outcome.run_dir / "attempt-01/case/constant/polyMesh/points"
    child_mesh = outcome.run_dir / "attempt-02/case/constant/polyMesh/points"
    assert parent_mesh.read_bytes() == child_mesh.read_bytes()
    parent_digest = sha256(parent_mesh.read_bytes()).hexdigest()
    reuse = json.loads(
        (outcome.run_dir / "attempt-02/execution-reuse.json").read_text(
            encoding="utf-8"
        )
    )
    assert reuse["earliest_rerun_stage"] == "solve"
    assert reuse["source_hashes"]["constant/polyMesh/points"] == parent_digest
    run_result = json.loads(
        (outcome.run_dir / "attempt-02/run-result.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["step_id"] for item in run_result["reused_steps"]] == [
        "mesh"
    ]
    assert json.loads(
        (outcome.run_dir / "performance-summary.json").read_text(
            encoding="utf-8"
        )
    )["path_kind"] == "repair_reuse"
