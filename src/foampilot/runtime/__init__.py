"""Local Foundation v10 preflight, typed Runner, and log parsing."""

from .logs import (
    ContinuitySample,
    EquationResidual,
    OpenFOAMLogSummary,
    parse_openfoam_log,
)
from .config import probe_openfoam_root, resolve_runtime_config
from .models import (
    ExecutionPolicyDecision,
    ExecutionRiskReport,
    IsolationPolicy,
    PlanRunResult,
    PlanStepResult,
    ReusedStepResult,
    RuntimeCheck,
    RuntimeConfig,
    RuntimeConfigError,
    RuntimeConfigProvenance,
    RuntimeFieldSource,
    RuntimeOverrides,
    RuntimeResolution,
    SandboxProbe,
)
from .policy import decide_execution_policy
from .risk import scan_execution_risk
from .plan_runner import PlanRunner, RuntimeExecutionError

__all__ = [
    "ContinuitySample",
    "EquationResidual",
    "ExecutionPolicyDecision",
    "ExecutionRiskReport",
    "IsolationPolicy",
    "OpenFOAMLogSummary",
    "PlanRunResult",
    "PlanRunner",
    "PlanStepResult",
    "ReusedStepResult",
    "RuntimeCheck",
    "RuntimeConfig",
    "RuntimeConfigError",
    "RuntimeConfigProvenance",
    "RuntimeFieldSource",
    "RuntimeOverrides",
    "RuntimeResolution",
    "RuntimeExecutionError",
    "SandboxProbe",
    "decide_execution_policy",
    "parse_openfoam_log",
    "preflight_passed",
    "probe_openfoam_root",
    "resolve_runtime_config",
    "scan_execution_risk",
    "run_preflight",
]


def run_preflight(*args, **kwargs):
    from .preflight import run_preflight as implementation

    return implementation(*args, **kwargs)


def preflight_passed(*args, **kwargs):
    from .preflight import preflight_passed as implementation

    return implementation(*args, **kwargs)
