"""Performance evidence, explicit reuse, and deterministic cache policy."""

from .models import (
    ModelPerformance,
    PathKind,
    PerformanceReuse,
    PerformanceStages,
    PerformanceSummary,
    TaskBuilderPerformance,
)
from .reporting import (
    build_performance_summary,
    build_taskbuilder_performance,
)
from .plan_reuse import (
    PlanReuseError,
    VerifiedPlanSource,
    load_verified_plan_source,
)
from .derived_cache import (
    CacheLookup,
    DerivedCache,
    MeshKeyResult,
    geometry_cache_key,
    mesh_cache_key,
)
from .repair_reuse import (
    RepairReuseDecision,
    RepairReusePreparation,
    classify_repair_rerun,
    prepare_repair_reuse,
)

__all__ = [
    "ModelPerformance",
    "PathKind",
    "PerformanceReuse",
    "PerformanceStages",
    "PerformanceSummary",
    "TaskBuilderPerformance",
    "build_performance_summary",
    "build_taskbuilder_performance",
    "PlanReuseError",
    "VerifiedPlanSource",
    "load_verified_plan_source",
    "CacheLookup",
    "DerivedCache",
    "MeshKeyResult",
    "geometry_cache_key",
    "mesh_cache_key",
    "RepairReuseDecision",
    "RepairReusePreparation",
    "classify_repair_rerun",
    "prepare_repair_reuse",
]
