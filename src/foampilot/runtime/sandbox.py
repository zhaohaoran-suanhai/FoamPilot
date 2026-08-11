"""Shared construction and probing of the networkless execution sandbox."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path

from foampilot.environment.models import EnvironmentSnapshot

from .models import (
    RuntimeConfig,
    SandboxLaunch,
    SandboxMount,
    SandboxProbe,
)


Executor = Callable[..., subprocess.CompletedProcess[str]]
_NAMESPACE_FLAGS = (
    "--die-with-parent",
    "--new-session",
    "--unshare-net",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--clearenv",
)
_SOURCE_AND_EXEC = (
    'source "$1" >/dev/null 2>&1; shift; cd /case; exec "$@"'
)


class SandboxBuildError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _intersects(first: Path, second: Path) -> bool:
    return _within(first, second) or _within(second, first)


def _append_mount(
    argv: list[str],
    mounts: list[SandboxMount],
    mount: SandboxMount,
) -> None:
    mounts.append(mount)
    target = str(mount.target)
    if mount.kind == "ro_bind":
        assert mount.source is not None
        argv.extend(("--ro-bind", str(mount.source), target))
    elif mount.kind == "bind":
        assert mount.source is not None
        argv.extend(("--bind", str(mount.source), target))
    elif mount.kind == "tmpfs":
        argv.extend(("--tmpfs", target))
    elif mount.kind == "dir":
        argv.extend(("--dir", target))
    elif mount.kind == "symlink":
        assert mount.source is not None
        argv.extend(("--symlink", str(mount.source), target))
    elif mount.kind == "proc":
        argv.extend(("--proc", target))
    elif mount.kind == "dev":
        argv.extend(("--dev", target))


def _system_mounts(argv: list[str], mounts: list[SandboxMount]) -> None:
    for directory in (Path("/usr"), Path("/etc")):
        if directory.is_dir():
            _append_mount(
                argv,
                mounts,
                SandboxMount(kind="ro_bind", source=directory, target=directory),
            )
    for location in (Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64")):
        if location.is_symlink():
            _append_mount(
                argv,
                mounts,
                SandboxMount(
                    kind="symlink",
                    source=os.readlink(location),
                    target=location,
                ),
            )
        elif location.exists():
            _append_mount(
                argv,
                mounts,
                SandboxMount(kind="ro_bind", source=location, target=location),
            )
    _append_mount(argv, mounts, SandboxMount(kind="proc", target="/proc"))
    _append_mount(argv, mounts, SandboxMount(kind="dev", target="/dev"))
    _append_mount(argv, mounts, SandboxMount(kind="tmpfs", target="/tmp"))


def _ensure_parents(
    path: Path,
    argv: list[str],
    mounts: list[SandboxMount],
    created: set[Path],
) -> None:
    system_roots = (Path("/usr"), Path("/etc"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"))
    for parent in reversed(path.parents):
        if parent == Path("/"):
            continue
        if any(parent == root or _within(parent, root) for root in system_roots):
            continue
        if parent in created:
            continue
        _append_mount(argv, mounts, SandboxMount(kind="dir", target=parent))
        created.add(parent)


def build_sandbox_argv(
    *,
    config: RuntimeConfig,
    environment: EnvironmentSnapshot,
    case_dir: Path,
    protected_paths: Sequence[Path],
    memory_mib: int,
    cpu_seconds: int,
    typed_argv: Sequence[str],
) -> SandboxLaunch:
    """Build the complete argv used by both preflight and PlanRunner."""

    if config.bubblewrap is None:
        raise SandboxBuildError("BWRAP_UNAVAILABLE", "bubblewrap is not configured")
    bubblewrap = config.bubblewrap.resolve()
    if not bubblewrap.is_file() or not os.access(bubblewrap, os.X_OK):
        raise SandboxBuildError("BWRAP_UNAVAILABLE", "bubblewrap is unavailable")
    openfoam_root = config.openfoam_root.resolve()
    case = Path(case_dir).resolve()
    if not openfoam_root.is_dir():
        raise SandboxBuildError("SANDBOX_SETUP_FAILED", "OpenFOAM root is missing")
    if not case.is_dir():
        raise SandboxBuildError("SANDBOX_SETUP_FAILED", "case directory is missing")
    if memory_mib < 1 or cpu_seconds < 1 or not typed_argv:
        raise SandboxBuildError("SANDBOX_SETUP_FAILED", "invalid sandbox limits or argv")

    trusted = tuple(path.resolve() for path in config.trusted_readonly_roots)
    for root in trusted:
        if not root.exists():
            raise SandboxBuildError(
                "TRUSTED_RUNTIME_ROOT_INVALID",
                "trusted readonly root does not exist",
            )
    protected_values = [Path(path).resolve() for path in protected_paths]
    if environment.tutorial_root is not None:
        protected_values.append(environment.tutorial_root.resolve())
    protected = tuple(dict.fromkeys(protected_values))
    for trusted_root in trusted:
        if any(_intersects(trusted_root, item) for item in protected):
            raise SandboxBuildError(
                "TRUSTED_RUNTIME_ROOT_INVALID",
                "trusted readonly root intersects a protected path",
            )
    for item in protected:
        if item.exists() and not item.is_dir():
            raise SandboxBuildError(
                "SANDBOX_SETUP_FAILED",
                "protected regular files cannot be reliably hidden",
            )

    argv = [str(bubblewrap), *_NAMESPACE_FLAGS]
    mounts: list[SandboxMount] = []
    _system_mounts(argv, mounts)
    created: set[Path] = set()
    for root in (openfoam_root, *trusted):
        _ensure_parents(root, argv, mounts, created)
        _append_mount(
            argv,
            mounts,
            SandboxMount(kind="ro_bind", source=root, target=root),
        )
    _ensure_parents(Path("/home/agent"), argv, mounts, created)
    if Path("/home/agent") not in created:
        _append_mount(
            argv,
            mounts,
            SandboxMount(kind="dir", target="/home/agent"),
        )
        created.add(Path("/home/agent"))

    mounted_roots = (
        Path("/usr"),
        Path("/etc"),
        openfoam_root,
        *trusted,
    )
    hidden: list[Path] = []
    for item in protected:
        if not item.is_dir():
            continue
        if any(_within(item, root) for root in mounted_roots):
            _append_mount(
                argv,
                mounts,
                SandboxMount(kind="tmpfs", target=item),
            )
            hidden.append(item)

    _append_mount(
        argv,
        mounts,
        SandboxMount(kind="bind", source=case, target="/case"),
    )
    address_space = memory_mib * 1024 * 1024
    argv.extend(
        (
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
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "LC_ALL",
            "C.UTF-8",
            "/usr/bin/prlimit",
            f"--cpu={cpu_seconds}",
            f"--as={address_space}",
            "--",
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            _SOURCE_AND_EXEC,
            "foampilot",
            str(openfoam_root / "etc/bashrc"),
            *tuple(typed_argv),
        )
    )
    return SandboxLaunch(
        argv=tuple(argv),
        mounts=tuple(mounts),
        hidden_paths=tuple(hidden),
    )


def _safe_builder_sha256(launch: SandboxLaunch) -> str:
    structure = {
        "namespace_flags": _NAMESPACE_FLAGS,
        "mounts": [
            {
                "kind": mount.kind,
                "target_class": (
                    str(mount.target)
                    if str(mount.target)
                    in {
                        "/usr",
                        "/etc",
                        "/bin",
                        "/sbin",
                        "/lib",
                        "/lib64",
                        "/proc",
                        "/dev",
                        "/tmp",
                        "/case",
                        "/home/agent",
                    }
                    else "<dynamic>"
                ),
            }
            for mount in launch.mounts
        ],
        "resource_limits": True,
        "fixed_source_template": True,
    }
    encoded = json.dumps(structure, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def probe_sandbox(
    *,
    config: RuntimeConfig,
    environment: EnvironmentSnapshot,
    case_dir: Path,
    protected_paths: Sequence[Path],
    memory_mib: int,
    cpu_seconds: int,
    executor: Executor = subprocess.run,
) -> SandboxProbe:
    """Execute a no-op through the complete production sandbox builder."""

    try:
        launch = build_sandbox_argv(
            config=config,
            environment=environment,
            case_dir=case_dir,
            protected_paths=protected_paths,
            memory_mib=memory_mib,
            cpu_seconds=cpu_seconds,
            typed_argv=("/usr/bin/true",),
        )
    except SandboxBuildError as error:
        return SandboxProbe(
            status="failed",
            ok=False,
            failure_code=error.code,
            return_code=None,
            detail=error.detail,
        )
    try:
        result = executor(
            list(launch.argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(cpu_seconds + 5, 10),
        )
    except (OSError, subprocess.TimeoutExpired):
        return SandboxProbe(
            status="failed",
            ok=False,
            builder_sha256=_safe_builder_sha256(launch),
            namespace_flags=_NAMESPACE_FLAGS,
            mount_count=len(launch.mounts),
            protected_path_count=len(launch.hidden_paths),
            failure_code="BWRAP_UNAVAILABLE",
            return_code=None,
            detail="bubblewrap process could not be launched",
        )
    if result.returncode == 0:
        return SandboxProbe(
            status="passed",
            ok=True,
            builder_sha256=_safe_builder_sha256(launch),
            namespace_flags=_NAMESPACE_FLAGS,
            mount_count=len(launch.mounts),
            protected_path_count=len(launch.hidden_paths),
            return_code=0,
            detail="complete networkless sandbox launch succeeded",
        )
    combined = f"{result.stderr}\n{result.stdout}".casefold()
    failure_code = (
        "NAMESPACE_UNAVAILABLE"
        if any(
            token in combined
            for token in ("operation not permitted", "netlink_route", "unshare")
        )
        else "SANDBOX_SETUP_FAILED"
    )
    return SandboxProbe(
        status="failed",
        ok=False,
        builder_sha256=_safe_builder_sha256(launch),
        namespace_flags=_NAMESPACE_FLAGS,
        mount_count=len(launch.mounts),
        protected_path_count=len(launch.hidden_paths),
        failure_code=failure_code,
        return_code=result.returncode,
        detail=f"complete sandbox launch failed with return code {result.returncode}",
    )


def not_requested_probe() -> SandboxProbe:
    return SandboxProbe(
        status="not_requested",
        ok=None,
        return_code=None,
        detail="sandbox probe not requested by trusted_host policy",
    )
