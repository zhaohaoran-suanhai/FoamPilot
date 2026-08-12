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
