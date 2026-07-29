"""Factual local OpenFOAM environment inventory."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandFact(StrictModel):
    name: str
    path: Path
    help_excerpt: str | None = None


class EnvironmentSnapshot(StrictModel):
    schema_version: Literal[1]
    distribution: Literal["foundation"]
    version: str
    openfoam_root: Path
    tutorial_root: Path
    workspace_root: Path
    workspace_writable: bool
    commands: list[CommandFact]
    mpi_launcher: Path | None
    gmsh: Path | None
    max_mpi_ranks: int

    @property
    def executable_names(self) -> set[str]:
        return {item.name for item in self.commands}

    def agent_payload(self) -> dict[str, object]:
        return self.model_dump(exclude={"tutorial_root"}, mode="json")
