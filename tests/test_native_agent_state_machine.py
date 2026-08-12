from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import pytest

from foampilot.activity import OperationCancelled
from foampilot.agent import NativeAgent
from foampilot.repair import RepairProposal
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
from foampilot.assets import BundleMember, compute_bundle_manifest_sha256
from foampilot.manifests import CasePatch
from foampilot.preprocessing import ExecutedMeshFacts, MeshCheckFact, MeshQualityReport
from foampilot.simulation import (
    FactEvidence,
    ResolvedValue,
    SimulationIntent,
)
from foampilot.acceptance import AcceptanceRequest, AcceptanceScope
from foampilot.observations import (
    ObservationRequest,
    ObservationScope,
    TimeSelection,
)
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
from foampilot.tasks import PublicAsset
from tests.support.tasks import replace_explicit_fact


POLY_MESH_FIXTURE = Path(__file__).parent / "fixtures/poly_mesh/minimal"


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


class AcceptanceIntentModel(RecordingModel):
    def generate_structured(
        self,
        request,
        schema,
        *,
        budget,
        trace,
        output_normalizer=None,
    ):
        if schema is SimulationIntent:
            observation = ObservationRequest(
                observation_id="continuity",
                kind="continuity",
                quantity="continuity",
                dimension="1",
                scope=ObservationScope(kind="global"),
                time_selection=TimeSelection(kind="latest"),
                provenance=(
                    FactEvidence(
                        kind="user_quote",
                        detail="continuity <= 1e-5",
                    ),
                ),
            )
            self.all_requests.append(request)
            return __import__(
                "foampilot.models", fromlist=["ModelResult"]
            ).ModelResult(
                value=SimulationIntent(
                    facts=(
                        ResolvedValue(
                            field_path="solver.family",
                            value="icoFoam",
                            source="user_text",
                            impact="low",
                            evidence=(
                                FactEvidence(
                                    kind="user_quote",
                                    detail="icoFoam",
                                ),
                            ),
                            confirmed=True,
                        ),
                    ),
                    observation_requests=(observation,),
                    acceptance_requests=(
                        AcceptanceRequest(
                            condition_id="continuity-limit",
                            observation=observation,
                            operator="less_equal",
                            limit=1.0e-5,
                            unit="1",
                            scope=AcceptanceScope(time="latest"),
                            source="user_text",
                            confirmed=True,
                            provenance=(
                                FactEvidence(
                                    kind="user_quote",
                                    detail="continuity <= 1e-5",
                                ),
                            ),
                        ),
                    ),
                ),
                logical_request_id="acceptance-intent",
                backend_id=self.primary_backend_id,
                model=self.primary_model,
                transport_attempts=1,
                backend_switches=0,
                elapsed_seconds=0,
            )
        return super().generate_structured(
            request,
            schema,
            budget=budget,
            trace=trace,
            output_normalizer=output_normalizer,
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


class ProvidedMeshRunner(SequencePlanRunner):
    def __init__(self) -> None:
        super().__init__([(0, "Time = 1\nEnd\n", "")])
        self.probe_calls = 0

    def probe_provided_mesh(self, **kwargs) -> ExecutedMeshFacts:
        self.probe_calls += 1
        case = Path(kwargs["case_root"])
        assert (case / "constant/polyMesh/cellZones").is_file()
        metrics = MeshQualityReport(
            strategy="provided",
            commands_completed=("inspect-provided-mesh",),
            mesh_created=True,
            check_mesh_passed=True,
            cells=2,
            faces=11,
            points=12,
            regions=1,
            patches=("inlet", "outlet", "top", "bottom", "frontAndBack"),
            failed_requirements=(),
            warnings=(),
            evidence_files=(".foampilot/logs/check.log",),
        )
        return ExecutedMeshFacts(
            mesh_check=MeshCheckFact(
                executed=True,
                executable_identity="synthetic-checkMesh",
                return_code=0,
                timed_out=False,
                mesh_ok=True,
                evidence_paths=(".foampilot/logs/check.log",),
            ),
            metrics=metrics,
        )


def _provided_asset(root: Path) -> PublicAsset:
    members = tuple(
        BundleMember(
            relative_path=path.relative_to(POLY_MESH_FIXTURE).as_posix(),
            logical_name=path.relative_to(POLY_MESH_FIXTURE).as_posix(),
            sha256=sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )
        for path in sorted(POLY_MESH_FIXTURE.rglob("*"))
        if path.is_file()
    )
    values = {
        "adapter_id": "foampilot.asset.openfoam-poly-mesh",
        "kind": "openfoam_poly_mesh",
        "source_path": "mesh/native",
        "install_path": "constant/polyMesh",
        "region": None,
        "members": members,
    }
    manifest = compute_bundle_manifest_sha256(**values)
    return PublicAsset(
        path="mesh/native",
        sha256=manifest,
        purpose="provided native mesh",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256=manifest,
    )


def _provided_task(root: Path):
    payload = _task().model_dump(mode="json")
    payload["public_assets"] = [_provided_asset(root).model_dump(mode="json")]
    replace_explicit_fact(payload, "geometry.input", {
        "mode": "openfoam_mesh",
        "dimensionality": "three_d",
        "description": "synthetic native mesh",
        "length_unit": "m",
        "assets": [
            {
                "path": "mesh/native",
                "format": "openfoam_mesh",
                "role": "poly_mesh_bundle",
            }
        ],
        "patch_roles": [],
        "region_roles": [],
    })
    replace_explicit_fact(payload, "mesh.intent", {"strategy": "provided"})
    return _task().model_validate(payload)


def _provided_plan():
    plan = _plan()
    manifest = plan.manifest.model_copy(
        update={
            "mesh_family": "provided",
            "patches": [
                CasePatch(name=name, region="default", mesh_type=patch_type)
                for name, patch_type in (
                    ("inlet", "patch"),
                    ("outlet", "patch"),
                    ("top", "symmetryPlane"),
                    ("bottom", "symmetryPlane"),
                    ("frontAndBack", "empty"),
                )
            ],
        }
    )
    return plan.model_copy(
        update={
            "manifest": manifest,
            "files": [
                item
                for item in plan.files
                if item.path != "system/blockMeshDict"
            ],
            "commands": [plan.commands[-1]],
        }
    )


def test_provided_mesh_facts_exist_before_first_model_call(
    tmp_path: Path,
) -> None:
    public_root = tmp_path / "public"
    import shutil

    shutil.copytree(POLY_MESH_FIXTURE, public_root / "mesh/native")
    runner = ProvidedMeshRunner()
    model = RecordingModel([_provided_plan()])
    agent = NativeAgent(
        gateway=model,
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment("checkMesh", "icoFoam"),
        runner=runner,
    )

    outcome = agent.solve(
        _provided_task(public_root),
        public_asset_root=public_root,
    )

    assert outcome.summary.native_status == "RUN_COMPLETED"
    assert runner.probe_calls == 1
    assert (outcome.run_dir / "asset-bundles.json").is_file()
    assert (outcome.run_dir / "input-mesh-facts.json").is_file()
    assert (outcome.run_dir / "pre-authoring-mesh-facts.json").is_file()
    assert "authoritative_input_mesh_facts" in model.requests[0].user_prompt
    assert "points\n(" not in model.requests[0].user_prompt


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
        environment_snapshot=_environment("blockMesh", "checkMesh", "icoFoam"),
        runner=runner,
    )


def test_native_agent_reaches_run_completed(
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

    assert outcome.status == "RUN_COMPLETED"
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
    assert (run_dir / "case-bundle.json").is_file()
    assert (run_dir / "design-conformance.json").is_file()
    assert (run_dir / "compiled-execution-plan.json").is_file()
    assert (run_dir / "plan-normalization.json").is_file()
    assert (run_dir / "execution-plan.json").is_file()
    assert json.loads(
        (run_dir / "execution-plan.json").read_text(encoding="utf-8")
    )["schema_version"] == 4
    assert (run_dir / "attempt-01/execution-plan.json").is_file()
    assert (
        run_dir / "attempt-01/case/system/controlDict"
    ).is_file()
    assert (run_dir / "attempt-01/run-assessment.json").is_file()
    assert not (run_dir / "attempt-01/public-validation.json").exists()
    for name in (
        "acceptance-plan.json",
        "observation-plan.json",
        "derived-metrics.json",
        "result-report.json",
    ):
        assert (run_dir / name).is_file()
    assert (run_dir / "attempt-01/derived-metrics.json").is_file()
    result = json.loads(
        (run_dir / "attempt-01/result-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["verdict"] == "NOT_REQUESTED"
    trace = run_dir / "attempt-01/generation-trace.json"
    assert trace.is_file()
    assert "deterministic_renderer" not in trace.read_text(encoding="utf-8")
    assert ArtifactStore(tmp_path / "runs").verify(run_dir) == []
    assert runner.calls == 1
    assert len(model.requests) == 1
    assert model.requests[0].context_artifacts == ()
    assert "frozen_case_design" in model.requests[0].user_prompt
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
    stage_names = [
        json.loads(line)["stage"]
        for line in workflow_events.splitlines()
    ]
    assert stage_names.index("ACCEPTANCE_COMPILED") < stage_names.index(
        "OBSERVATION_PLANNED"
    )
    assert stage_names.index("OBSERVATION_PLANNED") < stage_names.index(
        "CASE_AUTHORED"
    )
    assert stage_names.index("EXTRACTING_EVIDENCE") < stage_names.index(
        "RUN_ASSESSED"
    )
    assert stage_names.index("RUN_ASSESSED") < stage_names.index(
        "POSTPROCESSED"
    )
    assert stage_names.index("POSTPROCESSED") < stage_names.index(
        "ACCEPTANCE_EVALUATED"
    )
    assert '"observation_plan"' in model.requests[0].user_prompt


def test_ready_design_is_frozen_before_case_author_call(
    tmp_path: Path,
) -> None:
    model = RecordingModel([_plan()])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).solve(_task())

    assert outcome.status == "RUN_COMPLETED"
    for name in (
        "simulation-intent.json",
        "resolved-requirements.json",
        "case-design-proposal.json",
        "risk-decision.json",
        "case-design.json",
    ):
        assert (outcome.run_dir / name).is_file()
    assert [item.purpose for item in model.all_requests[:3]] == [
        "interpret-simulation-intent",
        "design-openfoam-case",
        "author-openfoam-case",
    ]
    assert "frozen_case_design" in model.requests[0].user_prompt


def test_non_empty_acceptance_contract_is_evaluated_from_run_facts(
    tmp_path: Path,
) -> None:
    model = AcceptanceIntentModel([_plan()])
    runner = SequencePlanRunner(
        [
            (
                0,
                "Time = 1\n"
                "PCG: Solving for p, Initial residual = 0.2, "
                "Final residual = 0.002, No Iterations 3\n"
                "time step continuity errors : sum local = 1e-08, "
                "global = -2e-09, cumulative = 3e-08\nEnd\n",
                "",
            )
        ]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    result = json.loads(
        (outcome.run_dir / "result-report.json").read_text(
            encoding="utf-8"
        )
    )
    metrics = json.loads(
        (outcome.run_dir / "derived-metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert outcome.status == "RUN_COMPLETED"
    assert result["verdict"] == "PASS"
    assert result["conditions"][0]["status"] == "PASS"
    assert result["conditions"][0]["observed_value"] == pytest.approx(3e-8)
    assert metrics["series"][0]["observation_id"] == "continuity"
    assert metrics["series"][0]["samples"][0]["value"] == pytest.approx(3e-8)
    author_payload = json.loads(model.requests[0].user_prompt)
    assert author_payload["observation_plan"]["items"][0][
        "required_for_condition_ids"
    ] == ["continuity-limit"]


def test_failed_acceptance_is_separate_from_successful_execution(
    tmp_path: Path,
) -> None:
    model = AcceptanceIntentModel([_plan()])
    runner = SequencePlanRunner(
        [
            (
                0,
                "Time = 1\n"
                "time step continuity errors : sum local = 1e-04, "
                "global = -2e-04, cumulative = 3e-04\nEnd\n",
                "",
            )
        ]
    )

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assessment = json.loads(
        (outcome.run_dir / "attempt-01/run-assessment.json").read_text(
            encoding="utf-8"
        )
    )
    result = json.loads(
        (outcome.run_dir / "result-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert outcome.status == "ACCEPTANCE_FAILED"
    assert outcome.summary.workflow_state == "FAILED"
    assert assessment["ok"] is True
    assert result["verdict"] == "FAIL"
    assert result["conditions"][0]["status"] == "FAIL"
    assert runner.calls == 1
    assert not (outcome.run_dir / "repair-proposal-attempt-01.json").exists()


class ConfirmationDesignModel(RecordingModel):
    def generate_structured(
        self,
        request,
        schema,
        *,
        budget,
        trace,
        output_normalizer=None,
    ):
        if schema is CaseDesignProposal:
            self.all_requests.append(request)
            return __import__("foampilot.models", fromlist=["ModelResult"]).ModelResult(
                value=CaseDesignProposal(
                    solver_family=ResolvedValue(
                        field_path="solver.family",
                        value="icoFoam",
                        source="user_text",
                        impact="high",
                        evidence=(
                            FactEvidence(kind="user_quote", detail="icoFoam"),
                        ),
                        confirmed=True,
                    ),
                    physical_models=(),
                    materials=(
                        ResolvedValue(
                            field_path="materials.fluid.nu",
                            value={"value": 1e-6, "unit": "m2/s"},
                            source="model_inference",
                            impact="high",
                            evidence=(
                                FactEvidence(
                                    kind="model_reason",
                                    detail="water-like candidate",
                                ),
                            ),
                            confirmed=False,
                        ),
                    ),
                    boundary_designs=(),
                    initial_conditions=(),
                    time_design=(),
                    numerical_design=(),
                    region_models=(),
                    extension_decisions=(
                        ExtensionDecision(
                            extension_id="foampilot.bridge.solver.icofoam",
                            schema_version=1,
                            values=(),
                            provenance=(
                                FactEvidence(
                                    kind="model_reason",
                                    detail="registered bridge capability",
                                ),
                            ),
                        ),
                    ),
                    uncertainties=(),
                    alternatives=(),
                    reasoning_evidence=(
                        FactEvidence(
                            kind="model_reason",
                            detail="candidate design",
                        ),
                    ),
                    capability_conflicts=(),
                ),
                logical_request_id="design-confirmation",
                backend_id=self.primary_backend_id,
                model=self.primary_model,
                transport_attempts=1,
                backend_switches=0,
                elapsed_seconds=0,
            )
        return super().generate_structured(
            request,
            schema,
            budget=budget,
            trace=trace,
            output_normalizer=output_normalizer,
        )


def test_confirmation_required_makes_zero_author_calls(
    tmp_path: Path,
) -> None:
    model = ConfirmationDesignModel([_plan()])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=SequencePlanRunner([]),
    ).solve(_task())

    assert outcome.status == "CONFIRMATION_REQUIRED"
    assert outcome.summary.workflow_state == "DEFERRED"
    assert outcome.summary.primary_failure is not None
    assert outcome.summary.primary_failure.code == "CONFIRMATION_REQUIRED"
    assert [item.purpose for item in model.all_requests] == [
        "interpret-simulation-intent",
        "design-openfoam-case",
    ]
    assert model.requests == []
    assert (outcome.run_dir / "questions.json").is_file()
    assert not (outcome.run_dir / "case-design.json").exists()
    assert not list(outcome.run_dir.glob("attempt-*"))


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
        environment_snapshot=_environment("blockMesh", "checkMesh", "icoFoam"),
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
    replace_explicit_fact(payload, "geometry.input", {
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
    })
    replace_explicit_fact(payload, "mesh.intent", {
        "strategy": "blockMesh",
        "quality": {"require_check_mesh_pass": False},
    })
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
    assert "authoritative_geometry_facts" in model.requests[0].user_prompt


def test_native_agent_persists_mesh_quality_report(
    tmp_path: Path,
) -> None:
    payload = _task().model_dump(mode="json")
    replace_explicit_fact(payload, "mesh.intent", {
        "strategy": "blockMesh",
        "quality": {
            "require_check_mesh_pass": True,
            "max_non_orthogonality": 70,
        },
    })
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

    assert outcome.status == "RUN_COMPLETED"
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
    replace_explicit_fact(payload, "mesh.intent", {
        "strategy": "blockMesh",
        "quality": {
            "require_check_mesh_pass": True,
            "max_non_orthogonality": 70,
        },
    })
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


def test_native_agent_ignores_model_authored_mpi_launcher(
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

    assert outcome.status == "RUN_COMPLETED"
    bundle = json.loads(
        (outcome.run_dir / "case-bundle.json").read_text(
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
    assert "commands" not in bundle
    assert normalized["commands"][-1]["executable"] == "icoFoam"
    assert records == []


def test_native_agent_ignores_model_authored_optional_utility(
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
            "checkMesh",
            "icoFoam",
            "postProcess",
        ),
        runner=MeshQualityRunner(0.0),
    )

    outcome = agent.solve(_task())

    assert outcome.status == "RUN_COMPLETED"
    bundle = json.loads(
        (outcome.run_dir / "case-bundle.json").read_text(
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
    assert "commands" not in bundle
    assert "postProcess" not in {
        item["executable"] for item in normalized["commands"]
    }
    assert records == []


def test_native_agent_rejects_incomplete_public_route_before_generation(
    tmp_path: Path,
) -> None:
    task = _task().model_copy(
        update={
            "request_text": "Calculate a requested field with OpenFOAM.",
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
    repair = RepairProposal(
        category="numerical",
        because="The solver log contains non-finite evidence.",
        design_changes=(
            {
                "field_path": "numerics.delta_t",
                "old_value": 0.01,
                "new_value": 0.001,
                "operator": "replace",
            },
        ),
        file_operations=(
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": _control_dict(delta_t=0.001),
            },
        ),
        expected_checks=("The solve log reaches End without nan.",),
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

    assert outcome.status == "RUN_COMPLETED"
    assert len(outcome.summary.attempts) == 2
    assert (
        outcome.run_dir
        / "attempt-02/case/system/controlDict"
    ).read_text() == _control_dict(delta_t=0.001)
    assert (
        outcome.run_dir / "repair-proposal-attempt-01.json"
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


def test_native_agent_plan_only_compiles_without_materializing_or_running(
    tmp_path: Path,
) -> None:
    runner = SequencePlanRunner([])
    outcome = _agent(
        tmp_path=tmp_path,
        model=RecordingModel([_plan()]),
        runner=runner,
    ).plan(_task())

    assert outcome.summary.workflow_state == "COMPLETED"
    assert outcome.summary.primary_failure is None
    assert runner.calls == 0
    assert (outcome.run_dir / "case-design.json").is_file()
    assert (outcome.run_dir / "case-bundle.json").is_file()
    assert (outcome.run_dir / "design-conformance.json").is_file()
    compiled = json.loads(
        (outcome.run_dir / "execution-plan.json").read_text(encoding="utf-8")
    )
    assert compiled["schema_version"] == 4
    assert compiled["compiler_identities"]
    assert not list(outcome.run_dir.glob("attempt-*"))


def test_native_agent_rejects_undeclared_dynamic_code_during_repair(
    tmp_path: Path,
) -> None:
    coded_control = (
        _control_dict(delta_t=0.001)
        + "#codeStream\n{\ncode #{ int generated = 1; #};\n}\n"
    )
    repair = RepairProposal(
        category="numerical",
        because="The first solver log contains non-finite evidence.",
        design_changes=(
            {
                "field_path": "numerics.delta_t",
                "old_value": 0.01,
                "new_value": 0.001,
                "operator": "replace",
            },
        ),
        file_operations=(
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": coded_control,
            },
        ),
        expected_checks=("The repaired solve reaches End.",),
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

    assert outcome.status == "SOLVER_FAILED"
    assert "UNDECLARED_SEMANTIC_CHANGE" in outcome.summary.message
    assert not (outcome.run_dir / "attempt-02/execution-plan.json").exists()
    assert [report.risk_level for report in runner.risk_reports] == ["low"]


def test_native_agent_does_not_model_repair_blocking_static_issue(
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
    model = RecordingModel([plan])
    runner = SequencePlanRunner([(0, "Time = 1\nEnd\n", "")])

    outcome = _agent(
        tmp_path=tmp_path,
        model=model,
        runner=runner,
    ).solve(_task())

    assert outcome.status == "STATIC_INSPECTION_FAILED"
    assert len(outcome.summary.attempts) == 1
    assert outcome.summary.attempts[0].status == "STATIC_INSPECTION_FAILED"
    assert outcome.summary.terminal_blocker is not None
    assert (
        outcome.summary.terminal_blocker.code
        == "AUTOMATIC_REPAIR_NOT_AUTHORIZED"
    )
    assert runner.calls == 0
    assert (
        outcome.run_dir / "attempt-01/run-assessment.json"
    ).is_file()
    assert not (
        outcome.run_dir / "attempt-01/public-validation.json"
    ).exists()
    assert not (
        outcome.run_dir / "repair-proposal-attempt-01.json"
    ).exists()
    assert not (outcome.run_dir / "attempt-02").exists()


def test_native_agent_repair_cannot_invent_missing_physics_dictionary(
    tmp_path: Path,
) -> None:
    plan = _plan()
    model = RecordingModel([plan])
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

    assert outcome.status == "SOLVER_FAILED"
    assert outcome.summary.terminal_blocker is not None
    assert (
        outcome.summary.terminal_blocker.code
        == "AUTOMATIC_REPAIR_NOT_AUTHORIZED"
    )
    assert not (outcome.run_dir / "attempt-02").exists()
    assert runner.calls == 1


def test_native_agent_repair_cannot_insert_missing_typed_command(
    tmp_path: Path,
) -> None:
    plan = _plan()
    runner = SequencePlanRunner(
        [
            (1, "", "required initialization command setFields is missing"),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )
    outcome = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment(
            "blockMesh", "checkMesh", "setFields", "icoFoam"
        ),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "SOLVER_FAILED"
    assert outcome.summary.terminal_blocker is not None
    assert (
        outcome.summary.terminal_blocker.code
        == "AUTOMATIC_REPAIR_NOT_AUTHORIZED"
    )
    assert not (outcome.run_dir / "attempt-02/execution-plan.json").exists()


def test_native_agent_repair_cannot_remove_typed_command(
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
    runner = SequencePlanRunner(
        [
            (1, "", "unsupported optional command post is not required"),
            (0, "Time = 1\nEnd\n", ""),
        ]
    )
    outcome = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=_environment(
            "blockMesh", "checkMesh", "icoFoam", "postProcess"
        ),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "SOLVER_FAILED"
    assert outcome.summary.terminal_blocker is not None
    assert (
        outcome.summary.terminal_blocker.code
        == "AUTOMATIC_REPAIR_NOT_AUTHORIZED"
    )


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
        [(1, "", "Courant number 10\nfloating point exception")]
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

    assert outcome.status == "CASE_DESIGN_CONTRADICTED"
    assert (outcome.run_dir / "case-design.json").is_file()
    assert (outcome.run_dir / "authoring-error.json").is_file()
    assert not (outcome.run_dir / "execution-plan.json").exists()
    assert ArtifactStore(tmp_path / "runs").verify(outcome.run_dir) == []


def test_native_agent_adds_discovered_tutorial_to_execution_guards(
    tmp_path: Path,
) -> None:
    tutorial_root = tmp_path / "OpenFOAM-10/tutorials"
    environment = _environment("blockMesh", "checkMesh", "icoFoam").model_copy(
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

    assert outcome.status == "CASE_DESIGN_CONTRADICTED"
    assert "AUTHOR_PROTECTED_PATH_LEAK" in (
        outcome.run_dir / "authoring-error.json"
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
    environment = _environment("blockMesh", "checkMesh", "icoFoam").model_copy(
        update={"tutorial_root": tutorial_root}
    )
    model = RecordingModel([_plan()])
    base_task = _task()
    task = base_task.model_copy(
        update={
            "request_text": (
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
