"""Structured, provenance-bearing OpenFOAM knowledge."""

from .coverage import (
    KnowledgeCoverageCell,
    KnowledgeCoverageReport,
    KnowledgeCoverageStatus,
    build_knowledge_coverage,
)
from .io import (
    build_knowledge_manifest,
    knowledge_entry_json_schema,
    load_knowledge_corpus,
    load_knowledge_entry,
    verify_knowledge_manifest,
)
from .models import (
    PILOT_FAMILIES,
    KnowledgeApplicability,
    KnowledgeContent,
    KnowledgeEntry,
    KnowledgeLeakage,
    KnowledgeSource,
)
from .retrieval import KnowledgeMatch, KnowledgeQuery, select_knowledge

__all__ = [
    "PILOT_FAMILIES",
    "KnowledgeCoverageCell",
    "KnowledgeCoverageReport",
    "KnowledgeCoverageStatus",
    "KnowledgeApplicability",
    "KnowledgeContent",
    "KnowledgeEntry",
    "KnowledgeLeakage",
    "KnowledgeMatch",
    "KnowledgeQuery",
    "KnowledgeSource",
    "build_knowledge_coverage",
    "build_knowledge_manifest",
    "knowledge_entry_json_schema",
    "load_knowledge_corpus",
    "load_knowledge_entry",
    "select_knowledge",
    "verify_knowledge_manifest",
]
