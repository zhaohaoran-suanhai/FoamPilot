"""System-owned dynamic inspection of an already staged native polyMesh."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from foampilot.environment import EnvironmentSnapshot
from foampilot.plans import NativeCommand
from foampilot.runtime import (
    PlanRunResult,
    PlanRunner,
    RuntimeConfig,
    scan_execution_risk,
)
from foampilot.runtime.protection import runtime_protected_paths
from foampilot.tasks import MeshIntent, ResourceBudget

from .mesh_quality import build_mesh_quality_report
from .models import ExecutedMeshFacts, MeshCheckFact


_CONTROL_DICT = """FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      controlDict;
}
application     checkMesh;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         0;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
"""


class _Runner(Protocol):
    def run(self, **kwargs: object) -> PlanRunResult: ...


def _build_runner(
    *,
    runtime_config: RuntimeConfig,
    environment: EnvironmentSnapshot,
) -> _Runner:
    return PlanRunner.from_runtime_config(
        runtime_config,
        {"checkMesh"},
        environment=environment,
        workspace_root=environment.workspace_root,
    )


def _relative_evidence(path: Path, case_root: Path) -> str:
    try:
        return path.resolve().relative_to(case_root).as_posix()
    except ValueError:
        return path.name


def probe_provided_mesh(
    case_root: Path,
    environment: EnvironmentSnapshot,
    runtime_config: RuntimeConfig,
    budget_seconds: int,
) -> ExecutedMeshFacts:
    """Run one fixed, serial ``checkMesh`` before any model authoring."""

    if budget_seconds < 1:
        raise ValueError("mesh probe budget must be positive")
    case = case_root.resolve()
    workspace = environment.workspace_root.resolve()
    if not case.is_relative_to(workspace):
        raise ValueError("mesh probe case is outside the runtime workspace")
    if not (case / "constant/polyMesh").is_dir():
        raise ValueError("provided polyMesh is not staged")
    control_dict = case / "system/controlDict"
    if control_dict.exists():
        raise ValueError("mesh probe controlDict target already exists")
    control_dict.parent.mkdir(parents=True, exist_ok=True)
    control_dict.write_text(_CONTROL_DICT, encoding="utf-8")

    timeout_seconds = min(budget_seconds, 60)
    command = NativeCommand(
        step_id="inspect-provided-mesh",
        stage="check",
        executable="checkMesh",
        args=[],
        mpi_ranks=1,
        timeout_seconds=timeout_seconds,
    )
    risk = scan_execution_risk(
        case,
        openfoam_root=runtime_config.openfoam_root,
        trusted_readonly_roots=runtime_config.trusted_readonly_roots,
        commands=(command,),
    )
    protected_paths = runtime_protected_paths((), environment)
    runner = _build_runner(
        runtime_config=runtime_config,
        environment=environment,
    )
    result = runner.run(
        case_dir=case,
        commands=(command,),
        budget=ResourceBudget(
            max_attempts=1,
            max_wall_seconds=budget_seconds,
            max_mpi_ranks=1,
            memory_mib=256,
        ),
        risk_report=risk,
        protected_paths=protected_paths,
    )
    metrics = build_mesh_quality_report(
        result,
        MeshIntent(
            strategy="provided",
            quality={"require_check_mesh_pass": True},
        ),
    )
    step = next(
        (
            item
            for item in result.steps
            if item.step_id == "inspect-provided-mesh"
        ),
        None,
    )
    if step is None:
        mesh_check = MeshCheckFact(
            executed=False,
            executable_identity=str(
                next(
                    item.path
                    for item in environment.commands
                    if item.name == "checkMesh"
                ).resolve()
            ),
            return_code=None,
            timed_out=result.timed_out,
            mesh_ok=None,
            evidence_paths=metrics.evidence_files,
        )
    else:
        mesh_check = MeshCheckFact(
            executed=True,
            executable_identity=str(Path(step.command[0]).resolve()),
            return_code=step.return_code,
            timed_out=step.timed_out,
            mesh_ok=metrics.check_mesh_passed,
            evidence_paths=tuple(
                _relative_evidence(path, case)
                for path in (step.stdout_path, step.stderr_path)
            ),
        )
    return ExecutedMeshFacts(mesh_check=mesh_check, metrics=metrics)


__all__ = ["probe_provided_mesh"]
