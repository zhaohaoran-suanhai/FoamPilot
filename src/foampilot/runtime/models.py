"""Typed runtime inputs and results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfig(StrictModel):
    openfoam_root: Path
    tutorial_root: Path
    python_executable: Path
    bubblewrap: Path
    max_mpi_ranks: int = Field(default=1, ge=1)
    execution_backend: Literal["auto", "bubblewrap", "host"] = (
        "bubblewrap"
    )

    @classmethod
    def local_foundation_v10(cls) -> "RuntimeConfig":
        root = Path("/home/edwin/workplace/OpenFOAM-10")
        return cls(
            openfoam_root=root,
            tutorial_root=root / "tutorials",
            python_executable=Path(
                "/home/edwin/feal-venv-py312/bin/python"
            ),
            bubblewrap=Path("/usr/local/bin/bwrap"),
            execution_backend="auto",
        )


class RuntimeCheck(StrictModel):
    name: str
    ok: bool
    detail: str
    blocking: bool = True


class PlanStepResult(StrictModel):
    step_id: str
    command: list[str]
    return_code: int | None
    started_at: datetime
    finished_at: datetime
    timed_out: bool
    stdout_path: Path
    stderr_path: Path
    execution_backend: Literal["bubblewrap", "host"] = "bubblewrap"
    backend_fallback_reason: str | None = None


class PlanRunResult(StrictModel):
    case_dir: Path
    steps: list[PlanStepResult]
    failed_step_id: str | None = None
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.failed_step_id is None and bool(self.steps)
