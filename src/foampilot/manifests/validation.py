"""Standalone validation entrypoint for thin case manifests."""

from __future__ import annotations

from .models import CaseManifest


def validate_case_manifest(value: object) -> CaseManifest:
    return CaseManifest.model_validate(value)
