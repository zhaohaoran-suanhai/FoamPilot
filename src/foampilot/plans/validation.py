"""Deterministic safety policy for model-authored case bundles."""

from __future__ import annotations

from pathlib import PurePosixPath

from foampilot.tasks import TaskSpec

from .models import ExecutionPlan, PlanIssue


_SHELL_TOKENS = {"&&", "||", ";", "|", "<", ">"}
_SHELL_MARKERS = ("$(", "`", "\n", "\r", "\0")
_MPI_HOST_OPTIONS = {"--host", "--hostfile", "-host", "-hostfile"}
_CONTEXT_OVERRIDE_OPTIONS = {
    "-case",
    "--case",
    "-roots",
    "--roots",
    "-hostroots",
    "--hostroots",
}
_MPI_LAUNCHERS = {"mpirun", "mpiexec", "orterun"}


def _issue(code: str, location: str, detail: str) -> PlanIssue:
    return PlanIssue(code=code, location=location, detail=detail)


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _validate_argument(argument: str, location: str) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    if (
        argument in _SHELL_TOKENS
        or any(marker in argument for marker in _SHELL_MARKERS)
        or any(character in argument for character in (";", "|", "<", ">"))
    ):
        issues.append(
            _issue(
                "SHELL_TOKEN",
                location,
                "shell syntax is forbidden in typed command arguments",
            )
        )
    values = [argument]
    if argument.startswith("-") and "=" in argument:
        values.append(argument.split("=", 1)[1])
    for value in values:
        path = PurePosixPath(value)
        if ".." in path.parts:
            issues.append(
                _issue(
                    "PARENT_TRAVERSAL",
                    location,
                    "parent traversal is forbidden in command arguments",
                )
            )
        if path.is_absolute():
            issues.append(
                _issue(
                    "EXTERNAL_ABSOLUTE_PATH",
                    location,
                    "absolute paths are forbidden in command arguments",
                )
            )
    return issues


def validate_execution_plan(
    plan: ExecutionPlan,
    task: TaskSpec,
    available_executables: set[str],
) -> list[PlanIssue]:
    """Return safety and resource violations without judging CFD strategy."""

    issues: list[PlanIssue] = []
    file_paths = [item.path for item in plan.files]
    public_assets = {
        item.install_path if item.kind == "directory" else item.path
        for item in task.public_assets
    }

    if len(file_paths) != len(set(file_paths)):
        issues.append(
            _issue(
                "DUPLICATE_FILE_PATH",
                "files",
                "generated file paths must be unique",
            )
        )
    for index, generated in enumerate(plan.files):
        location = f"files[{index}]"
        if ".foampilot" in PurePosixPath(generated.path).parts:
            issues.append(
                _issue(
                    "RESERVED_INTERNAL_PATH",
                    f"{location}.path",
                    ".foampilot is reserved for Runner-owned artifacts",
                )
            )
        if not _safe_relative(generated.path):
            issues.append(
                _issue(
                    "UNSAFE_FILE_PATH",
                    f"{location}.path",
                    "generated files must use safe relative paths",
                )
            )
        if any(
            generated.path == asset_path
            or generated.path.startswith(f"{asset_path}/")
            for asset_path in public_assets
            if asset_path is not None
        ):
            issues.append(
                _issue(
                    "PUBLIC_ASSET_OVERWRITE",
                    f"{location}.path",
                    "generated files must not overwrite public assets",
                )
            )
            if task.mesh is not None and task.mesh.strategy == "provided":
                issues.append(
                    _issue(
                        "PROVIDED_MESH_REGENERATION",
                        f"{location}.path",
                        "provided mesh members are system-owned and immutable",
                    )
                )
        if any(
            protected in generated.content
            for protected in task.protected_paths
        ):
            issues.append(
                _issue(
                    "PROTECTED_REFERENCE",
                    f"{location}.content",
                    "generated content references an evaluator-protected path",
                )
            )

    step_ids = [item.step_id for item in plan.commands]
    if len(step_ids) != len(set(step_ids)):
        issues.append(
            _issue(
                "DUPLICATE_STEP_ID",
                "commands",
                "command step IDs must be unique",
            )
        )
    if (
        sum(command.timeout_seconds for command in plan.commands)
        > task.resource_budget.max_wall_seconds
    ):
        issues.append(
            _issue(
                "TIMEOUT_BUDGET",
                "commands",
                "command timeouts exceed the task wall-time budget",
            )
        )

    for index, command in enumerate(plan.commands):
        location = f"commands[{index}]"
        if (
            task.mesh is not None
            and task.mesh.strategy == "provided"
            and command.stage == "mesh"
        ):
            issues.append(
                _issue(
                    "PROVIDED_MESH_REGENERATION",
                    location,
                    "provided mesh plans must not contain mesh-generation commands",
                )
            )
        if command.executable in _MPI_LAUNCHERS:
            issues.append(
                _issue(
                    "MPI_LAUNCHER_UNNORMALIZED",
                    f"{location}.executable",
                    "the Runner owns MPI launch; an ambiguous launcher "
                    "command cannot execute",
                )
            )
        if command.executable not in available_executables:
            issues.append(
                _issue(
                    "EXECUTABLE_UNAVAILABLE",
                    f"{location}.executable",
                    f"executable is not installed: {command.executable}",
                )
            )
        if command.mpi_ranks > task.resource_budget.max_mpi_ranks:
            issues.append(
                _issue(
                    "MPI_RANK_LIMIT",
                    f"{location}.mpi_ranks",
                    "command MPI ranks exceed the task budget",
                )
            )
        for argument_index, argument in enumerate(command.args):
            argument_location = f"{location}.args[{argument_index}]"
            issues.extend(_validate_argument(argument, argument_location))
            if (
                argument.casefold().split("=", 1)[0]
                in _CONTEXT_OVERRIDE_OPTIONS
            ):
                issues.append(
                    _issue(
                        "CASE_CONTEXT_OVERRIDE",
                        argument_location,
                        "case and distributed root selection is Runner-owned",
                    )
                )
            if (
                argument in _MPI_HOST_OPTIONS
                or any(
                    argument.startswith(f"{option}=")
                    for option in _MPI_HOST_OPTIONS
                )
            ):
                issues.append(
                    _issue(
                        "MPI_HOST_SELECTION",
                        argument_location,
                        "MPI host selection is forbidden",
                    )
                )
            if any(
                protected in argument for protected in task.protected_paths
            ):
                issues.append(
                    _issue(
                        "PROTECTED_REFERENCE",
                        argument_location,
                        "command references an evaluator-protected path",
                    )
                )
    return issues
