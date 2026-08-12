from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.plans import NativeCommand
from foampilot.preprocessing import probe_provided_mesh
from foampilot.runtime import PlanRunResult, PlanStepResult, RuntimeConfig


class RecordingRunner:
    def __init__(self, case_root: Path) -> None:
        self.case_root = case_root
        self.commands: list[NativeCommand] = []

    def run(self, **kwargs) -> PlanRunResult:
        self.commands = list(kwargs["commands"])
        logs = self.case_root / ".foampilot/logs"
        logs.mkdir(parents=True)
        stdout = logs / "01-inspect-provided-mesh.stdout.log"
        stderr = logs / "01-inspect-provided-mesh.stderr.log"
        stdout.write_text(
            """Mesh stats
    points: 12
    faces: 11
    cells: 2
    Number of regions: 1 (OK).
Mesh OK.
""",
            encoding="utf-8",
        )
        stderr.write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return PlanRunResult(
            case_dir=self.case_root,
            steps=[
                PlanStepResult(
                    step_id="inspect-provided-mesh",
                    command=["/opt/OpenFOAM-10/bin/checkMesh"],
                    return_code=0,
                    started_at=now,
                    finished_at=now,
                    elapsed_seconds=0.01,
                    timed_out=False,
                    stdout_path=stdout,
                    stderr_path=stderr,
                    execution_backend="host",
                )
            ],
        )


def _runtime(tmp_path: Path) -> tuple[RuntimeConfig, EnvironmentSnapshot]:
    root = tmp_path / "OpenFOAM-10"
    config = RuntimeConfig(
        openfoam_root=root,
        isolation="trusted_host",
    )
    environment = EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=root,
        tutorial_root=None,
        workspace_root=tmp_path,
        workspace_writable=True,
        commands=[CommandFact(name="checkMesh", path=root / "bin/checkMesh")],
        mpi_launcher=None,
        gmsh=None,
        max_mpi_ranks=1,
    )
    return config, environment


def test_probe_owns_the_check_mesh_command(monkeypatch, tmp_path: Path) -> None:
    case_root = tmp_path / "case"
    (case_root / "constant/polyMesh").mkdir(parents=True)
    config, environment = _runtime(tmp_path)
    runner = RecordingRunner(case_root)
    monkeypatch.setattr(
        "foampilot.preprocessing.mesh_probe._build_runner",
        lambda **kwargs: runner,
    )

    facts = probe_provided_mesh(
        case_root,
        environment,
        config,
        budget_seconds=60,
    )

    assert runner.commands == [
        NativeCommand(
            step_id="inspect-provided-mesh",
            stage="check",
            executable="checkMesh",
            args=[],
            mpi_ranks=1,
            timeout_seconds=60,
        )
    ]
    assert facts.source == "pre_authoring_probe"
    assert facts.mesh_check.executed is True
    assert facts.mesh_check.mesh_ok is True
    assert facts.metrics.check_mesh_passed is True
    control = (case_root / "system/controlDict").read_text(encoding="utf-8")
    assert "object      controlDict;" in control


def test_probe_caps_command_timeout_to_sixty_seconds(
    monkeypatch,
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "case"
    (case_root / "constant/polyMesh").mkdir(parents=True)
    config, environment = _runtime(tmp_path)
    runner = RecordingRunner(case_root)
    monkeypatch.setattr(
        "foampilot.preprocessing.mesh_probe._build_runner",
        lambda **kwargs: runner,
    )

    probe_provided_mesh(
        case_root,
        environment,
        config,
        budget_seconds=120,
    )

    assert runner.commands[0].timeout_seconds == 60


def test_probe_delegates_log_meaning_to_evidence_extractor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "case"
    (case_root / "constant/polyMesh").mkdir(parents=True)
    config, environment = _runtime(tmp_path)
    runner = RecordingRunner(case_root)
    monkeypatch.setattr(
        "foampilot.preprocessing.mesh_probe._build_runner",
        lambda **kwargs: runner,
    )
    calls = []

    class RecordingExtractor:
        def extract(self, run_result, plan, root):
            calls.append((run_result, plan, root))
            from foampilot.evidence import (
                MeshCheckFact,
                RawCommandEvidence,
                RunFacts,
            )
            from hashlib import sha256

            stdout = run_result.steps[0].stdout_path
            stderr = run_result.steps[0].stderr_path
            stdout_hash = sha256(stdout.read_bytes()).hexdigest()
            stderr_hash = sha256(stderr.read_bytes()).hexdigest()
            step = run_result.steps[0]
            return RunFacts(
                run_id="probe",
                attempt=1,
                plan_sha256="a" * 64,
                extractor_identities={"test": "1"},
                raw_steps=(
                    RawCommandEvidence(
                        step_id=step.step_id,
                        stage="check",
                        executable="checkMesh",
                        argv=tuple(step.command),
                        return_code=step.return_code,
                        started_at=step.started_at,
                        finished_at=step.finished_at,
                        elapsed_seconds=step.elapsed_seconds,
                        timed_out=step.timed_out,
                        stdout_path=stdout.relative_to(root).as_posix(),
                        stderr_path=stderr.relative_to(root).as_posix(),
                        stdout_sha256=stdout_hash,
                        stderr_sha256=stderr_hash,
                        execution_backend="host",
                    ),
                ),
                mesh_checks=(
                    MeshCheckFact(
                        step_id=step.step_id,
                        executed=True,
                        mesh_ok=True,
                        cells=77,
                    ),
                ),
                source_sha256={
                    stdout.relative_to(root).as_posix(): stdout_hash,
                    stderr.relative_to(root).as_posix(): stderr_hash,
                },
            )

    monkeypatch.setattr(
        "foampilot.preprocessing.mesh_probe.EvidenceExtractorRegistry.first_party",
        lambda: type(
            "Registry",
            (),
            {"resolve": lambda self, distribution, version: RecordingExtractor()},
        )(),
    )

    facts = probe_provided_mesh(case_root, environment, config, 60)

    assert len(calls) == 1
    assert calls[0][2] == case_root.resolve()
    assert facts.metrics.cells == 77
