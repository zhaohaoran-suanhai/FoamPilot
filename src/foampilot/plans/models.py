"""Minimal model-authored case-bundle contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foampilot.manifests import CaseManifest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratedFile(StrictModel):
    path: str = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _reserve_internal_namespace(cls, value: str) -> str:
        if ".foampilot" in PurePosixPath(value).parts:
            raise ValueError("generated file path uses reserved .foampilot namespace")
        return value


class CommandStage(StrEnum):
    MESH = "mesh"
    CHECK = "check"
    INITIALIZE = "initialize"
    DECOMPOSE = "decompose"
    SOLVE = "solve"
    RECONSTRUCT = "reconstruct"
    POSTPROCESS = "postprocess"


class NativeCommand(StrictModel):
    step_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    stage: CommandStage
    executable: str = Field(pattern=r"^[A-Za-z0-9_.+-]+$")
    args: list[str] = Field(default_factory=list)
    mpi_ranks: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(ge=1)


class ExecutionPlan(StrictModel):
    schema_version: Literal[3] = 3
    manifest: CaseManifest
    files: list[GeneratedFile] = Field(min_length=1)
    commands: list[NativeCommand] = Field(min_length=1)


class PlanIssue(StrictModel):
    code: str
    location: str
    detail: str
