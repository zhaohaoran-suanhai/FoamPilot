"""Static native-case inspection results."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectionIssue(StrictModel):
    code: str
    path: str | None = None
    detail: str


class InspectionReport(StrictModel):
    issues: list[InspectionIssue] = Field(default_factory=list)
    observed_patches: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues
