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
    "ResolvedValue",
    "SimulationIntent",
    "Uncertainty",
    "canonical_sha256",
    "interpret_intent",
    "write_json_exclusive",
    "write_yaml_exclusive",
]


def __getattr__(name: str):
    """Load task-dependent stages lazily to keep provenance dependency-free."""

    if name in {"SimulationIntent", "interpret_intent"}:
        from .intent import SimulationIntent, interpret_intent

        return {
            "SimulationIntent": SimulationIntent,
            "interpret_intent": interpret_intent,
        }[name]
    raise AttributeError(name)
