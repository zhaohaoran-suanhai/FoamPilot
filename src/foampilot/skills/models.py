"""Contracts for Skill scenarios, evidence, and validation results."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


SkillName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64),
]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillScenario(StrictModel):
    skill_name: SkillName
    triggers: Annotated[list[NonEmpty], Field(min_length=1)]
    non_triggers: Annotated[list[NonEmpty], Field(min_length=1)]
    boundaries: Annotated[list[NonEmpty], Field(min_length=1)]
    pressure_prompt: NonEmpty
    success_criteria: Annotated[list[NonEmpty], Field(min_length=1)]
    forbidden_actions: Annotated[list[NonEmpty], Field(min_length=1)]


class SkillScenarioSuite(StrictModel):
    schema_version: Literal["1.0.0"]
    skills: Annotated[list[SkillScenario], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_skill_names(self) -> "SkillScenarioSuite":
        names = [scenario.skill_name for scenario in self.skills]
        if len(names) != len(set(names)):
            raise ValueError("skill scenario names must be unique")
        return self


class SkillTestEvidence(StrictModel):
    schema_version: Literal["1.0.0"]
    skill_name: SkillName
    phase: Literal["baseline", "forward"]
    agent_id: NonEmpty
    recorded_at: datetime
    prompt: NonEmpty
    output: NonEmpty
    observed_behaviors: Annotated[list[NonEmpty], Field(min_length=1)]
    verdict: Literal["PASS", "FAIL"]
    reviewer_notes: NonEmpty

    @model_validator(mode="after")
    def phase_matches_verdict(self) -> "SkillTestEvidence":
        if self.phase == "baseline" and self.verdict != "FAIL":
            raise ValueError("baseline evidence must record a FAIL")
        return self


class SkillValidationIssue(StrictModel):
    code: str
    path: str
    message: str
