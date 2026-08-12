"""Manifest-bound read-only loader for historical ExecutionPlan v3."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.manifests import CaseManifest

from .models import GeneratedFile, NativeCommand


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LegacyExecutionPlanV3(_StrictModel):
    schema_version: Literal[3]
    manifest: CaseManifest
    files: list[GeneratedFile] = Field(min_length=1)
    commands: list[NativeCommand] = Field(min_length=1)


def load_legacy_execution_plan_v3_for_replay(
    path: str | Path,
    *,
    expected_sha256: str,
) -> LegacyExecutionPlanV3:
    """Load only a caller-manifested v3 artifact for read-only replay."""

    source = Path(path)
    payload = source.read_bytes()
    actual = sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            "LEGACY_PLAN_HASH_MISMATCH: "
            f"expected {expected_sha256}, got {actual}"
        )
    return LegacyExecutionPlanV3.model_validate_json(payload)


__all__ = [
    "LegacyExecutionPlanV3",
    "load_legacy_execution_plan_v3_for_replay",
]
