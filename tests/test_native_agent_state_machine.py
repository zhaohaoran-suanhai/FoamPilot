from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time

from foampilot.activity import OperationCancelled
from foampilot.agent import NativeAgent
from foampilot.agent.repair_patch import RepairPatch
from foampilot.artifacts import ArtifactStore
from foampilot.models import (
    BackendError,
    BackendFailureKind,
    GatewayRequestError,
)
from foampilot.plans import GeneratedFile, NativeCommand
from foampilot.runtime import (
    ExecutionPolicyDecision,
    ExecutionRiskReport,
    PlanRunResult,
    PlanStepResult,
    RuntimeConfig,
    RuntimeExecutionError,
    SandboxProbe,
)
from foampilot.workflow import (
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowStore,
)

from tests.test_native_case_generation import (
    RecordingModel,
    _environment,
    _plan,
    _task,
)


def _control_dict(*, delta_t: float = 0.01) -> str:
    return (
        "FoamFile\n"
        "{\n"
        "    format ascii;\n"
        "    class dictionary;\n"
        "    object controlDict;\n"
        "}\n"
        "application icoFoam;\n"
        f"deltaT {delta_t};\n"
    )


class SequencePlanRunner:
    def __init__(self, outcomes: list[tuple[int, str, str]]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.risk_reports: list[ExecutionRiskReport] = []
        self.protected_paths: list[tuple[Path, ...]] = []
        self.execution_seconds_used_values: list[float] = []

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
        del budget
        self.risk_reports.append(risk_report)
        self.protected_paths.append(tuple(protected_paths))
        self.execution_seconds_used_values.append(execution_seconds_used)
        return_code, stdout_text, stderr_text = self.outcomes[self.calls]
        self.calls += 1
        command = commands[-1]
        log_dir = Path(case_dir) / ".foampilot/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = log_dir / "solve.stdout.log"
        stderr = log_dir / "solve.stderr.log"
        stdout.write_text(stdout_text, encoding="utf-8")
        stderr.write_text(stderr_text, encoding="utf-8")
        now = datetime.now(timezone.utc)
        step = PlanStepResult(
            step_id=command.step_id,
            command=[command.executable],
            return_code=return_code,
            started_at=now,
            finished_at=now,
            elapsed_seconds=0.0,
            timed_out=False,
            stdout_path=stdout,
            stderr_path=stderr,
        )
        return PlanRunResult(
            case_dir=Path(case_dir),
            steps=[step],
            failed_step_id=(
                None if return_code == 0 else command.step_id
            ),
            sandbox_probe=SandboxProbe(
                status="passed",
                ok=True,
                builder_sha256="a" * 64,
                namespace_flags=("--unshare-net",),
                mount_count=8,
                protected_path_count=len(protected_paths),
                return_code=0,
                detail="synthetic sandbox probe passed",
            ),
            execution_policy=ExecutionPolicyDecision(
                requested_isolation="sandbox_preferred",
                actual_backend="bubblewrap",
                allowed=True,
                code="SANDBOX_SELECTED",
            ),
        )


class LiveSequencePlanRunner(SequencePlanRunner):
    emits_live_workflow = True

    def run(self, *, workflow: WorkflowStore, attempt: int, **kwargs):
        command = kwargs["commands"][-1]
        now = datetime.now(timezone.utc)
        workflow.record(
            WorkflowEvent.started(
                stage=WorkflowStage.OPENFOAM_STEP_STARTED,
                sequence=workflow.next_sequence,
                occurred_at=now,
                attempt=attempt,
                step_id=command.step_id,
            )
        )
        result = super().run(**kwargs)
        workflow.record(
            WorkflowEvent(
                sequence=workflow.next_sequence,
                stage=WorkflowStage.OPENFOAM_STEP_COMPLETE,
                state=WorkflowEventState.COMPLETED,
                occurred_at=now,
                attempt=attempt,
                step_id=command.step_id,
            )
        )
        return result


class MeshQualityRunner:
    def __init__(self, max_non_orthogonality: float) -> None:
        self.max_non_orthogonality = max_non_orthogonality

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
        del budget
        del risk_report
        del protected_paths
        del execution_seconds_used
        case = Path(case_dir)
        log_dir = case / ".foampilot/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        steps = []
        for index, command in enumerate(commands, start=1):
            stdout = log_dir / f"{index:02d}-{command.step_id}.stdout.log"
            stderr = log_dir / f"{index:02d}-{command.step_id}.stderr.log"
            if command.executable == "checkMesh":
                stdout.write_text(
                    "points: 100\nfaces: 200\ncells: 80\n"
                    f"Mesh non-orthogonality Max: {self.max_non_orthogonality} "
                    "average: 1\nMax skewness = 0.5 OK.\nMesh OK.\n",
                    encoding="utf-8",
                )
            else:
                stdout.write_text("Time = 1\nEnd\n", encoding="utf-8")
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
                )
            )
        return PlanRunResult(
            case_dir=case,
            steps=steps,
            sandbox_probe=SandboxProbe(
                status="passed",
                ok=True,
                builder_sha256="a" * 64,
                namespace_flags=("--unshare-net",),
                mount_count=8,
                protected_path_count=0,
                return_code=0,
                detail="synthetic sandbox probe passed",
            ),
            execution_policy=ExecutionPolicyDecision(
                requested_isolation="sandbox_preferred",
                actual_backend="bubblewrap",
                allowed=True,
                code="SANDBOX_SELECTED",
            ),
        )


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(openfoam_root=Path("/opt/openfoam"))


def _agent(
    *,
    tmp_path: Path,
    model: RecordingModel,
    runner: SequencePlanRunner,
) -> NativeAgent:
    return NativeAgent(
        gateway=model,
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment("blockMesh", "icoFoam"),
        runner=runner,
    )


def test_native_agent_reaches_public_validation_pass(
    tmp_path: Path,
) -> None:
    plan = _plan()
    model = RecordingModel(
        [plan]
    )
    runner = SequencePlanRunner([(0, "Time = 1\nEnd\n", "")])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    run_dir = outcome.run_dir
    assert (run_dir / "task.yaml").is_file()
    assert (run_dir / "environment.json").is_file()
    assert (run_dir / "agent-context.json").is_file()
    assert (run_dir / "agent-status-author-01.json").is_file()
    capability = json.loads(
        (run_dir / "capability-profile.json").read_text(encoding="utf-8")
    )
    assert capability["solver_executable"] == "icoFoam"
    assert capability["confidence"] == "high"
    agent_context = json.loads(
        (run_dir / "agent-context.json").read_text(encoding="utf-8")
    )
    assert agent_context["knowledge_slots"]["solver_family_contract"]
    assert agent_context["skill_names"] == [
        "openfoam-author-native-case",
        "openfoam-incompressible-pressure-velocity",
    ]
    assert (run_dir / "authored-execution-plan.json").is_file()
    assert (run_dir / "plan-normalization.json").is_file()
    assert (run_dir / "execution-plan.json").is_file()
    assert json.loads(
        (run_dir / "execution-plan.json").read_text(encoding="utf-8")
    )["schema_version"] == 3
    assert (run_dir / "attempt-01/execution-plan.json").is_file()
    assert (
        run_dir / "attempt-01/case/system/controlDict"
    ).is_file()
    assert (
        run_dir / "attempt-01/public-validation.json"
    ).is_file()
    trace = run_dir / "attempt-01/generation-trace.json"
    assert trace.is_file()
    assert "deterministic_renderer" not in trace.read_text(encoding="utf-8")
    assert ArtifactStore(tmp_path / "runs").verify(run_dir) == []
    assert runner.calls == 1
    assert len(model.requests) == 1
    assert model.requests[0].context_artifacts[0].path == (
        "agent-status-author-01.json"
    )
    assert "DETERMINISTIC AGENT STATUS" in model.requests[0].user_prompt
    assert model.budgets[0].request_timeout_seconds == 420
    generation_deadline_remaining = (
        model.budgets[0].stage_deadline_monotonic - time.monotonic()
    )
    assert 479 <= generation_deadline_remaining <= 480
    assert not (run_dir / "draft-plan.json").exists()
    assert not (run_dir / "plan-review.json").exists()
    assert not (run_dir / "reviewed-plan.json").exists()
    workflow_events = (
        run_dir / "workflow-events.jsonl"
    ).read_text(encoding="utf-8")
    assert '"stage":"OPENFOAM_STEP_STARTED"' in workflow_events
    assert '"stage":"OPENFOAM_STEP_COMPLETE"' in workflow_events
    assert '"stage":"ROUTING_READY"' in workflow_events


def test_native_agent_uses_live_runner_events_without_replaying(
    tmp_path: Path,
) -> None:
    runner = LiveSequencePlanRunner([(0, "Time = 1\nEnd\n", "")])

    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_plan()]),
        runner=runner,
    ).solve(_task())

    events = [
        WorkflowEvent.model_validate_json(line)
        for line in (outcome.run_dir / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert sum(
        event.stage == WorkflowStage.OPENFOAM_STEP_STARTED for event in events
    ) == 1
    assert sum(
        event.stage == WorkflowStage.OPENFOAM_STEP_COMPLETE for event in events
    ) == 1
    activity_path = outcome.run_dir / "activity-events.jsonl"
    assert activity_path.is_file()
    activity = [
        json.loads(line)
        for line in activity_path.read_text(encoding="utf-8").splitlines()
    ]
    assert activity[0]["state"] == "started"
    assert activity[-1]["state"] == "completed"
    observability = json.loads(
        (outcome.run_dir / "observability.json").read_text(encoding="utf-8")
    )
    assert observability == {
        "schema_version": 1,
        "state": "ok",
        "diagnostics": [],
    }


def test_native_agent_freezes_runtime_and_execution_evidence(
    tmp_path: Path,
) -> None:
    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_plan()]),
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).solve(_task())

    assert json.loads(
        (outcome.run_dir / "runtime-config.json").read_text(encoding="utf-8")
    )["isolation"] == "sandbox_preferred"
    assert (outcome.run_dir / "runtime-config-provenance.json").is_file()
    assert (outcome.run_dir / "sandbox-probe.json").is_file()
    assert (outcome.run_dir / "execution-policy.json").is_file()
    attempt = outcome.run_dir / "attempt-01"
    assert (attempt / "execution-risk-report.json").is_file()
    assert (attempt / "sandbox-probe.json").is_file()
    assert (attempt / "execution-policy.json").is_file()
    store = ArtifactStore(outcome.run_dir.parent)
    assert store.verify(outcome.run_dir) == []
    manifest = json.loads(
        (outcome.run_dir / "artifact-manifest.json").read_text(
            encoding="utf-8"
        )
    )["files"]
    expected = {
        "runtime-config.json",
        "runtime-config-provenance.json",
        "sandbox-probe.json",
        "execution-policy.json",
        "attempt-01/execution-risk-report.json",
        "attempt-01/sandbox-probe.json",
        "attempt-01/execution-policy.json",
    }
    assert expected <= set(manifest)

    (attempt / "execution-policy.json").write_text(
        '{"mutated": true}\n',
        encoding="utf-8",
    )
    assert store.verify(outcome.run_dir) == [
        "hash mismatch: attempt-01/execution-policy.json"
    ]


class PolicyBlockedRunner:
    def __init__(self) -> None:
        self.calls = 0

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
        del (
            case_dir,
            commands,
            budget,
            risk_report,
            protected_paths,
            execution_seconds_used,
        )
        self.calls += 1
        probe = SandboxProbe(
            status="failed",
            ok=False,
            failure_code="NAMESPACE_UNAVAILABLE",
            return_code=1,
            detail="user namespaces are disabled",
        )
        decision = ExecutionPolicyDecision(
            requested_isolation="sandbox_preferred",
            actual_backend=None,
            allowed=False,
            code="HOST_DYNAMIC_CODE_BLOCKED",
            fallback_reason=probe.detail,
        )
        raise RuntimeExecutionError(decision, probe)


class SandboxSetupFailureRunner(SequencePlanRunner):
    def run(self, **kwargs):
        result = super().run(**kwargs)
        return result.model_copy(
            update={
                "failed_step_id": result.steps[0].step_id,
                "execution_error_code": "SANDBOX_SETUP_FAILED",
            }
        )


class WallBudgetFailureRunner(SequencePlanRunner):
    def run(self, **kwargs):
        result = super().run(**kwargs)
        return result.model_copy(
            update={
                "failed_step_id": result.steps[0].step_id,
                "timed_out": True,
                "execution_error_code": "EXECUTION_WALL_BUDGET_EXHAUSTED",
            }
        )


def test_native_agent_maps_policy_block_to_environment_without_repair(
    tmp_path: Path,
) -> None:
    runner = PolicyBlockedRunner()
    model = RecordingModel([_plan()])

    outcome = NativeAgent(
        gateway=model,
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment("blockMesh", "icoFoam"),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "BLOCKED_ENVIRONMENT"
    assert outcome.summary.attempts[-1].status == "BLOCKED_ENVIRONMENT"
    assert outcome.summary.primary_failure is not None
    assert outcome.summary.primary_failure.code == "HOST_DYNAMIC_CODE_BLOCKED"
    assert runner.calls == 1
    assert len(model.requests) == 1
    assert not (outcome.run_dir / "attempt-02").exists()


def test_native_agent_maps_runtime_sandbox_setup_failure_without_repair(
    tmp_path: Path,
) -> None:
    runner = SandboxSetupFailureRunner([(1, "", "bwrap: setup failed")])
    model = RecordingModel([_plan()])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "BLOCKED_ENVIRONMENT"
    assert outcome.summary.primary_failure is not None
    assert outcome.summary.primary_failure.code == "SANDBOX_SETUP_FAILED"
    assert runner.calls == 1
    assert len(model.requests) == 1
    assert not (outcome.run_dir / "attempt-02").exists()


def test_native_agent_maps_cumulative_wall_budget_to_workflow_failure(
    tmp_path: Path,
) -> None:
    runner = WallBudgetFailureRunner([(1, "", "deadline")])
    model = RecordingModel([_plan()])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "EXECUTION_WALL_BUDGET_EXHAUSTED"
    assert outcome.summary.attempts[-1].status == "EXECUTION_BUDGET_EXHAUSTED"
    assert outcome.summary.primary_failure is not None
    assert outcome.summary.primary_failure.domain == "workflow"
    assert (
        outcome.summary.primary_failure.code
        == "EXECUTION_WALL_BUDGET_EXHAUSTED"
    )
    assert runner.calls == 1
    assert len(model.requests) == 1
    assert not (outcome.run_dir / "attempt-02").exists()


def test_native_agent_probes_geometry_before_routing_and_generation(
    tmp_path: Path,
) -> None:
    payload = _task().model_dump(mode="json")
    payload["geometry"] = {
        "mode": "parametric",
        "dimensionality": "two_d",
        "description": "Unit cavity",
        "length_unit": "m",
        "parameters": {
            "length": {"value": 1.0, "unit": "m"},
            "height": {"value": 1.0, "unit": "m"},
        },
        "patch_roles": [
            {"name": "movingWall", "role": "wall"},
        ],
    }
    payload["mesh"] = {
        "strategy": "blockMesh",
        "quality": {"require_check_mesh_pass": False},
    }
    task = type(_task()).model_validate(payload)
    model = RecordingModel([_plan()])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).solve(task)

    facts = json.loads(
        (outcome.run_dir / "geometry-facts.json").read_text(encoding="utf-8")
    )
    assert facts["mode"] == "parametric"
    events = [
        json.loads(line)
        for line in (outcome.run_dir / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    stages = [item["stage"] for item in events]
    assert stages.index("GEOMETRY_READY") < stages.index("ROUTING_READY")
    assert stages.index("GEOMETRY_READY") < stages.index(
        "MODEL_GENERATION_STARTED"
    )
    assert "PUBLIC GEOMETRY FACTS" in model.requests[0].user_prompt


def test_native_agent_persists_mesh_quality_report(
    tmp_path: Path,
) -> None:
    payload = _task().model_dump(mode="json")
    payload["mesh"] = {
        "strategy": "blockMesh",
        "quality": {
            "require_check_mesh_pass": True,
            "max_non_orthogonality": 70,
        },
    }
    task = type(_task()).model_validate(payload)
    plan = _plan()
    plan.commands.insert(
        1,
        NativeCommand(
            step_id="check-mesh",
            stage="check",
            executable="checkMesh",
            timeout_seconds=10,
        ),
    )
    model = RecordingModel([plan])
    agent = NativeAgent(
        gateway=model,
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment("blockMesh", "checkMesh", "icoFoam"),
        runner=MeshQualityRunner(12.0),
    )

    outcome = agent.solve(task)

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    quality_path = outcome.run_dir / "attempt-01/mesh-quality-report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    assert quality["check_mesh_passed"] is True
    assert quality["max_non_orthogonality"] == 12.0
    assert quality["failed_requirements"] == []
    events = (outcome.run_dir / "workflow-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"stage":"MESH_QUALITY_COMPLETE"' in events


def test_mesh_quality_threshold_has_distinct_native_status(
    tmp_path: Path,
) -> None:
    payload = _task().model_dump(mode="json")
    payload["resource_budget"]["max_attempts"] = 1
    payload["mesh"] = {
        "strategy": "blockMesh",
        "quality": {
            "require_check_mesh_pass": True,
            "max_non_orthogonality": 70,
        },
    }
    task = type(_task()).model_validate(payload)
    plan = _plan()
    plan.commands.insert(
        1,
        NativeCommand(
            step_id="check-mesh",
            stage="check",
            executable="checkMesh",
            timeout_seconds=10,
        ),
    )
    agent = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment("blockMesh", "checkMesh", "icoFoam"),
        runner=MeshQualityRunner(82.0),
    )

    outcome = agent.solve(task)

    assert outcome.status == "MESH_QUALITY_FAILED"
    assert outcome.summary.primary_failure is not None
    assert outcome.summary.primary_failure.domain == "mesh"


def test_native_agent_normalizes_simple_mpi_launcher_before_policy(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan.commands[-1].executable = "mpirun"
    plan.commands[-1].args = ["-n", "1", "icoFoam", "-parallel"]
    model = RecordingModel([plan])
    runner = SequencePlanRunner([(0, "Time = 1\nEnd\n", "")])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    raw = json.loads(
        (outcome.run_dir / "authored-execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    normalized = json.loads(
        (outcome.run_dir / "execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    records = json.loads(
        (outcome.run_dir / "plan-normalization.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["commands"][-1]["executable"] == "mpirun"
    assert normalized["commands"][-1]["executable"] == "icoFoam"
    assert records[0]["original_launcher"] == "mpirun"


def test_native_agent_records_known_utility_stage_normalization(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan.commands.append(
        NativeCommand(
            step_id="post",
            stage="solve",
            executable="postProcess",
            args=["-func", "CourantNo"],
            timeout_seconds=10,
        )
    )
    agent = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment(
            "blockMesh",
            "icoFoam",
            "postProcess",
        ),
        runner=MeshQualityRunner(0.0),
    )

    outcome = agent.solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    raw = json.loads(
        (outcome.run_dir / "authored-execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    normalized = json.loads(
        (outcome.run_dir / "execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    records = json.loads(
        (outcome.run_dir / "plan-normalization.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["commands"][-1]["stage"] == "solve"
    assert normalized["commands"][-1]["stage"] == "postprocess"
    assert records[-1]["executable"] == "postProcess"
    assert records[-1]["normalized_stage"] == "postprocess"


def test_native_agent_rejects_incomplete_public_route_before_generation(
    tmp_path: Path,
) -> None:
    task = _task().model_copy(
        update={
            "prompt": "Calculate a requested field with OpenFOAM.",
        }
    )
    model = RecordingModel([])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=SequencePlanRunner([]),
    ).solve(task)

    assert outcome.summary.primary_failure is not None
    assert outcome.summary.primary_failure.code == "REQUEST_INCOMPLETE"
    assert (outcome.run_dir / "capability-profile.json").is_file()
    assert not (outcome.run_dir / "execution-plan.json").exists()
    assert model.requests == []


def test_native_agent_applies_one_evidence_scoped_repair(
    tmp_path: Path,
) -> None:
    plan = _plan()
    repair = RepairPatch(
        because="The solver log contains non-finite evidence.",
        evidence=["nan appears in the solve log"],
        file_operations=[
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": _control_dict(delta_t=0.001),
            }
        ],
        command_operations=[],
        expected_check="The solve log reaches End without nan.",
        stable_control="The mesh and boundaries remain unchanged.",
    )
    model = RecordingModel(
        [
            plan,
            repair,
        ]
    )
    runner = SequencePlanRunner(
        [
            (0, "Time = 0.1\nnan in U\n", ""),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert len(outcome.summary.attempts) == 2
    assert (
        outcome.run_dir
        / "attempt-02/case/system/controlDict"
    ).read_text() == _control_dict(delta_t=0.001)
    assert (
        outcome.run_dir / "repair-patch-attempt-01.json"
    ).is_file()
    assert (outcome.run_dir / "agent-status-repair-01.json").is_file()
    assert (
        outcome.run_dir / "failure-classification-attempt-01.json"
    ).is_file()
    assert (outcome.run_dir / "repair-scope-attempt-01.json").is_file()
    assert model.requests[1].context_artifacts[0].path == (
        "agent-status-repair-01.json"
    )
    assert '"current_stage": "repair"' in model.requests[1].user_prompt
    assert runner.calls == 2


def test_native_agent_recomputes_execution_risk_after_repair(
    tmp_path: Path,
) -> None:
    coded_control = (
        _control_dict(delta_t=0.001)
        + "#codeStream\n{\ncode #{ int generated = 1; #};\n}\n"
    )
    repair = RepairPatch(
        because="The first solver log contains non-finite evidence.",
        evidence=["nan appears in the solve log"],
        file_operations=[
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": coded_control,
            }
        ],
        command_operations=[],
        expected_check="The repaired solve reaches End.",
        stable_control="The mesh and boundaries remain unchanged.",
    )
    runner = SequencePlanRunner(
        [
            (0, "Time = 0.1\nnan in U\n", ""),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_plan(), repair]),
        runner=runner,
    ).solve(_task())

    first = json.loads(
        (outcome.run_dir / "attempt-01/execution-risk-report.json").read_text(
            encoding="utf-8"
        )
    )
    second = json.loads(
        (outcome.run_dir / "attempt-02/execution-risk-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert first["risk_level"] == "low"
    assert second["risk_level"] == "high"
    assert second["scanned_file_sha256"] != first["scanned_file_sha256"]
    assert [report.risk_level for report in runner.risk_reports] == ["low", "high"]


def test_native_agent_repairs_blocking_static_issue_before_execution(
    tmp_path: Path,
) -> None:
    bad_control = GeneratedFile(
        path="system/controlDict",
        content=(
            _control_dict()
            + """
functions
{
    extrema
    {
        type fieldMinMax;
        fields (U);
    }
}
"""
        ),
    )
    velocity = GeneratedFile(
        path="0/U",
        content="FoamFile { class volVectorField; object U; }\n",
    )
    plan = _plan(files=[bad_control, velocity])
    repair = RepairPatch(
        because="The static report identifies an unsupported function object.",
        evidence=["UNSUPPORTED_OF10_FUNCTION_OBJECT in system/controlDict"],
        file_operations=[
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": _control_dict(),
            }
        ],
        command_operations=[],
        expected_check="Static inspection accepts controlDict.",
        stable_control="The mesh, fields, and solver command remain unchanged.",
    )
    model = RecordingModel([plan, repair])
    runner = SequencePlanRunner([(0, "Time = 1\nEnd\n", "")])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert len(outcome.summary.attempts) == 2
    assert outcome.summary.attempts[0].status == "STATIC_INSPECTION_FAILED"
    assert runner.calls == 1
    assert (
        outcome.run_dir / "attempt-01/public-validation.json"
    ).is_file()
    assert (
        outcome.run_dir / "repair-patch-attempt-01.json"
    ).is_file()
    assert (
        outcome.run_dir / "attempt-02/case/system/controlDict"
    ).read_text(encoding="utf-8") == _control_dict()


def test_native_agent_repair_can_add_a_safe_required_dictionary(
    tmp_path: Path,
) -> None:
    plan = _plan()
    property_file = GeneratedFile(
        path="constant/physicalProperties.water",
        content=(
            "FoamFile { class dictionary; "
            "object physicalProperties.water; }\n"
            "viscosityModel constant;\nnu 1e-6;\nrho 1000;\n"
        ),
    )
    repair = RepairPatch(
        because="The solver reports a missing grouped phase dictionary.",
        evidence=["cannot find constant/physicalProperties.water"],
        file_operations=[
            {
                "operation": "add",
                "path": property_file.path,
                "content": property_file.content,
            }
        ],
        command_operations=[],
        expected_check="The solver opens the phase dictionary.",
        stable_control="Mesh, fields, and commands remain unchanged.",
    )
    model = RecordingModel([plan, repair])
    runner = SequencePlanRunner(
        [
            (1, "", "cannot find constant/physicalProperties.water"),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    assert (
        outcome.run_dir
        / "attempt-02/case/constant/physicalProperties.water"
    ).read_text() == property_file.content
    assert runner.calls == 2


def test_native_agent_repair_can_insert_missing_typed_command(
    tmp_path: Path,
) -> None:
    plan = _plan()
    repair = RepairPatch(
        because="The public log identifies a missing initialization step.",
        evidence=["required initialization command setFields is missing"],
        file_operations=[],
        command_operations=[
            {
                "operation": "insert_before",
                "anchor_step_id": "solve",
                "command": {
                    "step_id": "set-fields",
                    "stage": "initialize",
                    "executable": "setFields",
                    "args": [],
                    "mpi_ranks": 1,
                    "timeout_seconds": 10,
                },
            }
        ],
        expected_check="The initialization step runs before the solver.",
        stable_control="Case files and solver settings remain unchanged.",
    )
    runner = SequencePlanRunner(
        [
            (1, "", "required initialization command setFields is missing"),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )
    outcome = NativeAgent(
        gateway=RecordingModel([plan, repair]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment(
            "blockMesh", "setFields", "icoFoam"
        ),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    repaired_plan = json.loads(
        (outcome.run_dir / "attempt-02/execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["step_id"] for item in repaired_plan["commands"]] == [
        "mesh",
        "set-fields",
        "solve",
    ]


def test_native_agent_repair_can_remove_unsupported_typed_command(
    tmp_path: Path,
) -> None:
    plan = _plan().model_copy(deep=True)
    plan.commands.append(
        NativeCommand(
            step_id="optional-post",
            stage="postprocess",
            executable="postProcess",
            timeout_seconds=10,
        )
    )
    repair = RepairPatch(
        because="The optional postprocess command is unsupported.",
        evidence=["unsupported optional command is not required"],
        file_operations=[],
        command_operations=[
            {
                "operation": "remove",
                "target_step_id": "optional-post",
            }
        ],
        expected_check="The required solver path completes without the command.",
        stable_control="Case files and solver command remain unchanged.",
    )
    runner = SequencePlanRunner(
        [
            (1, "", "unsupported optional command post is not required"),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )
    outcome = NativeAgent(
        gateway=RecordingModel([plan, repair]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment(
            "blockMesh", "icoFoam", "postProcess"
        ),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "PUBLIC_VALIDATION_PASS"
    repaired_plan = json.loads(
        (outcome.run_dir / "attempt-02/execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert "optional-post" not in {
        item["step_id"] for item in repaired_plan["commands"]
    }


def test_native_agent_does_not_repair_environment_failure(
    tmp_path: Path,
) -> None:
    model = RecordingModel(
        [_plan()]
    )
    runner = SequencePlanRunner(
        [(1, "", "bwrap: namespace unavailable\n")]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "BLOCKED_ENVIRONMENT"
    assert len(outcome.summary.attempts) == 1
    assert runner.calls == 1
    assert model.replies == []


def _transport_failure() -> GatewayRequestError:
    return GatewayRequestError(
        failure=BackendError(
            kind=BackendFailureKind.NETWORK_UNAVAILABLE,
            backend_id="recording",
            model="transport-blocked",
            purpose="generation",
            detail="network unavailable",
            retryable=True,
        ),
        logical_request_id="blocked",
        transport_attempts=3,
        backend_switches=0,
        deadline_reason=None,
    )


def _schema_failure() -> GatewayRequestError:
    return GatewayRequestError(
        failure=BackendError(
            kind=BackendFailureKind.SCHEMA_INVALID,
            backend_id="recording",
            model="invalid-plan",
            purpose="generation",
            detail="backend output failed ExecutionPlan validation",
            retryable=False,
        ),
        logical_request_id="invalid",
        transport_attempts=2,
        backend_switches=0,
        deadline_reason=None,
    )


def test_native_agent_classifies_exhausted_model_transport_as_environment(
    tmp_path: Path,
) -> None:
    runner = SequencePlanRunner([])

    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_transport_failure()]),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "DEFERRED"
    assert outcome.summary.workflow_state == "DEFERRED"
    assert outcome.summary.native_status is None
    assert outcome.summary.primary_failure is None
    assert (
        outcome.summary.terminal_blocker.code
        == "NETWORK_UNAVAILABLE"
    )
    assert outcome.summary.terminal_blocker.message == "无法连接模型服务。"
    assert outcome.summary.terminal_blocker.recovery.endswith("。")
    assert outcome.summary.attempts == []
    assert runner.calls == 0


def test_native_agent_finalizes_user_cancel_without_repair(
    tmp_path: Path,
) -> None:
    runner = SequencePlanRunner([])

    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([OperationCancelled()]),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "CANCELLED"
    assert outcome.summary.workflow_state == "CANCELLED"
    assert outcome.summary.primary_failure is None
    assert outcome.summary.resume.allowed is False
    assert (outcome.run_dir / "cancellation.json").is_file()
    assert ArtifactStore(outcome.run_dir.parent).verify(outcome.run_dir) == []
    assert runner.calls == 0


def test_native_agent_classifies_unresolved_schema_as_generation_invalid(
    tmp_path: Path,
) -> None:
    runner = SequencePlanRunner([])

    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_schema_failure()]),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "GENERATION_INVALID"
    assert outcome.summary.workflow_state == "FAILED"
    assert outcome.summary.native_status is None
    assert outcome.summary.primary_failure.domain == "plan"
    assert outcome.summary.primary_failure.code == "GENERATION_INVALID"
    assert outcome.summary.terminal_blocker is None
    assert not outcome.summary.resume.allowed
    assert runner.calls == 0

    from foampilot.qualification.reporting import classify_qualification

    assert classify_qualification(outcome, [], []) == "FAIL_AGENT"


def test_solver_failure_survives_repair_backend_blocker(
    tmp_path: Path,
) -> None:
    model = RecordingModel([_plan(), _transport_failure()])
    runner = SequencePlanRunner(
        [(1, "", "FOAM FATAL ERROR: missing keyword")]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "SOLVER_FAILED"
    assert outcome.summary.workflow_state == "DEFERRED"
    assert outcome.summary.native_status == "SOLVER_FAILED"
    assert outcome.summary.primary_failure.domain == "solver"
    assert outcome.summary.primary_failure.step_id == "solve"
    assert outcome.summary.terminal_blocker.domain == "backend"
    assert (
        outcome.summary.terminal_blocker.code
        == "NETWORK_UNAVAILABLE"
    )
    assert outcome.summary.resume.allowed
    assert (
        outcome.summary.resume.from_stage
        == "MODEL_REPAIR_STARTED"
    )


def test_native_agent_preserves_invalid_plan_issues_without_json_crash(
    tmp_path: Path,
) -> None:
    invalid = _plan(application="unknownFoam")
    model = RecordingModel([invalid])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=SequencePlanRunner([]),
    ).solve(_task())

    assert outcome.status == "PLAN_INVALID"
    assert (outcome.run_dir / "execution-plan.json").is_file()
    assert (outcome.run_dir / "plan-issues.json").is_file()
    assert ArtifactStore(tmp_path / "runs").verify(outcome.run_dir) == []


def test_native_agent_adds_discovered_tutorial_to_execution_guards(
    tmp_path: Path,
) -> None:
    tutorial_root = tmp_path / "OpenFOAM-10/tutorials"
    environment = _environment("blockMesh", "icoFoam").model_copy(
        update={"tutorial_root": tutorial_root}
    )
    plan = _plan(
        files=[
            GeneratedFile(
                path="system/controlDict",
                content=(
                    "FoamFile\n{\n class dictionary;\n"
                    " object controlDict;\n}\n"
                    "application icoFoam;\n"
                    f'#include "{tutorial_root}/cavity/controlDict"\n'
                ),
            )
        ]
    )
    outcome = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=environment,
        runner=SequencePlanRunner([]),
    ).solve(_task().model_copy(update={"protected_paths": []}))

    assert outcome.status == "PLAN_INVALID"
    assert "PROTECTED_REFERENCE" in (
        outcome.run_dir / "plan-issues.json"
    ).read_text(encoding="utf-8")
    author_status = json.loads(
        (outcome.run_dir / "agent-status-author-01.json").read_text(
            encoding="utf-8"
        )
    )
    assert author_status["immutable_constraints"]["protected_path_count"] == 1


def test_runtime_tutorial_path_is_protected_before_model_authoring(
    tmp_path: Path,
) -> None:
    tutorial_root = tmp_path / "OpenFOAM-10/tutorials"
    environment = _environment("blockMesh", "icoFoam").model_copy(
        update={"tutorial_root": tutorial_root}
    )
    model = RecordingModel([_plan()])
    base_task = _task()
    task = base_task.model_copy(
        update={
            "prompt": (
                f"{base_task.prompt} Copy the case from "
                f"{tutorial_root}/cavity."
            )
        }
    )

    outcome = NativeAgent(
        gateway=model,
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=environment,
        runner=SequencePlanRunner([]),
    ).solve(task)

    assert outcome.status == "CASE_GENERATION_FAILED"
    assert model.requests == []
