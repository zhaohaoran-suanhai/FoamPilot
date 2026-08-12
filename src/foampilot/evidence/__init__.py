"""Canonical native-run evidence contracts."""

from .models import (
    ContinuityFact,
    CourantFact,
    FieldOperationFact,
    MeshCheckFact,
    NativeErrorFact,
    RawCommandEvidence,
    ReusedCommandEvidence,
    ResidualFact,
    RunFacts,
    SolverProgressFact,
)
from .metrics import MetricPoint, MetricsProjection, MetricsWriter
from .telemetry import IncrementalOpenFOAMLogParser, ResidualMetric

__all__ = [
    "ContinuityFact",
    "CourantFact",
    "FieldOperationFact",
    "EvidenceExtractionError",
    "EvidenceExtractor",
    "EvidenceExtractorRegistry",
    "MeshCheckFact",
    "MetricPoint",
    "MetricsProjection",
    "MetricsWriter",
    "IncrementalOpenFOAMLogParser",
    "NativeErrorFact",
    "RawCommandEvidence",
    "ReusedCommandEvidence",
    "ResidualFact",
    "ResidualMetric",
    "RunFacts",
    "SolverProgressFact",
    "OpenFOAM10EvidenceExtractor",
]


def __getattr__(name: str):
    if name in {
        "EvidenceExtractionError",
        "EvidenceExtractor",
        "EvidenceExtractorRegistry",
    }:
        from .extractors import (
            EvidenceExtractionError,
            EvidenceExtractor,
            EvidenceExtractorRegistry,
        )

        return {
            "EvidenceExtractionError": EvidenceExtractionError,
            "EvidenceExtractor": EvidenceExtractor,
            "EvidenceExtractorRegistry": EvidenceExtractorRegistry,
        }[name]
    if name == "OpenFOAM10EvidenceExtractor":
        from .openfoam10 import OpenFOAM10EvidenceExtractor

        return OpenFOAM10EvidenceExtractor
    raise AttributeError(name)
