"""Strict model-authored case bundle with no execution authority."""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foampilot.manifests import CaseManifest
from foampilot.plans.models import GeneratedFile


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseBundle(StrictModel):
    schema_version: Literal[1] = 1
    manifest: CaseManifest
    files: list[GeneratedFile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> Self:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("case bundle file paths must be unique")
        return self


def load_case_bundle_output(output_text: str) -> CaseBundle:
    """Parse one author response without accepting plan-shaped payloads."""

    return CaseBundle.model_validate(json.loads(output_text))
