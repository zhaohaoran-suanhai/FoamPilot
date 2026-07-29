"""Read-only discovery for the supported local Foundation v10 runtime."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .models import RuntimeCheck, RuntimeConfig


def _path_check(name: str, path: Path, *, executable: bool = False) -> RuntimeCheck:
    exists = path.is_file() if executable else path.exists()
    ok = exists and (not executable or os.access(path, os.X_OK))
    expectation = "executable file" if executable else "existing path"
    return RuntimeCheck(
        name=name,
        ok=ok,
        detail=f"{path} ({expectation})",
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
    if not config.bubblewrap.is_file():
        return RuntimeCheck(
            name="bubblewrap_launch",
            ok=False,
            detail=f"bubblewrap executable is missing: {config.bubblewrap}",
        )
    try:
        result = subprocess.run(
            [
                str(config.bubblewrap),
                "--unshare-net",
                "--ro-bind",
                "/usr",
                "/usr",
                "--symlink",
                "usr/bin",
                "/bin",
                "--symlink",
                "usr/lib",
                "/lib",
                "--symlink",
                "usr/lib64",
                "/lib64",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "/usr/bin/true",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return RuntimeCheck(
            name="bubblewrap_launch",
            ok=False,
            detail=f"bubblewrap launch failed: {error}",
        )
    detail = (
        "networkless bubblewrap namespace launch succeeded"
        if result.returncode == 0
        else result.stderr.strip() or f"bubblewrap returned {result.returncode}"
    )
    return RuntimeCheck(
        name="bubblewrap_launch",
        ok=result.returncode == 0,
        detail=detail,
    )


def run_preflight(config: RuntimeConfig) -> list[RuntimeCheck]:
    """Return deterministic readiness checks without changing the environment."""

    checks = [
        _path_check("python_executable", config.python_executable, executable=True),
        _path_check("openfoam_root", config.openfoam_root),
        _path_check("openfoam_bashrc", config.openfoam_root / "etc" / "bashrc"),
        _path_check("tutorial_root", config.tutorial_root),
        _path_check("bubblewrap", config.bubblewrap, executable=True),
        _bubblewrap_launch_check(config),
        _solver_check(config, "icoFoam"),
    ]
    return checks
