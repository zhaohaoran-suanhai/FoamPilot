"""One-call native case authoring from one frozen design."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from .models import CaseBundle

if TYPE_CHECKING:
    from foampilot.context.models import AgentContext
    from foampilot.preprocessing.models import GeometryFacts, InputMeshFacts
    from foampilot.simulation.risk_gate import CaseDesign


_AUTHOR_REQUEST_LIMIT_BYTES = 96 * 1024
_PUBLIC_CONTEXT_LIMIT_BYTES = 48 * 1024


class CaseAuthoringError(ValueError):
    """The model response violated the frozen authoring boundary."""


class AuthorTargetFacts(BaseModel):
    """Target facts with a deliberately smaller agent-visible projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    distribution: Literal["foundation"]
    version: str = Field(pattern=r"^[0-9]+$")
    solver_executable: str = Field(pattern=r"^[A-Za-z0-9_.+-]+$")
    required_outputs: tuple[str, ...] = Field(min_length=1)
    required_authored_paths: tuple[str, ...] = Field(min_length=1)
    extension_authoring_rules: tuple[str, ...] = ()
    public_asset_install_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()

    @field_validator(
        "required_outputs",
        "required_authored_paths",
        "extension_authoring_rules",
        "public_asset_install_paths",
        "protected_paths",
    )
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in values):
            raise ValueError("author target values must not be blank")
        if len(values) != len(set(values)):
            raise ValueError("author target values must be unique")
        return values

    @field_validator("required_authored_paths", "public_asset_install_paths")
    @classmethod
    def validate_relative_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("author target case paths must be safe and relative")
            normalized.append(path.as_posix())
        return tuple(normalized)

    def agent_payload(self) -> dict[str, object]:
        """Return the only target fields permitted in a model request."""

        return self.model_dump(mode="json", exclude={"protected_paths"})


def _mesh_summary(facts: InputMeshFacts) -> dict[str, object]:
    return {
        "fact_id": f"mesh:{facts.bundle_manifest_sha256}",
        "region": facts.region,
        "declared_length_unit": facts.declared_length_unit,
        "points": facts.points,
        "faces": facts.faces,
        "internal_faces": facts.internal_faces,
        "cells": facts.cells,
        "bounding_box_m": facts.bounding_box_m.model_dump(mode="json"),
        "patches": [
            {
                "name": item.name,
                "type": item.patch_type,
                "face_count": item.face_count,
            }
            for item in facts.patches
        ],
        "cell_zones": [
            {"name": item.name, "element_count": item.element_count}
            for item in facts.cell_zones
        ],
        "face_zones": [
            {"name": item.name, "element_count": item.element_count}
            for item in facts.face_zones
        ],
        "point_zones": [
            {"name": item.name, "element_count": item.element_count}
            for item in facts.point_zones
        ],
        "dimensionality_observations": facts.dimensionality_observations,
        "topology_observations": facts.topology_observations,
        "warnings": facts.warnings,
        "raw_content_included": False,
    }


def _public_context(context: AgentContext) -> dict[str, object]:
    payload: dict[str, object] = {
        "knowledge_text": context.knowledge_text,
        "skills_text": context.skills_text,
        "selected_knowledge_ids": context.selected_knowledge_ids,
        "selected_source_hashes": context.selected_source_hashes,
        "skill_names": context.skill_names,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _PUBLIC_CONTEXT_LIMIT_BYTES:
        raise CaseAuthoringError("AUTHOR_PUBLIC_CONTEXT_BUDGET_EXCEEDED")
    return payload


def _safe_case_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and ".foampilot" not in path.parts
    )


def _validate_bundle(
    bundle: CaseBundle,
    *,
    design: CaseDesign,
    target: AuthorTargetFacts,
) -> None:
    expected_solver = str(design.proposal.solver_family.value)
    actual_solver = bundle.manifest.solver_executable
    if target.solver_executable != expected_solver:
        raise CaseAuthoringError(
            "AUTHOR_TARGET_DESIGN_MISMATCH: target solver differs from design"
        )
    if actual_solver != expected_solver:
        raise CaseAuthoringError(
            f"AUTHOR_SOLVER_MISMATCH: {actual_solver} != {expected_solver}"
        )

    file_paths = {item.path for item in bundle.files}
    unsafe = sorted(item for item in file_paths if not _safe_case_path(item))
    if unsafe:
        raise CaseAuthoringError(
            "AUTHOR_UNSAFE_FILE_PATH: " + ", ".join(unsafe)
        )

    for asset_path in target.public_asset_install_paths:
        overlaps = sorted(
            item
            for item in file_paths
            if item == asset_path or item.startswith(f"{asset_path}/")
        )
        if overlaps:
            raise CaseAuthoringError(
                "AUTHOR_PUBLIC_ASSET_OVERWRITE: " + ", ".join(overlaps)
            )

    for generated in bundle.files:
        if any(path in generated.content for path in target.protected_paths):
            raise CaseAuthoringError(
                f"AUTHOR_PROTECTED_PATH_LEAK: {generated.path}"
            )

    missing_required = sorted(
        set(target.required_authored_paths) - file_paths
    )
    if missing_required:
        raise CaseAuthoringError(
            "AUTHOR_REQUIRED_FILE_MISSING: " + ", ".join(missing_required)
        )

    missing_manifest = sorted(
        field.path
        for field in bundle.manifest.fields
        if field.created_by == "author" and field.path not in file_paths
    )
    if missing_manifest:
        raise CaseAuthoringError(
            "AUTHOR_MANIFEST_FILE_MISSING: " + ", ".join(missing_manifest)
        )


def _bind_manifest_family_metadata(
    bundle: CaseBundle,
    design: CaseDesign,
) -> CaseBundle:
    facts = {
        item.field_path: item.value for item in design.proposal.iter_values()
    }
    updates: dict[str, str] = {}
    physics_family = facts.get("physics.family")
    mesh_family = facts.get("mesh.strategy")
    if physics_family is not None:
        updates["physics_family"] = str(physics_family)
    if mesh_family is not None:
        updates["mesh_family"] = str(mesh_family)
    if not updates:
        return bundle
    return bundle.model_copy(
        update={"manifest": bundle.manifest.model_copy(update=updates)}
    )


def author_case(
    *,
    design: CaseDesign,
    mesh_facts: tuple[InputMeshFacts, ...],
    geometry_facts: GeometryFacts | None = None,
    target_facts: AuthorTargetFacts,
    context: AgentContext,
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> CaseBundle:
    """Author every related native case file in one logical model call."""

    payload = {
        "frozen_case_design": design.model_dump(mode="json"),
        "authoritative_input_mesh_facts": [
            _mesh_summary(item) for item in mesh_facts
        ],
        "authoritative_geometry_facts": (
            geometry_facts.model_dump(mode="json")
            if geometry_facts is not None
            else None
        ),
        "target_facts": target_facts.agent_payload(),
        "public_context": _public_context(context),
    }
    user_prompt = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if len(user_prompt.encode("utf-8")) > _AUTHOR_REQUEST_LIMIT_BYTES:
        raise CaseAuthoringError("AUTHOR_CONTEXT_BUDGET_EXCEEDED")

    request = ModelRequest(
        purpose="author-openfoam-case",
        system_prompt=(
            "Implement the frozen CaseDesign exactly for the declared OpenFOAM "
            "target. Return only one CaseBundle schema_version 1 containing the "
            "complete CaseManifest and every mutually related authored file. "
            "Do not return execution steps, argv, scripts, repair proposals, or "
            "revisions to the design. Do not regenerate or overwrite public "
            "assets. Use only the bounded public context and authoritative mesh "
            "facts supplied in this request. Manifest physics_family and "
            "mesh_family must use the exact frozen physics.family and "
            "mesh.strategy values. Do not author functions dictionaries, "
            "function objects, or observation configuration; FoamPilot owns "
            "and injects all runtime observation configuration after authoring."
        ),
        user_prompt=user_prompt,
    )
    response = gateway.generate_structured(
        request,
        CaseBundle,
        budget=budget,
        trace=trace,
    ).value
    response = _bind_manifest_family_metadata(response, design)
    _validate_bundle(response, design=design, target=target_facts)
    return response


def canonical_author_response_type() -> type[CaseBundle]:
    """Expose the sole model-authored artifact type for boundary tests."""

    return CaseBundle


__all__ = [
    "AuthorTargetFacts",
    "CaseAuthoringError",
    "author_case",
    "canonical_author_response_type",
]
