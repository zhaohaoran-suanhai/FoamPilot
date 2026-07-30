"""Strict, packageable qualification-suite manifests."""

from __future__ import annotations

from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuiteRole(StrEnum):
    REGRESSION = "regression"
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class SuiteCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    role: SuiteRole
    exclusive: bool = False


class QualificationSuite(StrictModel):
    schema_version: Literal[1] = 1
    protocol_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    max_workers: Literal[1, 2] = 2
    cases: list[SuiteCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> Self:
        identifiers = [item.case_id for item in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("suite case IDs must be unique")
        return self


def qualification_suite_path(protocol_id: str) -> Path:
    """Resolve one packaged suite manifest by protocol ID."""

    resource = (
        files("foampilot.qualification")
        .joinpath("data", "suites", f"{protocol_id}.yaml")
    )
    return Path(str(resource))


def load_qualification_suite(path: str | Path) -> QualificationSuite:
    """Load one strict suite manifest."""

    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"suite root must be a mapping: {source}")
    return QualificationSuite.model_validate(payload)
