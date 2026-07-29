"""Minimal model-authored case-bundle contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratedFile(StrictModel):
    path: str = Field(min_length=1)
    content: str = Field(min_length=1)


class NativeCommand(StrictModel):
    step_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    executable: str = Field(pattern=r"^[A-Za-z0-9_.+-]+$")
    args: list[str] = Field(default_factory=list)
    mpi_ranks: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(ge=1)


class ExecutionPlan(StrictModel):
    schema_version: Literal[2] = 2
    files: list[GeneratedFile] = Field(min_length=1)
    commands: list[NativeCommand] = Field(min_length=1)


class PlanIssue(StrictModel):
    code: str
    location: str
    detail: str
