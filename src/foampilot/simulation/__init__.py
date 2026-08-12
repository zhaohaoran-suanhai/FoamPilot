"""Public contract-first simulation reasoning interfaces."""

from .io import canonical_sha256, write_json_exclusive, write_yaml_exclusive
from .provenance import (
    ConfirmationRecord,
    DesignCandidate,
    EvidenceSource,
    FactEvidence,
    ImpactLevel,
    JsonValue,
    ResolvedValue,
    Uncertainty,
)

__all__ = [
    "ConfirmationRecord",
    "DesignCandidate",
    "EvidenceSource",
    "FactEvidence",
    "ImpactLevel",
    "JsonValue",
    "RequirementConflict",
    "RequirementGap",
    "ResolvedValue",
    "ResolvedRequirements",
    "SimulationIntent",
    "Uncertainty",
    "canonical_sha256",
    "interpret_intent",
    "resolve_requirements",
    "write_json_exclusive",
    "write_yaml_exclusive",
]


def __getattr__(name: str):
    """Load task-dependent stages lazily to keep provenance dependency-free."""

    if name in {
        "RequirementConflict",
        "RequirementGap",
        "ResolvedRequirements",
        "SimulationIntent",
        "interpret_intent",
        "resolve_requirements",
    }:
        from .intent import SimulationIntent, interpret_intent
        from .requirements import (
            RequirementConflict,
            RequirementGap,
            ResolvedRequirements,
            resolve_requirements,
        )

        return {
            "RequirementConflict": RequirementConflict,
            "RequirementGap": RequirementGap,
            "ResolvedRequirements": ResolvedRequirements,
            "SimulationIntent": SimulationIntent,
            "interpret_intent": interpret_intent,
            "resolve_requirements": resolve_requirements,
        }[name]
    raise AttributeError(name)
