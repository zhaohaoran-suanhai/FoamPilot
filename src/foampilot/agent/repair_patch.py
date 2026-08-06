"""Validated structural patches for native typed execution plans."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.tasks import TaskSpec

from .repair_scope import RepairScope


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileOperation(StrictModel):
    operation: Literal["add", "replace"]
    path: str = Field(min_length=1)
    content: str = Field(min_length=1)


class CommandOperation(StrictModel):
    """Provider-compatible flat command edit with strict local validation."""

    operation: Literal["insert_before", "insert_after", "replace", "remove"]
    anchor_step_id: str | None = None
    target_step_id: str | None = None
    command: NativeCommand | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> CommandOperation:
        inserting = self.operation in {"insert_before", "insert_after"}
        replacing = self.operation == "replace"
        removing = self.operation == "remove"
        if inserting:
            valid = (
                bool(self.anchor_step_id)
                and self.target_step_id is None
                and self.command is not None
            )
        elif replacing:
            valid = (
                bool(self.target_step_id)
                and self.anchor_step_id is None
                and self.command is not None
            )
        else:
            assert removing
            valid = (
                bool(self.target_step_id)
                and self.anchor_step_id is None
                and self.command is None
            )
        if not valid:
            raise ValueError(f"invalid command operation shape: {self.operation}")
        return self


class RepairPatch(StrictModel):
    schema_version: Literal[1] = 1
    because: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    file_operations: list[FileOperation] = Field(default_factory=list)
    command_operations: list[CommandOperation] = Field(default_factory=list)
    expected_check: str = Field(min_length=1)
    stable_control: str = Field(min_length=1)


class RepairChangeSet(StrictModel):
    changed_file_paths: list[str] = Field(default_factory=list)
    changed_files: list[GeneratedFile] = Field(default_factory=list)
    command_operations: list[str] = Field(default_factory=list)
    changed_commands: list[NativeCommand] = Field(default_factory=list)


class RepairPatchResult(StrictModel):
    plan: ExecutionPlan
    changes: RepairChangeSet
    normalizations: list[str] = Field(default_factory=list)


class RepairPatchError(ValueError):
    code = "REPAIR_PATCH_INVALID"
    message = "修复补丁越界或应用后的执行计划无效。"
    recovery = "请依据当前 RepairScope 只提交最小且实际生效的文件或命令操作。"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


_STAGE_ORDER = {
    "mesh": 0,
    "check": 1,
    "initialize": 2,
    "decompose": 3,
    "solve": 4,
    "reconstruct": 5,
    "postprocess": 6,
}


def _scope_operation(operation: str, *, command: bool) -> str:
    if not command:
        return f"{operation}_file"
    return {
        "insert_before": "insert_command_before",
        "insert_after": "insert_command_after",
        "replace": "replace_command",
        "remove": "remove_command",
    }[operation]


def _stage_inversions(commands: list[NativeCommand]) -> int:
    values = [_STAGE_ORDER[item.stage.value] for item in commands]
    return sum(
        1
        for index, value in enumerate(values)
        for later in values[index + 1 :]
        if value > later
    )


def apply_repair_patch(
    patch: RepairPatch,
    *,
    scope: RepairScope,
    task: TaskSpec,
    plan: ExecutionPlan,
    available_executables: set[str],
    current_files: dict[str, str],
) -> RepairPatchResult:
    """Normalize, validate and apply a patch; return only actual changes."""

    allowed = set(scope.allowed_operations)
    scoped_files = {item.path for item in scope.relevant_files}
    scoped_commands = set(scope.relevant_commands)
    file_by_path = {item.path: item for item in plan.files}
    file_order = [item.path for item in plan.files]
    commands = list(plan.commands)
    normalizations: list[str] = []
    changed_files: list[GeneratedFile] = []
    changed_file_paths: list[str] = []
    changed_commands: list[NativeCommand] = []
    command_changes: list[str] = []

    file_targets: set[str] = set()
    for operation in patch.file_operations:
        scope_name = _scope_operation(operation.operation, command=False)
        if scope_name not in allowed:
            raise RepairPatchError(f"OPERATION_OUTSIDE_SCOPE:{scope_name}")
        if operation.path not in scoped_files:
            raise RepairPatchError(f"OUTSIDE_REPAIR_SCOPE:{operation.path}")
        if operation.path in file_targets:
            raise RepairPatchError(f"DUPLICATE_FILE_OPERATION:{operation.path}")
        file_targets.add(operation.path)
        exists = operation.path in file_by_path
        if operation.operation == "add" and exists:
            raise RepairPatchError(f"ADD_FILE_ALREADY_EXISTS:{operation.path}")
        if operation.operation == "replace" and not exists:
            raise RepairPatchError(f"REPLACE_FILE_MISSING:{operation.path}")
        if current_files.get(operation.path) == operation.content:
            normalizations.append(f"DROP_NO_OP_FILE:{operation.path}")
            continue
        generated = GeneratedFile(
            path=operation.path,
            content=operation.content,
        )
        file_by_path[operation.path] = generated
        if not exists:
            file_order.append(operation.path)
        changed_files.append(generated)
        changed_file_paths.append(operation.path)

    touched_command_targets: set[str] = set()
    for operation in patch.command_operations:
        scope_name = _scope_operation(operation.operation, command=True)
        if scope_name not in allowed:
            raise RepairPatchError(f"OPERATION_OUTSIDE_SCOPE:{scope_name}")
        index_by_step = {
            command.step_id: index for index, command in enumerate(commands)
        }
        if operation.operation in {"insert_before", "insert_after"}:
            anchor = operation.anchor_step_id
            if anchor not in scoped_commands:
                raise RepairPatchError(f"OUTSIDE_REPAIR_SCOPE:{anchor}")
            if anchor not in index_by_step:
                raise RepairPatchError(f"UNKNOWN_COMMAND_ANCHOR:{anchor}")
            assert operation.command is not None
            new_step = operation.command.step_id
            if new_step in index_by_step or new_step in touched_command_targets:
                raise RepairPatchError(f"DUPLICATE_STEP_ID:{new_step}")
            anchor_index = index_by_step[anchor]
            insert_at = (
                anchor_index
                if operation.operation == "insert_before"
                else anchor_index + 1
            )
            commands.insert(insert_at, operation.command)
            touched_command_targets.add(new_step)
            changed_commands.append(operation.command)
            command_changes.append(f"{operation.operation}:{new_step}")
            continue

        target = operation.target_step_id
        if target not in scoped_commands:
            raise RepairPatchError(f"OUTSIDE_REPAIR_SCOPE:{target}")
        if target not in index_by_step:
            raise RepairPatchError(f"UNKNOWN_REPAIR_STEP:{target}")
        if target in touched_command_targets:
            raise RepairPatchError(f"DUPLICATE_COMMAND_OPERATION:{target}")
        touched_command_targets.add(target)
        index = index_by_step[target]
        previous = commands[index]
        if operation.operation == "replace":
            if operation.command.step_id != target:
                raise RepairPatchError(
                    f"REPLACEMENT_STEP_ID_MISMATCH:{target}"
                )
            if operation.command.stage != previous.stage:
                raise RepairPatchError(
                    f"REPLACEMENT_STAGE_MISMATCH:{target}"
                )
            if operation.command == previous:
                normalizations.append(f"DROP_NO_OP_COMMAND:{target}")
                continue
            commands[index] = operation.command
            changed_commands.append(operation.command)
            command_changes.append(f"replace:{target}")
        else:
            commands.pop(index)
            changed_commands.append(previous)
            command_changes.append(f"remove:{target}")

    if not changed_files and not command_changes:
        raise RepairPatchError("NO_OP_REPAIR_PATCH")
    if not commands:
        raise RepairPatchError("REPAIR_REMOVES_ALL_COMMANDS")
    if _stage_inversions(commands) > _stage_inversions(list(plan.commands)):
        raise RepairPatchError("COMMAND_STAGE_ORDER_INVALID")

    revised = plan.model_copy(
        update={
            "files": [file_by_path[path] for path in file_order],
            "commands": commands,
        }
    )
    normalized = normalize_execution_plan(
        revised,
        task,
        available_executables,
    )
    issues = validate_execution_plan(
        normalized.plan,
        task,
        available_executables,
    )
    if issues:
        detail = ",".join(
            f"{item.code}@{item.location}" for item in issues
        )
        raise RepairPatchError(f"INVALID_REPAIR_PLAN:{detail}")
    return RepairPatchResult(
        plan=normalized.plan,
        changes=RepairChangeSet(
            changed_file_paths=changed_file_paths,
            changed_files=changed_files,
            command_operations=command_changes,
            changed_commands=changed_commands,
        ),
        normalizations=normalizations,
    )


__all__ = [
    "CommandOperation",
    "FileOperation",
    "RepairChangeSet",
    "RepairPatch",
    "RepairPatchError",
    "RepairPatchResult",
    "apply_repair_patch",
]
