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
    tutorial_root: Path | None
    workspace_root: Path
    workspace_writable: bool
    commands: list[CommandFact]
    mpi_launcher: Path | None
    gmsh: Path | None
    max_mpi_ranks: int

    @property
    def executable_names(self) -> set[str]:
        return {item.name for item in self.commands}

    @property
    def available_executable_names(self) -> set[str]:
        """Return every executable allowed in a typed plan."""

        names = set(self.executable_names)
        if self.gmsh is not None:
            names.add("gmsh")
        return names

    def agent_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "distribution": self.distribution,
            "version": self.version,
            "workspace_writable": self.workspace_writable,
            "executable_names": sorted(self.available_executable_names),
            "mpi_available": self.mpi_launcher is not None,
            "gmsh_available": self.gmsh is not None,
            "max_mpi_ranks": self.max_mpi_ranks,
        }
