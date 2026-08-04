"""Read-only discovery for the supported local Foundation v10 runtime."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .models import RuntimeCheck, RuntimeConfig
from .sandbox import probe_bubblewrap


def _path_check(
    name: str,
    path: Path,
    *,
    executable: bool = False,
    blocking: bool = True,
) -> RuntimeCheck:
    exists = path.is_file() if executable else path.exists()
    ok = exists and (not executable or os.access(path, os.X_OK))
    expectation = "executable file" if executable else "existing path"
    return RuntimeCheck(
        name=name,
        ok=ok,
        detail=f"{path} ({expectation})",
        blocking=blocking,
    )


def _solver_check(config: RuntimeConfig, solver: str) -> RuntimeCheck:
    bashrc = config.openfoam_root / "etc" / "bashrc"
    if not bashrc.is_file():
        return RuntimeCheck(
            name=f"solver:{solver}",
            ok=False,
            detail=f"OpenFOAM bashrc is missing: {bashrc}",
        )
    command = f"source {shlex.quote(str(bashrc))}; command -v {shlex.quote(solver)}"
    result = subprocess.run(
        ["bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    resolved = result.stdout.strip()
    return RuntimeCheck(
        name=f"solver:{solver}",
        ok=result.returncode == 0 and bool(resolved),
        detail=resolved or result.stderr.strip() or f"{solver} was not found",
    )


def _bubblewrap_launch_check(config: RuntimeConfig) -> RuntimeCheck:
    ok, detail = probe_bubblewrap(config.bubblewrap)
    return RuntimeCheck(
        name="bubblewrap_launch",
        ok=ok,
        detail=detail,
        blocking=config.execution_backend == "bubblewrap",
    )


def _execution_backend_check(config: RuntimeConfig) -> RuntimeCheck:
    bubblewrap_ok, bubblewrap_detail = probe_bubblewrap(
        config.bubblewrap
    )
    if config.execution_backend == "bubblewrap":
        return RuntimeCheck(
            name="execution_backend",
            ok=bubblewrap_ok,
            detail=(
                "bubblewrap selected"
                if bubblewrap_ok
                else f"bubblewrap unavailable: {bubblewrap_detail}"
            ),
        )
    if config.execution_backend == "host":
        return RuntimeCheck(
            name="execution_backend",
            ok=True,
            detail="audited typed host execution selected explicitly",
        )
    return RuntimeCheck(
        name="execution_backend",
        ok=True,
        detail=(
            "bubblewrap selected by auto policy"
            if bubblewrap_ok
            else (
                "audited typed host execution selected by auto policy; "
                f"bubblewrap unavailable: {bubblewrap_detail}"
            )
        ),
    )


def preflight_passed(checks: list[RuntimeCheck]) -> bool:
    """Return whether all blocking runtime checks passed."""

    return all(check.ok or not check.blocking for check in checks)


def run_preflight(config: RuntimeConfig) -> list[RuntimeCheck]:
    """Return deterministic readiness checks without changing the environment."""

    checks = [
        _path_check("python_executable", config.python_executable, executable=True),
        _path_check("openfoam_root", config.openfoam_root),
        _path_check("openfoam_bashrc", config.openfoam_root / "etc" / "bashrc"),
        _path_check("tutorial_root", config.tutorial_root),
        _path_check(
            "bubblewrap",
            config.bubblewrap,
            executable=True,
            blocking=config.execution_backend == "bubblewrap",
        ),
        _bubblewrap_launch_check(config),
        _execution_backend_check(config),
        _solver_check(config, "icoFoam"),
    ]
    return checks
