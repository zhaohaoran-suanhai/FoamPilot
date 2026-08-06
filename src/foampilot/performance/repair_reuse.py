"""Deterministic dependency rules for immutable repair attempts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.plans import ExecutionPlan, NativeCommand
from foampilot.runtime import ReusedStepResult

if TYPE_CHECKING:
    from foampilot.agent.repair import RepairDecision


RerunStage = Literal["mesh", "initialize", "solve", "postprocess"]
_STAGE_ORDER = {"mesh": 0, "initialize": 1, "solve": 2, "postprocess": 3}
_MESH_NAMES = {
    "blockMeshDict",
    "snappyHexMeshDict",
    "surfaceFeatureExtractDict",
    "meshQualityDict",
    "topoSetDict",
    "createPatchDict",
    "refineMeshDict",
    "extrudeMeshDict",
    "decomposeParDict",
}
_INITIALIZE_NAMES = {"setFieldsDict", "setExprFieldsDict"}
_POSTPROCESS_NAMES = {
    "sampleDict",
    "probesDict",
    "surfacesDict",
    "postProcessDict",
}
_DYNAMIC_NAMES = {"dynamicMeshDict", "dynamicCode"}
_INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)


class RepairReuseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    earliest_rerun_stage: RerunStage
    reusable: bool
    reason_codes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class RepairReusePreparation:
    decision: RepairReuseDecision
    commands_to_execute: list[NativeCommand]
    reused_steps: list[ReusedStepResult]
    record: dict[str, object]


def _time_zero_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2:
        return False
    try:
        return float(parts[0]) == 0.0
    except ValueError:
        return False


def _path_stage(path: str) -> RerunStage:
    parsed = PurePosixPath(path)
    name = parsed.name
    if (
        name in _MESH_NAMES
        or path.startswith("constant/polyMesh/")
        or path.startswith("constant/triSurface/")
        or parsed.suffix in {".geo", ".msh"}
    ):
        return "mesh"
    if _time_zero_path(path) or name in _INITIALIZE_NAMES:
        return "initialize"
    if name in _POSTPROCESS_NAMES or path.startswith("system/postProcessing/"):
        return "postprocess"
    if parsed.suffix in {".inc", ".include"}:
        return "mesh"
    return "solve"


def _command_stage(command: NativeCommand) -> RerunStage:
    if command.stage in {"mesh", "check", "decompose"}:
        return "mesh"
    if command.stage == "initialize":
        return "initialize"
    if command.stage == "solve":
        return "solve"
    return "postprocess"


def _included_stage(plan: ExecutionPlan, changed_path: str) -> RerunStage | None:
    candidates: list[RerunStage] = []
    for generated in plan.files:
        parent = PurePosixPath(generated.path).parent
        for included in _INCLUDE.findall(generated.content):
            resolved = (parent / included).as_posix()
            if changed_path in {included, resolved}:
                candidates.append(_path_stage(generated.path))
    if not candidates:
        return None
    return min(candidates, key=lambda item: _STAGE_ORDER[item])


def classify_repair_rerun(
    plan: ExecutionPlan,
    decision: "RepairDecision",
) -> RepairReuseDecision:
    """Select the earliest stage from actual changed paths and commands."""

    if len(plan.manifest.regions) != 1:
        return RepairReuseDecision(
            earliest_rerun_stage="mesh",
            reusable=False,
            reason_codes=["MULTI_REGION_REPAIR_REUSE_UNSAFE"],
        )
    if any(command.mpi_ranks > 1 for command in plan.commands) or any(
        command.stage in {"decompose", "reconstruct"}
        for command in plan.commands
    ):
        return RepairReuseDecision(
            earliest_rerun_stage="mesh",
            reusable=False,
            reason_codes=["MPI_REPAIR_REUSE_UNSAFE"],
        )
    if any(
        PurePosixPath(item.path).name in _DYNAMIC_NAMES
        for item in plan.files
    ):
        return RepairReuseDecision(
            earliest_rerun_stage="mesh",
            reusable=False,
            reason_codes=["DYNAMIC_MESH_REPAIR_REUSE_UNSAFE"],
        )

    stages: list[RerunStage] = []
    reasons: list[str] = []
    for changed in decision.changed_files:
        stage = _included_stage(plan, changed.path) or _path_stage(changed.path)
        stages.append(stage)
        reasons.append(f"CHANGED_FILE_{stage.upper()}:{changed.path}")
    known_steps = {item.step_id: item for item in plan.commands}
    for changed in decision.changed_commands:
        if changed.step_id not in known_steps:
            return RepairReuseDecision(
                earliest_rerun_stage="mesh",
                reusable=False,
                reason_codes=["COMMAND_DEPENDENCY_UNRESOLVED"],
            )
        stage = min(
            (_command_stage(known_steps[changed.step_id]), _command_stage(changed)),
            key=lambda item: _STAGE_ORDER[item],
        )
        stages.append(stage)
        reasons.append(f"CHANGED_COMMAND_{stage.upper()}:{changed.step_id}")
    if not stages:
        return RepairReuseDecision(
            earliest_rerun_stage="mesh",
            reusable=False,
            reason_codes=["REPAIR_DEPENDENCY_EMPTY"],
        )
    earliest = min(stages, key=lambda item: _STAGE_ORDER[item])
    if earliest != "mesh" and not any(
        item.stage == "check" and item.executable == "checkMesh"
        for item in plan.commands
    ):
        return RepairReuseDecision(
            earliest_rerun_stage="mesh",
            reusable=False,
            reason_codes=[*reasons, "REPAIR_REUSE_REQUIRES_CHECKMESH"],
        )
    return RepairReuseDecision(
        earliest_rerun_stage=earliest,
        reusable=(earliest != "mesh"),
        reason_codes=reasons,
    )


def _file_hashes(root: Path, paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for selected in paths:
        if selected.is_symlink():
            raise ValueError("repair reuse does not accept symlinks")
        files = [selected] if selected.is_file() else sorted(selected.rglob("*"))
        for path in files:
            if path.is_dir():
                continue
            if path.is_symlink():
                raise ValueError("repair reuse does not accept symlinks")
            digest = sha256(path.read_bytes()).hexdigest()
            values[path.relative_to(root).as_posix()] = digest
    return values


def _copy_paths(source_root: Path, destination_root: Path, paths: list[Path]) -> None:
    for source in paths:
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def _selected_source_paths(
    parent_case: Path,
    stage: RerunStage,
) -> list[Path]:
    selected = sorted(
        path
        for path in (parent_case / "constant").rglob("polyMesh")
        if path.is_dir() and not path.is_symlink()
    )
    if stage in {"solve", "postprocess"}:
        for path in sorted(parent_case.iterdir()):
            if not path.is_dir():
                continue
            try:
                value = float(path.name)
            except ValueError:
                continue
            if value == 0.0:
                selected.append(path)
            elif stage == "postprocess":
                selected.append(path)
    return selected


def _commands_from_stage(
    plan: ExecutionPlan,
    stage: RerunStage,
) -> tuple[list[NativeCommand], list[NativeCommand]]:
    skipped_stages = {
        "initialize": {"mesh"},
        "solve": {"mesh", "initialize"},
        "postprocess": {"mesh", "initialize", "solve"},
    }[stage]
    skipped = [
        item for item in plan.commands if item.stage.value in skipped_stages
    ]
    execute = [
        item
        for item in plan.commands
        if item.stage.value not in skipped_stages or item.stage == "check"
    ]
    return execute, skipped


def prepare_repair_reuse(
    *,
    parent_attempt: str | Path,
    next_case_root: str | Path,
    plan: ExecutionPlan,
    decision: "RepairDecision",
) -> RepairReusePreparation:
    """Copy proven prior-stage products and produce an ordered command slice."""

    parent = Path(parent_attempt).resolve()
    parent_case = parent / "case"
    destination = Path(next_case_root).resolve()
    classified = classify_repair_rerun(plan, decision)
    full_commands = list(plan.commands)
    base_record: dict[str, object] = {
        "schema_version": 1,
        "applied": False,
        "source_attempt": parent.name,
        "earliest_rerun_stage": classified.earliest_rerun_stage,
        "reused_paths": [],
        "source_hashes": {},
        "reason_codes": list(classified.reason_codes),
        "reused_step_ids": [],
        "commands_to_execute": [item.step_id for item in full_commands],
    }
    if not classified.reusable:
        return RepairReusePreparation(
            classified,
            full_commands,
            [],
            base_record,
        )
    if not parent_case.is_dir() or not destination.is_dir():
        fallback = classified.model_copy(
            update={
                "earliest_rerun_stage": "mesh",
                "reusable": False,
                "reason_codes": [
                    *classified.reason_codes,
                    "PARENT_ATTEMPT_EVIDENCE_MISSING",
                ],
            }
        )
        base_record["earliest_rerun_stage"] = "mesh"
        base_record["reason_codes"] = fallback.reason_codes
        return RepairReusePreparation(fallback, full_commands, [], base_record)
    selected = _selected_source_paths(
        parent_case,
        classified.earliest_rerun_stage,
    )
    if not selected or not any(path.name == "polyMesh" for path in selected):
        fallback = classified.model_copy(
            update={
                "earliest_rerun_stage": "mesh",
                "reusable": False,
                "reason_codes": [
                    *classified.reason_codes,
                    "PARENT_MESH_EVIDENCE_MISSING",
                ],
            }
        )
        base_record["earliest_rerun_stage"] = "mesh"
        base_record["reason_codes"] = fallback.reason_codes
        return RepairReusePreparation(fallback, full_commands, [], base_record)
    try:
        before = _file_hashes(parent_case, selected)
        _copy_paths(parent_case, destination, selected)
        copied = [destination / path.relative_to(parent_case) for path in selected]
        after = _file_hashes(destination, copied)
        parent_after = _file_hashes(parent_case, selected)
    except (OSError, ValueError):
        fallback = classified.model_copy(
            update={
                "earliest_rerun_stage": "mesh",
                "reusable": False,
                "reason_codes": [
                    *classified.reason_codes,
                    "REPAIR_REUSE_COPY_FAILED",
                ],
            }
        )
        base_record["earliest_rerun_stage"] = "mesh"
        base_record["reason_codes"] = fallback.reason_codes
        return RepairReusePreparation(fallback, full_commands, [], base_record)
    if before != after or before != parent_after:
        fallback = classified.model_copy(
            update={
                "earliest_rerun_stage": "mesh",
                "reusable": False,
                "reason_codes": [
                    *classified.reason_codes,
                    "REPAIR_REUSE_HASH_MISMATCH",
                ],
            }
        )
        base_record["earliest_rerun_stage"] = "mesh"
        base_record["reason_codes"] = fallback.reason_codes
        return RepairReusePreparation(fallback, full_commands, [], base_record)
    execute, skipped = _commands_from_stage(
        plan,
        classified.earliest_rerun_stage,
    )
    source_id = parent.name
    reused_steps = [
        ReusedStepResult(
            step_id=item.step_id,
            stage=item.stage.value,
            executable=item.executable,
            source_kind="parent_attempt",
            source_id=source_id,
            reason_codes=["REPAIR_DEPENDENCY_UNCHANGED"],
        )
        for item in skipped
    ]
    record = {
        **base_record,
        "applied": True,
        "reused_paths": [
            path.relative_to(parent_case).as_posix() for path in selected
        ],
        "source_hashes": before,
        "reason_codes": [
            *classified.reason_codes,
            "REPAIR_DEPENDENCY_UNCHANGED",
        ],
        "reused_step_ids": [item.step_id for item in reused_steps],
        "commands_to_execute": [item.step_id for item in execute],
    }
    return RepairReusePreparation(
        classified,
        execute,
        reused_steps,
        record,
    )
