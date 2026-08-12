"""Immutable observation-only evidence contracts for one native attempt."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("evidence path must be safe and relative")
    return path.as_posix()


class RawCommandEvidence(StrictFrozenModel):
    step_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    stage: str = Field(min_length=1)
    executable: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    return_code: int | None
    started_at: datetime
    finished_at: datetime
    # None is reserved for read-only legacy evidence without monotonic timing.
    elapsed_seconds: float | None = Field(default=None, ge=0)
    timed_out: bool
    cancelled: bool = False
    stdout_path: str
    stderr_path: str
    stdout_sha256: str = Field(pattern=_SHA256_PATTERN)
    stderr_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_backend: Literal["bubblewrap", "host"]

    @field_validator("stdout_path", "stderr_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class MeshCheckFact(StrictFrozenModel):
    step_id: str
    executed: bool
    mesh_ok: bool | None
    points: int | None = Field(default=None, ge=0)
    faces: int | None = Field(default=None, ge=0)
    cells: int | None = Field(default=None, ge=0)
    regions: int | None = Field(default=None, ge=0)
    max_non_orthogonality: float | None = Field(default=None, ge=0)
    max_skewness: float | None = Field(default=None, ge=0)
    negative_volume_cells: int | None = Field(default=None, ge=0)
    parse_truncated: bool = False
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_execution_state(self) -> Self:
        if not self.executed and self.mesh_ok is not None:
            raise ValueError("unexecuted mesh check cannot report mesh_ok")
        return self


class SolverProgressFact(StrictFrozenModel):
    step_id: str
    simulation_time: float = Field(ge=0)
    completed_normally: bool | None = None
    line_number: int | None = Field(default=None, ge=1)


class ResidualFact(StrictFrozenModel):
    step_id: str
    simulation_time: float | None = Field(default=None, ge=0)
    region: str | None = None
    field: str = Field(min_length=1)
    initial: float = Field(ge=0)
    final: float = Field(ge=0)
    iterations: int = Field(ge=0)
    line_number: int | None = Field(default=None, ge=1)


class ContinuityFact(StrictFrozenModel):
    step_id: str
    simulation_time: float | None = Field(default=None, ge=0)
    region: str | None = None
    local: float | None = None
    global_value: float | None = None
    cumulative: float | None = None
    line_number: int | None = Field(default=None, ge=1)


class CourantFact(StrictFrozenModel):
    step_id: str
    simulation_time: float | None = Field(default=None, ge=0)
    region: str | None = None
    mean: float = Field(ge=0)
    maximum: float = Field(ge=0)
    line_number: int | None = Field(default=None, ge=1)


class NativeErrorFact(StrictFrozenModel):
    step_id: str
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1)
    subject: str | None = None
    path: str | None = None
    line_number: int | None = Field(default=None, ge=1)


class FieldOperationFact(StrictFrozenModel):
    step_id: str
    simulation_time: float | None = Field(default=None, ge=0)
    operation: Literal["min", "max", "volIntegrate"]
    field: str = Field(min_length=1)
    value: float
    line_number: int | None = Field(default=None, ge=1)


class ReusedCommandEvidence(StrictFrozenModel):
    step_id: str
    stage: str
    executable: str
    source_kind: str
    source_id: str
    reason_codes: tuple[str, ...] = ()


class RunFacts(StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    extractor_identities: dict[str, str] = Field(min_length=1)
    raw_steps: tuple[RawCommandEvidence, ...]
    mesh_checks: tuple[MeshCheckFact, ...] = ()
    solver_progress: tuple[SolverProgressFact, ...] = ()
    residuals: tuple[ResidualFact, ...] = ()
    continuity: tuple[ContinuityFact, ...] = ()
    courant: tuple[CourantFact, ...] = ()
    native_errors: tuple[NativeErrorFact, ...] = ()
    field_operations: tuple[FieldOperationFact, ...] = ()
    reused_steps: tuple[ReusedCommandEvidence, ...] = ()
    written_times: tuple[float, ...] = ()
    output_files: tuple[str, ...] = ()
    source_sha256: dict[str, str]

    @field_validator("written_times")
    @classmethod
    def validate_written_times(
        cls,
        values: tuple[float, ...],
    ) -> tuple[float, ...]:
        if any(value < 0 for value in values):
            raise ValueError("written times must be non-negative")
        if any(right < left for left, right in zip(values, values[1:])):
            raise ValueError("written times must preserve source order")
        return values

    @field_validator("output_files")
    @classmethod
    def validate_output_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_relative_path(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("output files must be unique")
        return normalized

    @field_validator("source_sha256")
    @classmethod
    def validate_source_hashes(cls, values: dict[str, str]) -> dict[str, str]:
        for path, digest in values.items():
            _relative_path(path)
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("source hash must be lowercase SHA256")
        return values

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        step_ids = tuple(item.step_id for item in self.raw_steps)
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("raw step ids must be unique")
        known_steps = set(step_ids)
        fact_groups = (
            self.mesh_checks,
            self.solver_progress,
            self.residuals,
            self.continuity,
            self.courant,
            self.native_errors,
            self.field_operations,
        )
        if any(
            fact.step_id not in known_steps
            for group in fact_groups
            for fact in group
        ):
            raise ValueError("fact references unknown step")
        times_by_step: dict[str, float] = {}
        for progress in self.solver_progress:
            previous = times_by_step.get(progress.step_id)
            if previous is not None and progress.simulation_time < previous:
                raise ValueError("solver time must preserve source order")
            times_by_step[progress.step_id] = progress.simulation_time
        expected_sources = {
            path: digest
            for step in self.raw_steps
            for path, digest in (
                (step.stdout_path, step.stdout_sha256),
                (step.stderr_path, step.stderr_sha256),
            )
        }
        for path, digest in expected_sources.items():
            if self.source_sha256.get(path) != digest:
                raise ValueError("raw step log hash missing from source_sha256")
        return self


__all__ = [
    "ContinuityFact",
    "CourantFact",
    "MeshCheckFact",
    "NativeErrorFact",
    "FieldOperationFact",
    "ReusedCommandEvidence",
    "RawCommandEvidence",
    "ResidualFact",
    "RunFacts",
    "SolverProgressFact",
]
