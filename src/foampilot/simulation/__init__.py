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
    "CaseDesign",
    "DesignCandidate",
    "EvidenceSource",
    "FactEvidence",
    "ImpactLevel",
    "IntentUncertainty",
    "JsonValue",
    "ExtensionDecision",
    "RequirementConflict",
    "RequirementGap",
    "ResolvedValue",
    "ResolvedRequirements",
    "RiskDecision",
    "RiskGateError",
    "RiskState",
    "SimulationIntent",
    "Uncertainty",
    "canonical_sha256",
    "design_case",
    "evaluate_design_risk",
    "freeze_case_design",
    "interpret_intent",
    "resolve_requirements",
    "write_json_exclusive",
    "write_yaml_exclusive",
]


def __getattr__(name: str):
    """Load task-dependent stages lazily to keep provenance dependency-free."""

    if name in {
        "CaseDesignProposal",
        "CaseDesign",
        "ExtensionDecision",
        "RequirementConflict",
        "RequirementGap",
        "ResolvedRequirements",
        "RiskDecision",
        "RiskGateError",
        "RiskState",
        "SimulationIntent",
        "IntentUncertainty",
        "design_case",
        "evaluate_design_risk",
        "freeze_case_design",
        "interpret_intent",
        "resolve_requirements",
    }:
        from .design import CaseDesignProposal, ExtensionDecision, design_case
        from .intent import IntentUncertainty, SimulationIntent, interpret_intent
        from .requirements import (
            RequirementConflict,
            RequirementGap,
            ResolvedRequirements,
            resolve_requirements,
        )
        from .risk_gate import (
            CaseDesign,
            RiskDecision,
            RiskGateError,
            RiskState,
            evaluate_design_risk,
            freeze_case_design,
        )

        return {
            "CaseDesignProposal": CaseDesignProposal,
            "CaseDesign": CaseDesign,
            "ExtensionDecision": ExtensionDecision,
            "RequirementConflict": RequirementConflict,
            "RequirementGap": RequirementGap,
            "ResolvedRequirements": ResolvedRequirements,
            "RiskDecision": RiskDecision,
            "RiskGateError": RiskGateError,
            "RiskState": RiskState,
            "SimulationIntent": SimulationIntent,
            "IntentUncertainty": IntentUncertainty,
            "design_case": design_case,
            "evaluate_design_risk": evaluate_design_risk,
            "freeze_case_design": freeze_case_design,
            "interpret_intent": interpret_intent,
            "resolve_requirements": resolve_requirements,
        }[name]
    raise AttributeError(name)
