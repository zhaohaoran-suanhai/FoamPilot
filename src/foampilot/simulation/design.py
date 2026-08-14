"""Design-only model stage and deterministic capability reconciliation."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from foampilot.context import AgentContext, public_design_context
from foampilot.extensions import CapabilityRegistry
from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
    StructuredOutputNormalization,
)
from foampilot.preprocessing.models import InputMeshFacts
from foampilot.tasks import TaskSpec

from .intent import SimulationIntent
from .provenance import (
    FactEvidence,
    JsonValue,
    ResolvedValue,
    StrictModel,
    Uncertainty,
)
from .requirements import ResolvedRequirements


_DESIGN_REQUEST_LIMIT_BYTES = 64 * 1024
_SECTIONS = (
    "physical_models",
    "materials",
    "boundary_designs",
    "initial_conditions",
    "time_design",
    "numerical_design",
    "region_models",
)


class ExtensionDecision(StrictModel):
    extension_id: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
    )
    schema_version: int = Field(ge=1)
    values: tuple[ResolvedValue[JsonValue], ...]
    provenance: tuple[FactEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_values(self) -> Self:
        paths = [item.field_path for item in self.values]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate extension decision field paths")
        return self


class CaseDesignProposal(StrictModel):
    schema_version: Literal[1] = 1
    solver_family: ResolvedValue[str]
    physical_models: tuple[ResolvedValue[JsonValue], ...]
    materials: tuple[ResolvedValue[JsonValue], ...]
    boundary_designs: tuple[ResolvedValue[JsonValue], ...]
    initial_conditions: tuple[ResolvedValue[JsonValue], ...]
    time_design: tuple[ResolvedValue[JsonValue], ...]
    numerical_design: tuple[ResolvedValue[JsonValue], ...]
    region_models: tuple[ResolvedValue[JsonValue], ...]
    extension_decisions: tuple[ExtensionDecision, ...]
    uncertainties: tuple[Uncertainty, ...]
    alternatives: tuple[str, ...]
    reasoning_evidence: tuple[FactEvidence, ...] = Field(min_length=1)
    capability_conflicts: tuple[str, ...]

    @field_validator("alternatives", "capability_conflicts")
    @classmethod
    def normalize_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("design text entries must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate design text entries are not allowed")
        return normalized

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.solver_family.field_path != "solver.family":
            raise ValueError("solver_family must use field path solver.family")
        extension_ids = [item.extension_id for item in self.extension_decisions]
        if len(extension_ids) != len(set(extension_ids)):
            raise ValueError("duplicate extension decisions are not allowed")
        facts = list(self.iter_values())
        paths = [item.field_path for item in facts]
        if len(paths) != len(set(paths)):
            duplicates = sorted(
                path for path, count in Counter(paths).items() if count > 1
            )
            raise ValueError(
                "duplicate case design field paths are not allowed: "
                + ", ".join(duplicates)
            )
        return self

    def iter_values(self) -> tuple[ResolvedValue, ...]:
        values = [self.solver_family]
        for section in _SECTIONS:
            values.extend(getattr(self, section))
        for decision in self.extension_decisions:
            values.extend(decision.values)
        return tuple(values)


def normalize_case_design_input(
    output_text: str,
) -> tuple[CaseDesignProposal, tuple[StructuredOutputNormalization, ...]]:
    """Parse one Case Designer response without semantic repair."""

    return CaseDesignProposal.model_validate(json.loads(output_text)), ()


def _mesh_summary(facts: InputMeshFacts) -> dict[str, object]:
    return {
        "fact_id": f"mesh:{facts.bundle_manifest_sha256}",
        "region": facts.region,
        "declared_length_unit": facts.declared_length_unit,
        "points": facts.points,
        "faces": facts.faces,
        "cells": facts.cells,
        "bounding_box_m": facts.bounding_box_m.model_dump(mode="json"),
        "patches": [
            {
                "name": patch.name,
                "type": patch.patch_type,
                "face_count": patch.face_count,
            }
            for patch in facts.patches
        ],
        "cell_zones": [
            {"name": zone.name, "element_count": zone.element_count}
            for zone in facts.cell_zones
        ],
        "dimensionality_observations": facts.dimensionality_observations,
        "warnings": facts.warnings,
        "raw_content_included": False,
    }


def _registry_summary(registry: CapabilityRegistry) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "extension_id": descriptor.extension_id,
            "extension_version": descriptor.extension_version,
            "protocol_version": descriptor.protocol_version,
            "capability_kinds": descriptor.capability_kinds,
            "supported_targets": [
                item.model_dump(mode="json")
                for item in descriptor.supported_targets
            ],
            "required_executables": descriptor.required_executables,
            "required_facts": [
                item.model_dump(mode="json") for item in descriptor.required_facts
            ],
            "compatible_extensions": descriptor.compatible_extensions,
            "incompatible_extensions": descriptor.incompatible_extensions,
        }
        for descriptor in (
            registry.descriptor(extension_id)
            for extension_id in registry.extension_ids()
        )
    )


def _model_fact(fact: ResolvedValue) -> ResolvedValue:
    if fact.source == "model_inference" and not fact.confirmed:
        return fact
    return fact.model_copy(
        update={
            "source": "model_inference",
            "confirmed": False,
            "evidence": (
                *fact.evidence,
                FactEvidence(
                    kind="authority_audit",
                    detail=(
                        "Case Designer cannot self-assert concrete fact authority"
                    ),
                ),
            ),
        }
    )


def _section_for(path: str) -> str | None:
    prefix = path.split(".", 1)[0]
    return {
        "physics": "physical_models",
        "materials": "materials",
        "boundaries": "boundary_designs",
        "initial": "initial_conditions",
        "time": "time_design",
        "numerics": "numerical_design",
        "regions": "region_models",
    }.get(prefix)


def _confirm_authoritative_composition(
    fact: ResolvedValue,
    authoritative: dict[str, ResolvedValue],
) -> ResolvedValue:
    """Confirm an aggregate only when all direct child facts exactly compose it."""

    if not isinstance(fact.value, dict) or not fact.value:
        return fact
    prefix = fact.field_path + "."
    children = {
        path.removeprefix(prefix): child
        for path, child in authoritative.items()
        if path.startswith(prefix) and "." not in path.removeprefix(prefix)
    }
    if set(children) != set(fact.value):
        return fact
    if any(fact.value[key] != children[key].value for key in children):
        return fact
    evidence: list[FactEvidence] = []
    for key in sorted(children):
        evidence.extend(
            item for item in children[key].evidence if item not in evidence
        )
    evidence.append(
        FactEvidence(
            kind="deterministic_composition",
            detail=(
                f"{fact.field_path} exactly composes all confirmed direct child facts"
            ),
        )
    )
    return fact.model_copy(
        update={
            "source": "deterministic_rule",
            "confirmed": True,
            "evidence": tuple(evidence),
        }
    )


def _replace_facts(
    proposal: CaseDesignProposal,
    requirements: ResolvedRequirements,
) -> tuple[CaseDesignProposal, list[str]]:
    authoritative = {
        item.field_path: item
        for item in requirements.resolved
        if item.confirmed and item.impact in {"medium", "high"}
    }
    conflicts: list[str] = []
    updates: dict[str, object] = {}
    seen: set[str] = set()

    proposed_solver = _model_fact(proposal.solver_family)
    solver = authoritative.get("solver.family", proposed_solver)
    if (
        "solver.family" in authoritative
        and proposed_solver.value != solver.value
    ):
        conflicts.append("model design contradicts resolved fact: solver.family")
    updates["solver_family"] = solver
    seen.add("solver.family")

    for section in _SECTIONS:
        reconciled: list[ResolvedValue] = []
        for proposed in getattr(proposal, section):
            model_fact = _model_fact(proposed)
            supported = _confirm_authoritative_composition(
                model_fact,
                authoritative,
            )
            selected = authoritative.get(model_fact.field_path, supported)
            if (
                model_fact.field_path in authoritative
                and model_fact.value != selected.value
            ):
                conflicts.append(
                    "model design contradicts resolved fact: "
                    + model_fact.field_path
                )
            reconciled.append(selected)
            seen.add(selected.field_path)
        updates[section] = reconciled

    decisions: list[ExtensionDecision] = []
    for decision in proposal.extension_decisions:
        values: list[ResolvedValue] = []
        for proposed in decision.values:
            model_fact = _model_fact(proposed)
            supported = _confirm_authoritative_composition(
                model_fact,
                authoritative,
            )
            selected = authoritative.get(model_fact.field_path, supported)
            if (
                model_fact.field_path in authoritative
                and model_fact.value != selected.value
            ):
                conflicts.append(
                    "model design contradicts resolved fact: "
                    + model_fact.field_path
                )
            values.append(selected)
            seen.add(selected.field_path)
        decisions.append(decision.model_copy(update={"values": tuple(values)}))
    updates["extension_decisions"] = tuple(decisions)

    for path, fact in sorted(authoritative.items()):
        if path in seen:
            continue
        section = _section_for(path)
        if section is not None:
            assert isinstance(updates[section], list)
            updates[section].append(fact)
            seen.add(path)

    updates["capability_conflicts"] = ()
    for section in _SECTIONS:
        updates[section] = tuple(
            sorted(updates[section], key=lambda item: item.field_path)  # type: ignore[arg-type]
        )
    payload = proposal.model_dump(mode="json")
    payload.update(updates)
    return CaseDesignProposal.model_validate(payload), conflicts


def _capability_conflicts(
    proposal: CaseDesignProposal,
    *,
    task: TaskSpec,
    registry: CapabilityRegistry,
    available_executables: tuple[str, ...],
) -> tuple[str, ...]:
    conflicts: list[str] = []
    selected = tuple(
        decision.extension_id for decision in proposal.extension_decisions
    )
    descriptors = {}
    for extension_id in selected:
        try:
            descriptor = registry.descriptor(extension_id)
        except LookupError:
            conflicts.append(f"extension is not registered: {extension_id}")
            continue
        descriptors[extension_id] = descriptor
        if not descriptor.supports_target(
            task.openfoam_target.distribution,
            task.openfoam_target.version,
        ):
            conflicts.append(f"extension target is unsupported: {extension_id}")
        required = set(descriptor.required_executables)
        available = {Path(item).name for item in available_executables}
        for executable in sorted(required - available):
            conflicts.append(f"required executable is unavailable: {executable}")
        decision = next(
            item for item in proposal.extension_decisions
            if item.extension_id == extension_id
        )
        if decision.schema_version != descriptor.protocol_version:
            conflicts.append(
                "extension protocol version mismatch: " + extension_id
            )

    solver = str(proposal.solver_family.value)
    solver_descriptors = [
        registry.descriptor(extension_id)
        for extension_id in registry.extension_ids()
        if any(
            kind.partition(":")[0] == "solver"
            and kind.partition(":")[2].casefold() == solver.casefold()
            for kind in registry.descriptor(extension_id).capability_kinds
        )
    ]
    if not solver_descriptors:
        conflicts.append(f"solver family is not registered: {solver}")
    elif not any(item.extension_id in selected for item in solver_descriptors):
        conflicts.append(f"solver extension was not selected: {solver}")

    selected_set = set(selected)
    for extension_id, descriptor in sorted(descriptors.items()):
        for other in sorted(
            selected_set & set(descriptor.incompatible_extensions)
        ):
            pair = ", ".join(sorted((extension_id, other)))
            conflicts.append(f"extensions are incompatible: {pair}")
    return tuple(dict.fromkeys(conflicts))


def design_case(
    *,
    task: TaskSpec,
    intent: SimulationIntent,
    requirements: ResolvedRequirements,
    mesh_facts: tuple[InputMeshFacts, ...],
    registry: CapabilityRegistry,
    context: AgentContext,
    available_executables: tuple[str, ...],
    gateway: ModelGateway,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> CaseDesignProposal:
    """Propose one coherent design without authoring files or commands."""

    intent_payload = intent.model_dump(
        mode="json",
        include={"constraints", "uncertainties"},
    )
    payload = {
        "target": task.openfoam_target.model_dump(mode="json"),
        "SimulationIntent": intent_payload,
        "ResolvedRequirements": requirements.model_dump(mode="json"),
        "InputMeshFacts": [_mesh_summary(item) for item in mesh_facts],
        "capability_registry": _registry_summary(registry),
        "available_executables": tuple(
            sorted({Path(item).name for item in available_executables})
        ),
        "public_context": public_design_context(context),
    }
    user_prompt = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(user_prompt.encode("utf-8")) > _DESIGN_REQUEST_LIMIT_BYTES:
        raise ValueError("design context exceeds the model request budget")
    request = ModelRequest(
        purpose="design-openfoam-case",
        system_prompt=(
            "Design one coherent CFD case from the frozen facts. Return only a "
            "CaseDesignProposal. Do not write native OpenFOAM file content, "
            "commands, scripts, paths, evaluator data, or confidence scores. "
            "All model-originated decisions use model_inference authority and "
            "remain unconfirmed. Express missing facts as uncertainties. "
            "Each field_path must occur exactly once globally across "
            "solver_family, all section arrays, and extension_decisions.values. "
            "Put an extension-owned fact only in that extension's "
            "extension_decisions.values; do not repeat it in a section array. "
            "Use the exact required fact field_paths from capability_registry; "
            "do not invent aliases for those paths. "
            "Never emit an empty evidence or provenance array; every candidate "
            "must include truthful provenance for why it was proposed."
        ),
        user_prompt=user_prompt,
    )
    response = gateway.generate_structured(
        request,
        CaseDesignProposal,
        budget=budget,
        trace=trace,
        output_normalizer=normalize_case_design_input,
    ).value
    reconciled, fact_conflicts = _replace_facts(response, requirements)
    capability_conflicts = _capability_conflicts(
        reconciled,
        task=task,
        registry=registry,
        available_executables=available_executables,
    )
    payload = reconciled.model_dump(mode="json")
    payload["capability_conflicts"] = tuple(
        dict.fromkeys((*fact_conflicts, *capability_conflicts))
    )
    return CaseDesignProposal.model_validate(payload)


__all__ = [
    "CaseDesignProposal",
    "ExtensionDecision",
    "design_case",
    "normalize_case_design_input",
]
