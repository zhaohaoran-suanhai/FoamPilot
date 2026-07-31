"""Static native-case inspection results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.manifests import SemanticRuleProvenance


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectionIssue(StrictModel):
    code: str
    path: str | None = None
    detail: str
    severity: Literal["error", "warning"] = "error"
    provenance: SemanticRuleProvenance | None = None


class InspectionReport(StrictModel):
    issues: list[InspectionIssue] = Field(default_factory=list)
    advisories: list[InspectionIssue] = Field(default_factory=list)
    observed_patches: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues
