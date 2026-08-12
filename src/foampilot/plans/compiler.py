"""Compile one immutable ExecutionPlan v4 from trusted contributors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foampilot.environment import EnvironmentSnapshot
from foampilot.extensions import CapabilityRegistry, PlanContext
from foampilot.simulation import CaseDesign
from foampilot.tasks import TaskSpec

from .models import ExecutionPlan
from .validation import validate_execution_plan

if TYPE_CHECKING:
    from foampilot.authoring import CaseBundle
    from foampilot.observations import ObservationPlan


class PlanCompilationError(ValueError):
    pass


def _raise(code: str, detail: str) -> None:
    raise PlanCompilationError(f"{code}: {detail}")


def compile_execution_plan(
    *,
    design: CaseDesign,
    bundle: CaseBundle,
    environment: EnvironmentSnapshot,
    task: TaskSpec,
    registry: CapabilityRegistry,
    observation_plan: ObservationPlan | None = None,
) -> ExecutionPlan:
    """Compose extension fragments and run the canonical plan safety policy."""

    target = task.openfoam_target
    if (
        environment.distribution != target.distribution
        or environment.version != target.version
    ):
        _raise(
            "PLAN_TARGET_MISMATCH",
            "task target and discovered environment differ",
        )
    solver = str(design.proposal.solver_family.value)
    if bundle.manifest.solver_executable != solver:
        _raise(
            "DESIGN_MANIFEST_MISMATCH",
            f"solver {bundle.manifest.solver_executable} != {solver}",
        )

    try:
        fragments = registry.plan_for(
            PlanContext(
                design=design,
                manifest=bundle.manifest,
                target=target,
                resource_budget=task.resource_budget,
                command_facts=tuple(environment.commands),
                mpi_available=environment.mpi_launcher is not None,
            )
        )
    except (LookupError, ValueError) as error:
        raise PlanCompilationError(str(error)) from error

    file_paths = {item.path for item in bundle.files}
    missing_paths = sorted(
        set(fragments.required_authored_paths) - file_paths
    )
    if missing_paths:
        _raise(
            "REQUIRED_AUTHORED_PATH_MISSING",
            ", ".join(missing_paths),
        )

    observation_commands = ()
    if observation_plan is not None:
        from foampilot.observations import compile_foundation10_observations

        observation_commands = compile_foundation10_observations(
            observation_plan
        ).commands
        unavailable = sorted(
            set(command.executable for command in observation_commands)
            - set(environment.available_executable_names)
        )
        if unavailable:
            _raise(
                "OBSERVATION_EXECUTABLE_UNAVAILABLE",
                ", ".join(unavailable),
            )
    plan = ExecutionPlan(
        compiled_from_design_sha256=design.design_sha256,
        compiler_identities=fragments.contributor_identities,
        manifest=bundle.manifest,
        files=bundle.files,
        commands=[*fragments.commands, *observation_commands],
    )
    issues = validate_execution_plan(
        plan,
        task,
        environment.available_executable_names,
    )
    if issues:
        detail = "; ".join(
            f"{item.code}@{item.location}: {item.detail}" for item in issues
        )
        _raise("PLAN_COMPILATION_POLICY_FAILED", detail)
    return plan


__all__ = ["PlanCompilationError", "compile_execution_plan"]
