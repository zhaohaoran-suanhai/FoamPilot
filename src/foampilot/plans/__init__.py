"""Agent-owned plan contracts and deterministic validation."""

from .models import (
    CommandStage,
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    PlanIssue,
)
from .normalizer import (
    NormalizationRecord,
    NormalizationResult,
    normalize_execution_plan,
)
from .input_normalizer import normalize_execution_plan_input
from .validation import validate_execution_plan

__all__ = [
    "CommandStage",
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
