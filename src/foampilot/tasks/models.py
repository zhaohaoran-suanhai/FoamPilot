"""Minimal evaluator-facing task contract for native OpenFOAM agents."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenFOAMTarget(StrictModel):
    distribution: Literal["foundation"]
    version: str = Field(pattern=r"^[0-9]+$")


class ResourceBudget(StrictModel):
    max_attempts: int = Field(ge=1, le=8)
    max_wall_seconds: int = Field(ge=1)
    max_mpi_ranks: int = Field(ge=1)
    memory_mib: int = Field(ge=256)


class PublicAsset(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_safe_relative_path(cls, value: str) -> str:
        parsed = PurePosixPath(value)
        if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
            raise ValueError(
                f"public asset path must be a safe relative path: {value!r}"
            )
        return parsed.as_posix()

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("public asset purpose must not be blank")
        return normalized


type JsonParameter = float | int | str | bool


class PublicCheck(StrictModel):
    name: str = Field(min_length=1)
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    parameters: dict[str, JsonParameter] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("public check name must not be blank")
        return normalized


class TaskSpec(StrictModel):
    schema_version: Literal[1]
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    openfoam_target: OpenFOAMTarget
    resource_budget: ResourceBudget
    required_outputs: list[str] = Field(min_length=1)
    acceptance_requirements: list[str] = Field(min_length=1)
    public_checks: list[PublicCheck] = Field(min_length=1)
    public_assets: list[PublicAsset] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)

    @field_validator(
        "title",
        "prompt",
    )
    @classmethod
    def validate_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text value must not be blank")
        return normalized

    @field_validator(
        "required_outputs",
        "acceptance_requirements",
    )
    @classmethod
    def validate_unique_text_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("list entries must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate list entries are not allowed")
        return normalized

    @field_validator("protected_paths")
    @classmethod
    def validate_protected_paths(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            path = Path(item)
            if not path.is_absolute():
                raise ValueError(
                    f"protected path must be absolute: {item!r}"
                )
            if path == Path("/"):
                raise ValueError("protected path must not be the filesystem root")
            normalized.append(str(path))
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate protected paths are not allowed")
        return normalized

    @model_validator(mode="after")
    def validate_cross_field_leakage(self) -> Self:
        asset_paths = [asset.path for asset in self.public_assets]
        if len(asset_paths) != len(set(asset_paths)):
            raise ValueError("duplicate public asset paths are not allowed")
        check_names = [check.name for check in self.public_checks]
        if len(check_names) != len(set(check_names)):
            raise ValueError("duplicate public check names are not allowed")
        visible = json.dumps(
            self.agent_payload(),
            ensure_ascii=False,
            sort_keys=True,
        )
        for protected in self.protected_paths:
            if protected in visible:
                raise ValueError(
                    "protected path appears in agent-visible task content"
                )
        return self

    def agent_payload(self) -> dict[str, object]:
        return self.model_dump(
            exclude={"protected_paths", "public_checks"},
            mode="json",
        )
