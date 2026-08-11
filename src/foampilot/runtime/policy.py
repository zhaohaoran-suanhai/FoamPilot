"""Pure execution-backend policy decisions."""

from __future__ import annotations

from .models import (
    ExecutionPolicyDecision,
    ExecutionRiskReport,
    RuntimeConfig,
    SandboxProbe,
)


_HOST_WARNING = (
    "当前 attempt 在宿主机以当前用户权限执行；typed argv 不等价于文件系统和网络隔离。"
)
_MECHANISM_FAILURES = {"BWRAP_UNAVAILABLE", "NAMESPACE_UNAVAILABLE"}


def _decision(
    config: RuntimeConfig,
    *,
    backend: str | None,
    allowed: bool,
    code: str,
    fallback_reason: str | None = None,
) -> ExecutionPolicyDecision:
    return ExecutionPolicyDecision(
        requested_isolation=config.isolation,
        actual_backend=backend,
        allowed=allowed,
        code=code,
        dynamic_code_host_opt_in=config.allow_dynamic_code_on_host,
        fallback_reason=fallback_reason,
        unisolated_warning=_HOST_WARNING if backend == "host" else None,
    )

def decide_execution_policy(
    config: RuntimeConfig,
    risk: ExecutionRiskReport,
    probe: SandboxProbe,
) -> ExecutionPolicyDecision:
    """Return one deterministic decision without performing I/O."""

    if config.isolation == "trusted_host":
        if risk.risk_level in {"high", "unknown"}:
            if not config.allow_dynamic_code_on_host:
                return _decision(
                    config,
                    backend=None,
                    allowed=False,
                    code="HOST_DYNAMIC_CODE_BLOCKED",
                )
            return _decision(
                config,
                backend="host",
                allowed=True,
                code="TRUSTED_HOST_DYNAMIC_CODE_OPT_IN",
            )
        return _decision(
            config,
            backend="host",
            allowed=True,
            code="TRUSTED_HOST_SELECTED",
        )

    sandbox_available = probe.status == "passed" and probe.ok is True
    if sandbox_available:
        return _decision(
            config,
            backend="bubblewrap",
            allowed=True,
            code="SANDBOX_SELECTED",
        )

    failure_code = probe.failure_code or "SANDBOX_SETUP_FAILED"
    if failure_code not in _MECHANISM_FAILURES:
        return _decision(
            config,
            backend=None,
            allowed=False,
            code=failure_code,
            fallback_reason=probe.detail,
        )
    if config.isolation == "sandbox_required":
        return _decision(
            config,
            backend=None,
            allowed=False,
            code="SANDBOX_REQUIRED_UNAVAILABLE",
            fallback_reason=probe.detail,
        )
    if risk.risk_level != "low":
        return _decision(
            config,
            backend=None,
            allowed=False,
            code="HOST_DYNAMIC_CODE_BLOCKED",
            fallback_reason=probe.detail,
        )
    return _decision(
        config,
        backend="host",
        allowed=True,
        code="HOST_FALLBACK_SELECTED",
        fallback_reason=probe.detail,
    )
