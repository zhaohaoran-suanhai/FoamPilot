"""Evidence extractor protocol and closed first-party registry."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from foampilot.plans import ExecutionPlan
from foampilot.runtime import PlanRunResult

from .models import RunFacts


class EvidenceExtractionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class EvidenceExtractor(Protocol):
    identity: str

    def extract(
        self,
        run_result: PlanRunResult,
        plan: ExecutionPlan,
        case_root: Path,
    ) -> RunFacts: ...


class EvidenceExtractorRegistry:
    """Closed registry keyed by exact OpenFOAM distribution and version."""

    def __init__(self, extractors: dict[tuple[str, str], EvidenceExtractor]):
        self._extractors = dict(extractors)

    @classmethod
    def first_party(cls) -> "EvidenceExtractorRegistry":
        from .openfoam10 import OpenFOAM10EvidenceExtractor

        return cls({("foundation", "10"): OpenFOAM10EvidenceExtractor()})

    def resolve(self, distribution: str, version: str) -> EvidenceExtractor:
        try:
            return self._extractors[(distribution, version)]
        except KeyError as error:
            raise LookupError(
                "EVIDENCE_EXTRACTOR_UNAVAILABLE: "
                f"{distribution} OpenFOAM {version}"
            ) from error


__all__ = [
    "EvidenceExtractionError",
    "EvidenceExtractor",
    "EvidenceExtractorRegistry",
]
