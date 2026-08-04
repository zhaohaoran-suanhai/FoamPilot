"""Shared construction of the networkless OpenFOAM sandbox."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import subprocess


@lru_cache(maxsize=8)
def probe_bubblewrap(bubblewrap: Path) -> tuple[bool, str]:
    """Probe one minimal networkless namespace once per process."""

    if not bubblewrap.is_file():
        return False, f"bubblewrap executable is missing: {bubblewrap}"
    try:
        result = subprocess.run(
            [
                str(bubblewrap),
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
        return False, f"bubblewrap launch failed: {error}"
    if result.returncode == 0:
        return True, "networkless bubblewrap namespace launch succeeded"
    return (
        False,
        result.stderr.strip()
        or result.stdout.strip()
        or f"bubblewrap returned {result.returncode}",
    )


def build_sandbox_prefix(
    *,
    bubblewrap: Path,
    openfoam_root: Path,
    case_dir: Path,
    memory_mib: int,
    cpu_seconds: int,
) -> list[str]:
    """Build the Bubblewrap and resource-limit prefix for typed commands."""

    project = str(openfoam_root.resolve())
    tutorials = str((openfoam_root / "tutorials").resolve())
    address_space = memory_mib * 1024 * 1024
    return [
        str(bubblewrap.resolve()),
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--clearenv",
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
        "--ro-bind",
        "/etc",
        "/etc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home",
        "--dir",
        "/home/agent",
        "--dir",
        "/home/edwin",
        "--dir",
        "/home/edwin/workplace",
        "--ro-bind",
        project,
        project,
        "--tmpfs",
        tutorials,
        "--bind",
        str(case_dir.resolve()),
        "/case",
        "--chdir",
        "/case",
        "--setenv",
        "HOME",
        "/home/agent",
        "--setenv",
        "USER",
        "agent",
        "--setenv",
        "LOGNAME",
        "agent",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "/usr/bin/prlimit",
        f"--cpu={cpu_seconds}",
        f"--as={address_space}",
        "--",
    ]
