"""Typed, command-free repair policy and authorization contracts."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from foampilot.plans.models import GeneratedFile, NativeCommand


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


RepairCategory = Literal["mechanical", "numerical", "physical", "capability"]
RepairAuthorizationState = Literal[
    "AUTHORIZED_AUTOMATIC",
    "CONFIRMATION_REQUIRED",
    "FINALIZE_FAILED",
]


class NumericalRepairRule(StrictFrozenModel):
    field_path: str = Field(pattern=r"^numerics(?:\.[A-Za-z0-9_-]+)+$")
    operators: tuple[Literal["replace", "scale"], ...] = Field(min_length=1)
    direction: Literal["increase", "decrease", "either"]
    minimum: float | None = None
    maximum: float | None = None
    authored_paths: tuple[str, ...] = Field(min_length=1)
    dictionary_keyword: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        if len(self.operators) != len(set(self.operators)):
            raise ValueError("numerical repair operators must be unique")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("numerical repair minimum exceeds maximum")
        if len(self.authored_paths) != len(set(self.authored_paths)):
            raise ValueError("numerical repair authored paths must be unique")
        for value in self.authored_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("repair authored path must be safe and relative")
        return self


class NumericalRepairEnvelope(StrictFrozenModel):
    schema_version: Literal[1] = 1
    rules: tuple[NumericalRepairRule, ...] = ()

    @model_validator(mode="after")
    def validate_unique_paths(self) -> Self:
        paths = [item.field_path for item in self.rules]
        if len(paths) != len(set(paths)):
            raise ValueError("numerical repair rule paths must be unique")
        return self


class RepairPolicy(StrictFrozenModel):
    automatic_numerical_repair: bool = True
    model_diagnostic: bool = True


class DesignChange(StrictFrozenModel):
    field_path: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*$"
    )
    old_value: object
    new_value: object
    operator: Literal["replace", "scale", "offset"]

    @field_validator("old_value", "new_value")
    @classmethod
    def validate_json_value(cls, value: object) -> object:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("repair values must be finite JSON values") from error
        return value


class RepairFileOperation(StrictFrozenModel):
    operation: Literal["add", "replace"]
    path: str = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or ".foampilot" in path.parts
            or not path.parts
        ):
            raise ValueError("repair file path must be safe and relative")
        return path.as_posix()


class RepairProposal(StrictFrozenModel):
    schema_version: Literal[1] = 1
    category: RepairCategory
    because: str = Field(min_length=1)
    design_changes: tuple[DesignChange, ...] = ()
    file_operations: tuple[RepairFileOperation, ...] = ()
    expected_checks: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_changes(self) -> Self:
        paths = [item.field_path for item in self.design_changes]
        if len(paths) != len(set(paths)):
            raise ValueError("repair design changes must be unique")
        file_paths = [item.path for item in self.file_operations]
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("repair file operations must be unique")
        return self


class RepairChangeSet(StrictFrozenModel):
    """Actual byte and command effects used only for deterministic reuse."""

    changed_file_paths: tuple[str, ...] = ()
    changed_files: tuple[GeneratedFile, ...] = ()
    command_operations: tuple[str, ...] = ()
    changed_commands: tuple[NativeCommand, ...] = ()


class RepairAuthorization(StrictFrozenModel):
    state: RepairAuthorizationState
    reason_codes: tuple[str, ...]
    authorized_paths: tuple[str, ...] = ()
    confirmation_paths: tuple[str, ...] = ()


class DerivedCaseDesignRecord(StrictFrozenModel):
    schema_version: Literal[1] = 1
    parent_design_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    design_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_paths: tuple[str, ...] = Field(min_length=1)


class RepairDecision(StrictFrozenModel):
    state: Literal[
        "MECHANICAL_PATCH",
        "AUTHORIZED_NUMERICAL_PATCH",
        "CONFIRMATION_REQUIRED",
        "FINALIZE_FAILED",
    ]
    reason_codes: tuple[str, ...] = Field(min_length=1)
    proposal: RepairProposal | None = None
    confirmation_paths: tuple[str, ...] = ()


__all__ = [
    "DesignChange",
    "DerivedCaseDesignRecord",
    "NumericalRepairEnvelope",
    "NumericalRepairRule",
    "RepairAuthorization",
    "RepairCategory",
    "RepairChangeSet",
    "RepairDecision",
    "RepairFileOperation",
    "RepairPolicy",
    "RepairProposal",
]
