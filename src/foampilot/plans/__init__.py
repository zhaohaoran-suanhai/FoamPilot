"""System-compiled plan contracts and deterministic validation.

The historical v3 reader intentionally lives in ``foampilot.plans.legacy``
and is not re-exported into the canonical authoring surface.
"""

from .models import (
    CommandStage,
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    PlanIssue,
)
from .normalizer import (
    CommandStageNormalizationRecord,
    NormalizationRecord,
    NormalizationResult,
    normalize_execution_plan,
)
from .input_normalizer import normalize_execution_plan_input
from .validation import validate_execution_plan

__all__ = [
    "CommandStage",
    "CommandStageNormalizationRecord",
    "ExecutionPlan",
    "GeneratedFile",
    "NativeCommand",
    "NormalizationRecord",
    "NormalizationResult",
    "PlanIssue",
    "normalize_execution_plan",
    "normalize_execution_plan_input",
    "validate_execution_plan",
]
