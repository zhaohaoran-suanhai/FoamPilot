from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from foampilot.agent import NativeAgent
from foampilot.agent.repair import RepairDecision
from foampilot.artifacts import ArtifactStore
from foampilot.models import TransportError
from foampilot.plans import GeneratedFile
from foampilot.runtime import (
    PlanRunResult,
    PlanStepResult,
    RuntimeConfig,
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

    def run(self, *, case_dir, commands, budget):
        del budget
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
        )


def _runtime_config() -> RuntimeConfig:
    root = Path("/home/edwin/workplace/OpenFOAM-10")
    return RuntimeConfig(
        openfoam_root=root,
        tutorial_root=root / "tutorials",
        python_executable=Path("/home/edwin/feal-venv-py312/bin/python"),
        bubblewrap=Path("/usr/local/bin/bwrap"),
    )


def _agent(
    *,
    tmp_path: Path,
    model: RecordingModel,
    runner: SequencePlanRunner,
) -> NativeAgent:
    return NativeAgent(
        model=model,
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
    assert (run_dir / "execution-plan.json").is_file()
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
    assert not (run_dir / "draft-plan.json").exists()
    assert not (run_dir / "plan-review.json").exists()
    assert not (run_dir / "reviewed-plan.json").exists()


def test_native_agent_applies_one_evidence_scoped_repair(
    tmp_path: Path,
) -> None:
    plan = _plan()
    repair = RepairDecision(
        because="The solver log contains non-finite evidence.",
        evidence=["nan appears in the solve log"],
        cause="The initial time step is too large.",
        changed_files=[
            GeneratedFile(
                path="system/controlDict",
                content=_control_dict(delta_t=0.001),
            )
        ],
        changed_commands=[],
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
        outcome.run_dir / "attempt-01/repair-decision.json"
    ).is_file()
    assert runner.calls == 2


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
    repair = RepairDecision(
        because="The static report identifies an unsupported function object.",
        evidence=["UNSUPPORTED_OF10_FUNCTION_OBJECT in system/controlDict"],
        cause="fieldMinMax is unavailable in Foundation OpenFOAM v10.",
        changed_files=[
            GeneratedFile(
                path="system/controlDict",
                content=_control_dict(),
            )
        ],
        changed_commands=[],
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
        outcome.run_dir / "attempt-01/repair-decision.json"
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
    repair = RepairDecision(
        because="The solver reports a missing grouped phase dictionary.",
        evidence=["cannot find constant/physicalProperties.water"],
        cause="The required grouped phase dictionary is absent.",
        changed_files=[property_file],
        changed_commands=[],
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


class TransportBlockedModel:
    model = "transport-blocked"

    def generate_structured(self, request, schema):
        del request, schema
        raise TransportError("network unavailable")


def test_native_agent_classifies_exhausted_model_transport_as_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def blocked(*args, **kwargs):
        del args, kwargs
        raise TransportError("network unavailable")

    monkeypatch.setattr(
        "foampilot.agent.generation.generate_with_retry",
        blocked,
    )
    runner = SequencePlanRunner([])

    outcome = _agent(
        tmp_path=tmp_path,
        model=TransportBlockedModel(),
        runner=runner,
    ).solve(_task())

    assert outcome.status == "BLOCKED_ENVIRONMENT"
    assert outcome.summary.attempts == []
    assert runner.calls == 0


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
