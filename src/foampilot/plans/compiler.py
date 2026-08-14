"""Compile one immutable ExecutionPlan v4 from trusted contributors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foampilot.environment import EnvironmentSnapshot
from foampilot.extensions import CapabilityRegistry, PlanContext
from foampilot.simulation import CaseDesign
from foampilot.tasks import TaskSpec

from .models import CommandStage, ExecutionPlan
from .validation import validate_execution_plan

if TYPE_CHECKING:
    from foampilot.authoring import CaseBundle
    from foampilot.observations import ObservationPlan


class PlanCompilationError(ValueError):
    pass


def _raise(code: str, detail: str) -> None:
    raise PlanCompilationError(f"{code}: {detail}")


def _confirmed_task_solver_run(task: TaskSpec) -> bool:
    fact = next(
        (
            item
            for item in task.explicit_facts
            if item.field_path == "execution.run_solver" and item.confirmed
        ),
        None,
    )
    if fact is None:
        return True
    if not isinstance(fact.value, bool):
        _raise(
            "PLAN_RUN_SOLVER_VALUE_INVALID",
            "confirmed task execution.run_solver must be boolean",
        )
    return fact.value


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

    context = PlanContext(
        design=design,
        manifest=bundle.manifest,
        target=target,
        resource_budget=task.resource_budget,
        command_facts=tuple(environment.commands),
        mpi_available=environment.mpi_launcher is not None,
    )
    try:
        run_solver = context.solver_run_enabled
        task_run_solver = _confirmed_task_solver_run(task)
        if task_run_solver is not run_solver:
            _raise(
                "PLAN_RUN_SOLVER_TASK_DESIGN_MISMATCH",
                "confirmed task and frozen design execution controls differ",
            )
        fragments = registry.plan_for(
            context
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

    execution_stages = {
        CommandStage.DECOMPOSE,
        CommandStage.SOLVE,
        CommandStage.RECONSTRUCT,
        CommandStage.POSTPROCESS,
    }
    commands = tuple(
        item
        for item in fragments.commands
        if run_solver or item.stage not in execution_stages
    )

    observation_commands = ()
    if observation_plan is not None and run_solver:
        from foampilot.observations import compile_foundation10_observations

        postprocess_count = sum(
            item.evidence_strategy.kind == "postprocess_command"
            for item in observation_plan.items
        )
        remaining_timeout = task.resource_budget.max_wall_seconds - sum(
            item.timeout_seconds for item in commands
        )
        if postprocess_count > remaining_timeout:
            _raise(
                "OBSERVATION_TIMEOUT_BUDGET_EXHAUSTED",
                "one second per post-process command cannot fit the wall budget",
            )
        per_command_timeout = (
            max(1, remaining_timeout // postprocess_count)
            if postprocess_count
            else 20
        )
        observation_commands = compile_foundation10_observations(
            observation_plan,
            postprocess_timeout_seconds=per_command_timeout,
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
        commands=[*commands, *observation_commands],
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
