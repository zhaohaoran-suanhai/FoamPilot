"""Narrow reader for reviewed frozen ExecutionPlan v2 replay fixtures.

This module is intentionally not exported from :mod:`foampilot.plans`.
Canonical authoring, execution, and resume accept ExecutionPlan v3 only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.manifests import CaseManifest

from .models import (
    CommandStage,
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrozenNativeCommandV2(StrictModel):
    step_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    executable: str = Field(pattern=r"^[A-Za-z0-9_.+-]+$")
    args: list[str] = Field(default_factory=list)
    mpi_ranks: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(ge=1)


class FrozenExecutionPlanV2(StrictModel):
    schema_version: Literal[2]
    files: list[GeneratedFile] = Field(min_length=1)
    commands: list[FrozenNativeCommandV2] = Field(min_length=1)


_UTILITY_STAGES: dict[str, CommandStage] = {
    "blockMesh": CommandStage.MESH,
    "snappyHexMesh": CommandStage.MESH,
    "gmshToFoam": CommandStage.MESH,
    "checkMesh": CommandStage.CHECK,
    "setFields": CommandStage.INITIALIZE,
    "topoSet": CommandStage.INITIALIZE,
    "splitMeshRegions": CommandStage.INITIALIZE,
    "decomposePar": CommandStage.DECOMPOSE,
    "reconstructPar": CommandStage.RECONSTRUCT,
    "postProcess": CommandStage.POSTPROCESS,
    "foamPostProcess": CommandStage.POSTPROCESS,
}


def load_frozen_v2_plan(
    payload: str | bytes | dict[str, object],
    overlay: str | bytes | dict[str, object],
) -> ExecutionPlan:
    """Convert one frozen v2 fixture using a separately reviewed manifest."""

    if isinstance(payload, (str, bytes)):
        legacy = FrozenExecutionPlanV2.model_validate_json(payload)
    else:
        legacy = FrozenExecutionPlanV2.model_validate(payload)
    if isinstance(overlay, (str, bytes)):
        manifest = CaseManifest.model_validate_json(overlay)
    else:
        manifest = CaseManifest.model_validate(overlay)

    commands: list[NativeCommand] = []
    for command in legacy.commands:
        stage = (
            CommandStage.SOLVE
            if command.executable == manifest.solver_executable
            else _UTILITY_STAGES.get(command.executable)
        )
        if stage is None:
            raise ValueError(
                "frozen v2 command stage is not reviewable: "
                f"{command.executable}"
            )
        commands.append(
            NativeCommand(
                step_id=command.step_id,
                stage=stage,
                executable=command.executable,
                args=command.args,
                mpi_ranks=command.mpi_ranks,
                timeout_seconds=command.timeout_seconds,
            )
        )
    return ExecutionPlan(
        manifest=manifest,
        files=legacy.files,
        commands=commands,
    )
