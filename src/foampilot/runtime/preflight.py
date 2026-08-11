"""Structured readiness report for the effective runtime configuration."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field

from foampilot.environment import EnvironmentSnapshot, discover_environment

from .models import (
    RuntimeCheck,
    RuntimeConfig,
    RuntimeConfigError,
    SandboxProbe,
    StrictModel,
)
from .protection import runtime_protected_paths
from .sandbox import not_requested_probe, probe_sandbox


class RuntimePreflightReport(StrictModel):
    schema_version: Literal[1] = 1
    ok: bool
    python_executable: Path
    checks: tuple[RuntimeCheck, ...] = Field(default_factory=tuple)
    environment: EnvironmentSnapshot | None
    sandbox_probe: SandboxProbe
    failure_code: str | None = None
    failure_message: str | None = None
    failure_recovery: str | None = None


def _environment_not_probed(detail: str) -> SandboxProbe:
    return SandboxProbe(
        status="not_requested",
        ok=None,
        return_code=None,
        detail=detail,
    )


def run_preflight(
    config: RuntimeConfig,
    *,
    workspace_root: str | Path,
    sandbox_executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RuntimePreflightReport:
    """Discover the environment and run the production-equivalent sandbox probe."""

    checks: list[RuntimeCheck] = [
        RuntimeCheck(
            name="python_interpreter",
            ok=True,
            detail=str(Path(sys.executable).resolve()),
            blocking=False,
        )
    ]
    try:
        environment = discover_environment(config, workspace_root)
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, RuntimeConfigError):
            failure_code = error.code
            failure_message = error.message
            failure_recovery = error.recovery
        else:
            failure_code = "OPENFOAM_DISCOVERY_FAILED"
            failure_message = "Foundation OpenFOAM v10 环境发现失败。"
            failure_recovery = "检查 OpenFOAM root 与工作目录后重试。"
        checks.append(
            RuntimeCheck(
                name="foundation_v10_environment",
                ok=False,
                detail=str(error),
            )
        )
        return RuntimePreflightReport(
            ok=False,
            python_executable=Path(sys.executable).resolve(),
            checks=tuple(checks),
            environment=None,
            sandbox_probe=_environment_not_probed(
                "environment discovery failed before sandbox probe"
            ),
            failure_code=failure_code,
            failure_message=failure_message,
            failure_recovery=failure_recovery,
        )

    checks.extend(
        (
            RuntimeCheck(
                name="foundation_v10_environment",
                ok=True,
                detail=f"Foundation OpenFOAM {environment.version}",
            ),
            RuntimeCheck(
                name="workspace_writable",
                ok=environment.workspace_writable,
                detail=str(environment.workspace_root),
            ),
        )
    )
    if config.isolation == "trusted_host":
        probe = not_requested_probe()
    else:
        with tempfile.TemporaryDirectory(prefix="foampilot-preflight-") as temporary:
            case = Path(temporary) / "case"
            case.mkdir()
            probe = probe_sandbox(
                config=config,
                environment=environment,
                case_dir=case,
                protected_paths=runtime_protected_paths((), environment),
                memory_mib=256,
                cpu_seconds=5,
                executor=sandbox_executor,
            )
    probe_blocking = config.isolation == "sandbox_required"
    checks.append(
        RuntimeCheck(
            name="sandbox_full_launch",
            ok=probe.ok is True or config.isolation == "trusted_host",
            detail=probe.detail,
            blocking=probe_blocking,
        )
    )
    ok = all(check.ok or not check.blocking for check in checks)
    failure_code: str | None = None
    failure_message: str | None = None
    failure_recovery: str | None = None
    if not environment.workspace_writable:
        failure_code = "WORKSPACE_NOT_WRITABLE"
        failure_message = "FoamPilot 工作目录不可写。"
        failure_recovery = "选择当前用户可创建目录和文件的 workspace 后重试。"
    elif probe_blocking and probe.ok is not True:
        failure_code = probe.failure_code or "SANDBOX_SETUP_FAILED"
        if failure_code in {"BWRAP_UNAVAILABLE", "NAMESPACE_UNAVAILABLE"}:
            failure_code = "SANDBOX_REQUIRED_UNAVAILABLE"
        failure_message = "sandbox_required 所需的完整 bubblewrap 沙箱不可用。"
        failure_recovery = "安装或修复 bubblewrap 与 namespace 权限后重试。"
    return RuntimePreflightReport(
        ok=ok,
        python_executable=Path(sys.executable).resolve(),
        checks=tuple(checks),
        environment=environment,
        sandbox_probe=probe,
        failure_code=failure_code,
        failure_message=failure_message,
        failure_recovery=failure_recovery,
    )


def preflight_passed(
    report: RuntimePreflightReport | list[RuntimeCheck],
) -> bool:
    """Return readiness for the structured report or legacy check collection."""

    if isinstance(report, RuntimePreflightReport):
        return report.ok
    return all(check.ok or not check.blocking for check in report)
