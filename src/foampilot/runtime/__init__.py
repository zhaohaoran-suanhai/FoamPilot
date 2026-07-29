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
    RuntimeCheck,
    RuntimeConfig,
)
from .plan_runner import PlanRunner
from .preflight import run_preflight

__all__ = [
    "ContinuitySample",
    "EquationResidual",
    "OpenFOAMLogSummary",
    "PlanRunResult",
    "PlanRunner",
    "PlanStepResult",
    "RuntimeCheck",
    "RuntimeConfig",
    "parse_openfoam_log",
    "run_preflight",
]
