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
    "CaseDesignProposal",
    "DesignCandidate",
    "EvidenceSource",
    "FactEvidence",
    "ImpactLevel",
    "JsonValue",
    "ExtensionDecision",
    "RequirementConflict",
    "RequirementGap",
    "ResolvedValue",
    "ResolvedRequirements",
    "SimulationIntent",
    "Uncertainty",
    "canonical_sha256",
    "design_case",
    "interpret_intent",
    "resolve_requirements",
    "write_json_exclusive",
    "write_yaml_exclusive",
]


def __getattr__(name: str):
    """Load task-dependent stages lazily to keep provenance dependency-free."""

    if name in {
        "CaseDesignProposal",
        "ExtensionDecision",
        "RequirementConflict",
        "RequirementGap",
        "ResolvedRequirements",
        "SimulationIntent",
        "design_case",
        "interpret_intent",
        "resolve_requirements",
    }:
        from .design import CaseDesignProposal, ExtensionDecision, design_case
        from .intent import SimulationIntent, interpret_intent
        from .requirements import (
            RequirementConflict,
            RequirementGap,
            ResolvedRequirements,
            resolve_requirements,
        )

        return {
            "CaseDesignProposal": CaseDesignProposal,
            "ExtensionDecision": ExtensionDecision,
            "RequirementConflict": RequirementConflict,
            "RequirementGap": RequirementGap,
            "ResolvedRequirements": ResolvedRequirements,
            "SimulationIntent": SimulationIntent,
            "design_case": design_case,
            "interpret_intent": interpret_intent,
            "resolve_requirements": resolve_requirements,
        }[name]
    raise AttributeError(name)
