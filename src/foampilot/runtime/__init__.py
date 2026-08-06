"""Local Foundation v10 preflight, typed Runner, and log parsing."""

from .logs import (
    ContinuitySample,
    EquationResidual,
    OpenFOAMLogSummary,
    parse_openfoam_log,
)
from .models import (
    PlanRunResult,
    PlanStepResult,
    ReusedStepResult,
    RuntimeCheck,
    RuntimeConfig,
)
from .plan_runner import PlanRunner
from .preflight import preflight_passed, run_preflight

__all__ = [
    "ContinuitySample",
    "EquationResidual",
    "OpenFOAMLogSummary",
    "PlanRunResult",
    "PlanRunner",
    "PlanStepResult",
    "ReusedStepResult",
    "RuntimeCheck",
    "RuntimeConfig",
    "parse_openfoam_log",
    "preflight_passed",
    "run_preflight",
]
