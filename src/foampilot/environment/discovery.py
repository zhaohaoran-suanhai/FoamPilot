"""Discover installed OpenFOAM commands without reading tutorials."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Iterable

from foampilot.runtime.config import (
    isolated_source_environment,
    probe_openfoam_root,
)
from foampilot.runtime.models import RuntimeConfig

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
    with tempfile.TemporaryDirectory(prefix="foampilot-source-home-") as temporary:
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
            env=isolated_source_environment(Path(temporary)),
        )


def _command_directories(
    config: RuntimeConfig,
    environment: dict[str, str],
) -> list[Path]:
    root = config.openfoam_root.resolve()
    trusted = tuple(path.resolve() for path in config.trusted_readonly_roots)
    directories: list[Path] = []
    for value in environment.get("PATH", "").split(os.pathsep):
        if not value:
            continue
        directory = Path(value).resolve()
        if directory.is_relative_to(root) or any(
            directory.is_relative_to(item) for item in trusted
        ):
            if directory not in directories:
                directories.append(directory)
    return directories


def _discover_commands(
    config: RuntimeConfig,
    environment: dict[str, str],
) -> list[CommandFact]:
    commands: dict[str, Path] = {}
    approved_roots = (
        config.openfoam_root.resolve(),
        *(path.resolve() for path in config.trusted_readonly_roots),
    )
    for directory in _command_directories(config, environment):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if (
                path.name not in commands
                and not path.is_dir()
                and os.access(path, os.X_OK)
            ):
                resolved = path.resolve()
                if any(
                    resolved.is_relative_to(root)
                    for root in approved_roots
                ):
                    commands[path.name] = resolved
    return [
        CommandFact(name=name, path=path)
        for name, path in sorted(commands.items())
    ]


def _which(
    name: str,
    environment: dict[str, str],
) -> Path | None:
    value = shutil.which(name, path=environment.get("PATH"))
    return Path(value).resolve() if value else None


def _approved_mpi_launcher(
    config: RuntimeConfig,
    environment: dict[str, str],
) -> Path | None:
    launcher = _which("mpirun", environment) or _which("mpiexec", environment)
    if launcher is None:
        return None
    approved_roots = (
        Path("/usr").resolve(),
        config.openfoam_root.resolve(),
        *(path.resolve() for path in config.trusted_readonly_roots),
    )
    if any(launcher.is_relative_to(root) for root in approved_roots):
        return launcher
    return None


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
    executable: Path,
) -> str:
    try:
        result = _run_sourced(config, [str(executable.resolve()), "-help"])
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
                    _help_excerpt(config, command.path)
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

    probe = probe_openfoam_root(config.openfoam_root)
    environment = probe.environment
    workspace = Path(workspace_root).resolve()
    gmsh = _which("gmsh", environment)
    if gmsh != Path("/usr/bin/gmsh"):
        gmsh = None
    snapshot = EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version=environment["WM_PROJECT_VERSION"],
        openfoam_root=probe.root,
        tutorial_root=probe.tutorial_root,
        workspace_root=workspace,
        workspace_writable=_workspace_is_writable(workspace),
        commands=_discover_commands(config, environment),
        mpi_launcher=_approved_mpi_launcher(config, environment),
        gmsh=gmsh,
        max_mpi_ranks=config.max_mpi_ranks,
    )
    return enrich_command_help(
        config,
        snapshot,
        shortlisted=shortlisted,
    )
