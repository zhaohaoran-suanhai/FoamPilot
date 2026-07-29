"""Discover installed OpenFOAM commands without reading tutorials."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Iterable

from foampilot.runtime import RuntimeConfig

from .models import CommandFact, EnvironmentSnapshot


_SOURCE_AND_EXEC = 'source "$1" >/dev/null 2>&1; shift; exec "$@"'
_HELP_LIMIT = 4096


def _run_sourced(
    config: RuntimeConfig,
    argv: list[str],
    *,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    bashrc = config.openfoam_root / "etc/bashrc"
    return subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            _SOURCE_AND_EXEC,
            "foampilot",
            str(bashrc),
            *argv,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sourced_environment(config: RuntimeConfig) -> dict[str, str]:
    result = _run_sourced(config, ["/usr/bin/env", "-0"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"could not source OpenFOAM environment: {detail}")
    values: dict[str, str] = {}
    for entry in result.stdout.split("\0"):
        if not entry or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        values[name] = value
    return values


def _command_directories(
    config: RuntimeConfig,
    environment: dict[str, str],
) -> list[Path]:
    root = config.openfoam_root.resolve()
    declared = {
        Path(value).resolve()
        for name in (
            "FOAM_APPBIN",
            "FOAM_SITE_APPBIN",
            "FOAM_USER_APPBIN",
        )
        if (value := environment.get(name))
    }
    directories: list[Path] = []
    for value in environment.get("PATH", "").split(os.pathsep):
        if not value:
            continue
        directory = Path(value).resolve()
        if directory in declared or directory.is_relative_to(root):
            if directory not in directories:
                directories.append(directory)
    return directories


def _discover_commands(
    config: RuntimeConfig,
    environment: dict[str, str],
) -> list[CommandFact]:
    commands: dict[str, Path] = {}
    for directory in _command_directories(config, environment):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if (
                path.name not in commands
                and not path.is_dir()
                and os.access(path, os.X_OK)
            ):
                commands[path.name] = path.resolve()
    return [
        CommandFact(name=name, path=path)
        for name, path in sorted(commands.items())
    ]


def _version(
    config: RuntimeConfig,
    environment: dict[str, str],
) -> str:
    result = _run_sourced(config, ["foamVersion"])
    text = (result.stdout or result.stderr).strip()
    match = re.search(r"(?:OpenFOAM[- ]?)?([0-9]+)", text)
    if result.returncode == 0 and match:
        return match.group(1)
    fallback = environment.get("WM_PROJECT_VERSION", "").strip()
    if fallback:
        return fallback
    raise RuntimeError(f"could not determine OpenFOAM version: {text}")


def _which(
    name: str,
    environment: dict[str, str],
) -> Path | None:
    value = shutil.which(name, path=environment.get("PATH"))
    return Path(value).resolve() if value else None


def _workspace_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=path,
            prefix=".foampilot-write-probe.",
            delete=False,
        ) as handle:
            probe = Path(handle.name)
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink()
    except OSError:
        return False
    return True


def _help_excerpt(
    config: RuntimeConfig,
    name: str,
) -> str:
    try:
        result = _run_sourced(config, [name, "-help"])
    except subprocess.TimeoutExpired:
        return "help command timed out"
    combined = "\n".join(
        value.strip()
        for value in (result.stdout, result.stderr)
        if value.strip()
    )
    return combined[:_HELP_LIMIT].strip()


def enrich_command_help(
    config: RuntimeConfig,
    snapshot: EnvironmentSnapshot,
    shortlisted: Iterable[str],
) -> EnvironmentSnapshot:
    selected = set(shortlisted) & snapshot.executable_names
    commands = [
        command.model_copy(
            update={
                "help_excerpt": (
                    _help_excerpt(config, command.name)
                    if command.name in selected
                    else command.help_excerpt
                )
            }
        )
        for command in snapshot.commands
    ]
    return snapshot.model_copy(update={"commands": commands})


def discover_environment(
    config: RuntimeConfig,
    workspace_root: str | Path,
    shortlisted: Iterable[str] = (),
) -> EnvironmentSnapshot:
    """Return facts from a sourced Foundation OpenFOAM environment."""

    environment = _sourced_environment(config)
    workspace = Path(workspace_root).resolve()
    snapshot = EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version=_version(config, environment),
        openfoam_root=config.openfoam_root.resolve(),
        tutorial_root=config.tutorial_root.resolve(),
        workspace_root=workspace,
        workspace_writable=_workspace_is_writable(workspace),
        commands=_discover_commands(config, environment),
        mpi_launcher=(
            _which("mpirun", environment)
            or _which("mpiexec", environment)
        ),
        gmsh=_which("gmsh", environment),
        max_mpi_ranks=config.max_mpi_ranks,
    )
    return enrich_command_help(
        config,
        snapshot,
        shortlisted=shortlisted,
    )
