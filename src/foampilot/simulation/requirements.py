"""Deterministic simulation-fact completeness and integrity resolution."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from foampilot.extensions import CapabilityDescriptor, RequiredFact
from foampilot.preprocessing.models import ExecutedMeshFacts, InputMeshFacts

from .intent import SimulationIntent
from .provenance import (
    DesignCandidate,
    FactEvidence,
    ResolvedValue,
    StrictModel,
)


class RequirementGap(StrictModel):
    field_path: str
    impact: Literal["low", "medium", "high"]
    kind: Literal["confirmable", "information_required", "design_required"]
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    description: str
    candidates: tuple[DesignCandidate, ...] = ()

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        if self.kind == "confirmable" and not self.candidates:
            raise ValueError("confirmable requirement gap needs a candidate")
        if self.kind in {"information_required", "design_required"} and self.candidates:
            raise ValueError(
                "candidate-free requirement gap forbids candidates"
            )
        return self


class RequirementConflict(StrictModel):
    field_path: str
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str
    evidence: tuple[FactEvidence, ...] = Field(min_length=1)


class ResolvedRequirements(StrictModel):
    schema_version: Literal[1] = 1
    resolved: tuple[ResolvedValue, ...]
    gaps: tuple[RequirementGap, ...]
    conflicts: tuple[RequirementConflict, ...]
    selected_extension_ids: tuple[str, ...]
    requirements_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def require(self, field_path: str) -> ResolvedValue:
        for item in self.resolved:
            if item.field_path == field_path:
                return item
        raise KeyError(field_path)

    def with_confirmations(
        self,
        values: tuple[ResolvedValue, ...],
    ) -> "ResolvedRequirements":
        """Replace confirmable gaps with concrete user-confirmed facts."""

        if any(
            item.source != "user_confirmation" or not item.confirmed
            for item in values
        ):
            raise ValueError("requirement confirmations need user authority")
        by_path = {item.field_path: item for item in self.resolved}
        for item in values:
            by_path[item.field_path] = item
        confirmed_paths = set(item.field_path for item in values)
        gaps = tuple(
            item
            for item in self.gaps
            if not (
                item.field_path in confirmed_paths
                and item.kind == "confirmable"
            )
        )
        resolved = tuple(by_path[path] for path in sorted(by_path))
        return ResolvedRequirements(
            resolved=resolved,
            gaps=gaps,
            conflicts=self.conflicts,
            selected_extension_ids=self.selected_extension_ids,
            requirements_sha256=_hash_payload(
                resolved,
                gaps,
                self.conflicts,
                self.selected_extension_ids,
            ),
        )


_SOURCE_PRECEDENCE = {
    "user_confirmation": 0,
    "public_asset_fact": 1,
    "user_text": 2,
    "deterministic_rule": 3,
    "system_default": 4,
    "model_inference": 5,
}


def _hash_payload(
    resolved: tuple[ResolvedValue, ...],
    gaps: tuple[RequirementGap, ...],
    conflicts: tuple[RequirementConflict, ...],
    extension_ids: tuple[str, ...],
) -> str:
    payload = {
        "schema_version": 1,
        "resolved": [item.model_dump(mode="json") for item in resolved],
        "gaps": [item.model_dump(mode="json") for item in gaps],
        "conflicts": [item.model_dump(mode="json") for item in conflicts],
        "selected_extension_ids": extension_ids,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _mesh_facts(meshes: tuple[InputMeshFacts, ...]) -> list[ResolvedValue]:
    values: list[ResolvedValue] = []
    for mesh in meshes:
        fact_id = f"mesh:{mesh.bundle_manifest_sha256}"
        evidence = (
            FactEvidence(
                kind="poly_mesh_inspector",
                detail="Authoritative InputMeshFacts",
                reference=fact_id,
            ),
        )
        values.append(
            ResolvedValue(
                field_path="geometry.length_unit",
                value=mesh.declared_length_unit,
                source="public_asset_fact",
                impact="high",
                evidence=evidence,
                confirmed=True,
            )
        )
        region_prefix = mesh.region or "default"
        values.append(
            ResolvedValue(
                field_path=f"mesh.regions.{region_prefix}.cells",
                value=mesh.cells,
                source="public_asset_fact",
                impact="high",
                evidence=evidence,
                confirmed=True,
            )
        )
        for zone in mesh.cell_zones:
            values.append(
                ResolvedValue(
                    field_path=f"mesh.cell_zones.{zone.name}.count",
                    value=zone.element_count,
                    source="public_asset_fact",
                    impact="high",
                    evidence=evidence,
                    confirmed=True,
                )
            )
        for patch in mesh.patches:
            values.append(
                ResolvedValue(
                    field_path=f"mesh.patches.{patch.name}.type",
                    value=patch.patch_type,
                    source="public_asset_fact",
                    impact="high",
                    evidence=evidence,
                    confirmed=True,
                )
            )
    return values


def _required_fields(
    capabilities: tuple[CapabilityDescriptor, ...],
) -> dict[str, RequiredFact]:
    required: dict[str, RequiredFact] = {}
    for descriptor in sorted(
        capabilities,
        key=lambda item: item.extension_id,
    ):
        for item in descriptor.required_facts:
            previous = required.get(item.field_path)
            if previous is None:
                required[item.field_path] = item
                continue
            rank = {"low": 0, "medium": 1, "high": 2}
            if rank[item.impact] > rank[previous.impact]:
                required[item.field_path] = item
    return required


def _executed_facts(
    executed: tuple[ExecutedMeshFacts, ...],
) -> list[ResolvedValue]:
    values: list[ResolvedValue] = []
    for index, facts in enumerate(executed):
        evidence = (
            FactEvidence(
                kind="mesh_probe",
                detail="Authoritative pre-authoring checkMesh probe",
                reference=f"mesh-probe:{index}",
            ),
        )
        observed = {
            "mesh.check.executed": facts.mesh_check.executed,
            "mesh.check.return_code": facts.mesh_check.return_code,
            "mesh.check.timed_out": facts.mesh_check.timed_out,
            "mesh.check.mesh_ok": facts.mesh_check.mesh_ok,
        }
        for path, value in observed.items():
            values.append(
                ResolvedValue(
                    field_path=path,
                    value=value,
                    source="public_asset_fact",
                    impact="high",
                    evidence=evidence,
                    confirmed=True,
                )
            )
    return values


def _candidate_for(fact: ResolvedValue) -> DesignCandidate:
    return DesignCandidate(
        candidate_id=(
            "candidate-" + sha256(
                json.dumps(
                    fact.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:12]
        ),
        value=fact.value,
        rationale="Model-inferred value requires concrete confirmation.",
        evidence=fact.evidence,
    )


def resolve_requirements(
    *,
    intent: SimulationIntent,
    mesh_facts: tuple[InputMeshFacts, ...],
    capabilities: tuple[CapabilityDescriptor, ...],
    executed_mesh_facts: tuple[ExecutedMeshFacts, ...] = (),
) -> ResolvedRequirements:
    """Merge facts by authority and expose gaps without inventing values."""

    candidates: dict[str, list[ResolvedValue]] = {}
    for fact in [
        *intent.facts,
        *_mesh_facts(mesh_facts),
        *_executed_facts(executed_mesh_facts),
    ]:
        candidates.setdefault(fact.field_path, []).append(fact)

    required = _required_fields(capabilities)
    resolved: list[ResolvedValue] = []
    gaps: list[RequirementGap] = []
    conflicts: list[RequirementConflict] = []

    for path, values in sorted(candidates.items()):
        ordered = sorted(values, key=lambda item: _SOURCE_PRECEDENCE[item.source])
        strongest_rank = _SOURCE_PRECEDENCE[ordered[0].source]
        strongest = [
            item
            for item in ordered
            if _SOURCE_PRECEDENCE[item.source] == strongest_rank
        ]
        serialized_values = {
            json.dumps(item.value, ensure_ascii=False, sort_keys=True)
            for item in strongest
        }
        if len(serialized_values) > 1:
            conflicts.append(
                RequirementConflict(
                    field_path=path,
                    code="EQUAL_AUTHORITY_CONFLICT",
                    detail="Equal-authority facts disagree.",
                    evidence=tuple(
                        evidence
                        for item in strongest
                        for evidence in item.evidence
                    ),
                )
            )
            continue
        selected = strongest[0]
        impact = required.get(path, None)
        effective_impact = impact.impact if impact is not None else selected.impact
        if (
            selected.source == "system_default"
            and effective_impact in {"medium", "high"}
        ):
            gaps.append(
                RequirementGap(
                    field_path=path,
                    impact=effective_impact,
                    kind="information_required",
                    code="HIGH_IMPACT_AUTHORITY_MISSING",
                    description=(
                        "A low-impact system default cannot satisfy this "
                        "required engineering fact."
                    ),
                )
            )
            continue
        if (
            selected.source == "model_inference"
            and effective_impact in {"medium", "high"}
            and path in required
        ):
            gaps.append(
                RequirementGap(
                    field_path=path,
                    impact=effective_impact,
                    kind="confirmable",
                    code="HIGH_IMPACT_AUTHORITY_MISSING",
                    description="Model inference requires concrete confirmation.",
                    candidates=(_candidate_for(selected),),
                )
            )
            continue
        resolved.append(selected)

    present = {item.field_path for item in resolved}
    gap_paths = {item.field_path for item in gaps}
    conflict_paths = {item.field_path for item in conflicts}
    for path, requirement in sorted(required.items()):
        if path in present or path in gap_paths or path in conflict_paths:
            continue
        gaps.append(
            RequirementGap(
                field_path=path,
                impact=requirement.impact,
                kind=(
                    "design_required"
                    if requirement.resolution == "designer_candidate"
                    else "information_required"
                ),
                code="REQUIRED_FACT_MISSING",
                description=requirement.description,
            )
        )

    available_zones = {
        zone.name
        for mesh in mesh_facts
        for zone in mesh.cell_zones
        if zone.element_count > 0
    }
    available_patches = {
        patch.name
        for mesh in mesh_facts
        for patch in mesh.patches
        if patch.face_count > 0
    }
    for fact in tuple(resolved):
        parts = fact.field_path.split(".")
        if len(parts) >= 3 and parts[0] == "regions" and parts[2] == "role":
            zone = parts[1]
            if mesh_facts and zone not in available_zones:
                resolved.remove(fact)
                gaps.append(
                    RequirementGap(
                        field_path=fact.field_path,
                        impact=fact.impact,
                        kind="information_required",
                        code="MESH_ZONE_REFERENCE_MISSING",
                        description=(
                            f"Referenced cellZone is absent or empty: {zone}"
                        ),
                    )
                )
        elif (
            len(parts) >= 3
            and parts[0] == "boundaries"
            and parts[2] == "role"
        ):
            patch = parts[1]
            if mesh_facts and patch not in available_patches:
                resolved.remove(fact)
                gaps.append(
                    RequirementGap(
                        field_path=fact.field_path,
                        impact=fact.impact,
                        kind="information_required",
                        code="MESH_PATCH_REFERENCE_MISSING",
                        description=(
                            f"Referenced mesh patch is absent or empty: {patch}"
                        ),
                    )
                )

    ordered_resolved = tuple(sorted(resolved, key=lambda item: item.field_path))
    ordered_gaps = tuple(sorted(gaps, key=lambda item: (item.field_path, item.code)))
    ordered_conflicts = tuple(
        sorted(conflicts, key=lambda item: (item.field_path, item.code))
    )
    extension_ids = tuple(sorted(item.extension_id for item in capabilities))
    return ResolvedRequirements(
        resolved=ordered_resolved,
        gaps=ordered_gaps,
        conflicts=ordered_conflicts,
        selected_extension_ids=extension_ids,
        requirements_sha256=_hash_payload(
            ordered_resolved,
            ordered_gaps,
            ordered_conflicts,
            extension_ids,
        ),
    )


__all__ = [
    "RequirementConflict",
    "RequirementGap",
    "ResolvedRequirements",
    "resolve_requirements",
]
