"""Read-only models for inspecting historical TaskSpec v2 run artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml

from .geometry import GeometryInput, MeshIntent
from .models import OpenFOAMTarget, PublicAsset, PublicCheck, ResourceBudget


class LegacyTaskSpecV2(BaseModel):
    """Historical run representation; never accepted by authoring commands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2]
    task_id: str
    title: str
    prompt: str
    openfoam_target: OpenFOAMTarget
    resource_budget: ResourceBudget
    required_outputs: list[str]
    acceptance_requirements: list[str]
    public_checks: list[PublicCheck]
    public_assets: list[PublicAsset] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    geometry: GeometryInput | None = None
    mesh: MeshIntent | None = None


def load_legacy_task_spec_from_run(path: str | Path) -> LegacyTaskSpecV2:
    """Load a manifested historical task for reporting, not execution."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    payload = (
        json.loads(text)
        if source.suffix.lower() == ".json"
        else yaml.safe_load(text)
    )
    return LegacyTaskSpecV2.model_validate(payload)


__all__ = ["LegacyTaskSpecV2", "load_legacy_task_spec_from_run"]
