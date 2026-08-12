"""Canonical native-run evidence contracts."""

from .models import (
    ContinuityFact,
    CourantFact,
    MeshCheckFact,
    NativeErrorFact,
    RawCommandEvidence,
    ResidualFact,
    RunFacts,
    SolverProgressFact,
)
from .extractors import (
    EvidenceExtractionError,
    EvidenceExtractor,
    EvidenceExtractorRegistry,
)
from .openfoam10 import OpenFOAM10EvidenceExtractor

__all__ = [
    "ContinuityFact",
    "CourantFact",
    "EvidenceExtractionError",
    "EvidenceExtractor",
    "EvidenceExtractorRegistry",
    "MeshCheckFact",
    "NativeErrorFact",
    "RawCommandEvidence",
    "ResidualFact",
    "RunFacts",
    "SolverProgressFact",
    "OpenFOAM10EvidenceExtractor",
]
