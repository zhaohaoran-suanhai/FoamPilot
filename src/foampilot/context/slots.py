"""Knowledge-slot definitions and deterministic pruning priority."""

from __future__ import annotations

from dataclasses import dataclass

from foampilot.knowledge.retrieval import KnowledgeType


@dataclass(frozen=True)
class ContextSlot:
    name: str
    knowledge_types: tuple[KnowledgeType, ...]
    query_terms: str
    solver_filtered: bool = True


BASE_SLOTS: tuple[ContextSlot, ...] = (
    ContextSlot(
        "solver_family_contract",
        ("solver_guide",),
        "solver family contract required files fields",
    ),
    ContextSlot(
        "mesh_pattern",
        ("mesh_pattern",),
        "mesh topology dimensionality patch pattern",
        solver_filtered=False,
    ),
    ContextSlot(
        "boundary_condition_contract",
        ("boundary_condition",),
        "boundary condition field patch contract",
    ),
    ContextSlot(
        "physics_transport_model",
        ("physics_model",),
        "physical transport thermophysical model properties",
    ),
    ContextSlot(
        "startup_numerics",
        ("numerics",),
        "conservative startup discretization time step bounded numerics",
    ),
)

PARALLEL_SLOT = ContextSlot(
    "parallel_execution",
    ("parallel_execution",),
    "parallel decomposition reconstruction mpi",
    solver_filtered=False,
)

ERROR_SLOT = ContextSlot(
    "error_playbook",
    ("error_playbook",),
    "failure error repair log diagnosis",
)

# Remove the least essential optional guidance first. Entries are never
# truncated.
PRUNE_ORDER: tuple[str, ...] = (
    "error_playbook",
    "parallel_execution",
    "startup_numerics",
    "boundary_condition_contract",
    "mesh_pattern",
    "physics_transport_model",
)
