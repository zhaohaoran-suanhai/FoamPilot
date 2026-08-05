"""Deterministic solver-family coverage over the public knowledge corpus."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import KnowledgeEntry
from .retrieval import KnowledgeType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeCoverageStatus(StrEnum):
    COVERED = "covered"
    PARTIAL = "partial"
    MISSING = "missing"


FAMILY_SOLVERS: dict[str, tuple[str, ...]] = {
    "buoyant-cht": ("buoyantFoam", "chtMultiRegionFoam"),
    "compressible-transient": (
        "rhoCentralFoam",
        "rhoPimpleFoam",
        "rhoSimpleFoam",
    ),
    "incompressible-pressure-velocity": (
        "icoFoam",
        "pimpleFoam",
        "pisoFoam",
        "porousSimpleFoam",
        "simpleFoam",
        "SRFPimpleFoam",
        "SRFSimpleFoam",
    ),
    "multiphase-vof": ("interFoam", "twoLiquidMixingFoam"),
    "scalar-field-transport": (
        "electrostaticFoam",
        "scalarTransportFoam",
    ),
    "solid-mechanics": (
        "solidDisplacementFoam",
        "solidEquilibriumDisplacementFoam",
    ),
}


COVERAGE_TYPES: tuple[KnowledgeType, ...] = (
    "solver_guide",
    "mesh_pattern",
    "boundary_condition",
    "physics_model",
    "numerics",
    "error_playbook",
    "validation_pattern",
)


class KnowledgeCoverageCell(StrictModel):
    family: str
    knowledge_type: KnowledgeType
    status: KnowledgeCoverageStatus
    solvers: tuple[str, ...]
    covered_solvers: tuple[str, ...]
    entry_ids: tuple[str, ...]


class KnowledgeCoverageReport(StrictModel):
    schema_version: Literal[1] = 1
    families: tuple[str, ...]
    knowledge_types: tuple[KnowledgeType, ...]
    cells: tuple[KnowledgeCoverageCell, ...]


def build_knowledge_coverage(
    entries: tuple[KnowledgeEntry, ...] | list[KnowledgeEntry],
) -> KnowledgeCoverageReport:
    """Describe corpus presence without claiming demonstrated capability."""

    families = tuple(sorted(FAMILY_SOLVERS))
    cells: list[KnowledgeCoverageCell] = []
    for family in families:
        solvers = FAMILY_SOLVERS[family]
        solver_set = set(solvers)
        for knowledge_type in COVERAGE_TYPES:
            candidates = tuple(
                entry
                for entry in entries
                if entry.knowledge_type == knowledge_type
                and entry.leakage.visibility == "public"
            )
            universal_entries = tuple(
                entry
                for entry in candidates
                if not entry.solvers
                and not entry.models
                and not entry.activation_terms
            )
            universal = bool(universal_entries)
            covered_solvers = tuple(
                solver
                for solver in solvers
                if universal
                or any(solver in entry.solvers for entry in candidates)
            )
            entry_ids = tuple(
                sorted(
                    entry.id
                    for entry in candidates
                    if entry in universal_entries
                    or set(entry.solvers) & solver_set
                )
            )
            status = (
                KnowledgeCoverageStatus.COVERED
                if len(covered_solvers) == len(solvers)
                else (
                    KnowledgeCoverageStatus.PARTIAL
                    if covered_solvers
                    else KnowledgeCoverageStatus.MISSING
                )
            )
            cells.append(
                KnowledgeCoverageCell(
                    family=family,
                    knowledge_type=knowledge_type,
                    status=status,
                    solvers=solvers,
                    covered_solvers=covered_solvers,
                    entry_ids=entry_ids,
                )
            )
    return KnowledgeCoverageReport(
        families=families,
        knowledge_types=COVERAGE_TYPES,
        cells=tuple(cells),
    )
