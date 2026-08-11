"""Portable, strict runtime configuration resolution."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from .models import (
    IsolationPolicy,
    RuntimeConfig,
    RuntimeConfigError,
    RuntimeConfigProvenance,
    RuntimeFieldSource,
    RuntimeOverrides,
    RuntimeResolution,
    StrictModel,
)


class OpenFOAMFileConfig(StrictModel):
    distribution: Literal["foundation"] | None = None
    version: Literal["10"] | None = None
    root: Path | None = None


class ExecutionFileConfig(StrictModel):
    isolation: IsolationPolicy | None = None
    bubblewrap: str | None = None
    max_mpi_ranks: int | None = Field(default=None, ge=1)
    allow_dynamic_code_on_host: bool | None = None
    trusted_readonly_roots: tuple[Path, ...] | None = None


class RuntimeFileConfig(StrictModel):
    schema_version: Literal[1]
    openfoam: OpenFOAMFileConfig = Field(default_factory=OpenFOAMFileConfig)
    execution: ExecutionFileConfig = Field(default_factory=ExecutionFileConfig)


def isolated_source_environment(home: Path) -> dict[str, str]:
    """Return the minimal environment used whenever etc/bashrc is sourced."""

    return {
        "HOME": str(home),
        "USER": "agent",
        "LOGNAME": "agent",
        "TMPDIR": "/tmp",
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "SHELL": "/bin/bash",
    }


class OpenFOAMRootProbe(StrictModel):
    root: Path
    environment: dict[str, str]
    foam_appbin: Path
    base_solver: Path
    tutorial_root: Path | None = None


_DEFAULT_CANDIDATES = (
    Path("/opt/OpenFOAM/OpenFOAM-10"),
    Path("/usr/lib/openfoam/openfoam10"),
    Path("/usr/lib/openfoam/openfoam-10"),
)
_ENVIRONMENT_FIELDS: dict[str, str] = {
    "FOAMPILOT_OPENFOAM_ROOT": "openfoam.root",
    "FOAMPILOT_EXECUTION_ISOLATION": "execution.isolation",
    "FOAMPILOT_BUBBLEWRAP": "execution.bubblewrap",
    "FOAMPILOT_MAX_MPI_RANKS": "execution.max_mpi_ranks",
    "FOAMPILOT_ALLOW_DYNAMIC_CODE_ON_HOST": (
        "execution.allow_dynamic_code_on_host"
    ),
}
_CLI_FIELDS: dict[str, str] = {
    "openfoam_root": "openfoam.root",
    "isolation": "execution.isolation",
    "bubblewrap": "execution.bubblewrap",
    "max_mpi_ranks": "execution.max_mpi_ranks",
    "allow_dynamic_code_on_host": "execution.allow_dynamic_code_on_host",
    "trusted_readonly_roots": "execution.trusted_readonly_roots",
}
_CLI_LOCATORS: dict[str, str] = {
    "openfoam_root": "--openfoam-root",
    "isolation": "--execution-isolation",
    "bubblewrap": "--bubblewrap",
    "max_mpi_ranks": "--max-mpi-ranks",
    "allow_dynamic_code_on_host": "--allow-dynamic-code-on-host",
    "trusted_readonly_roots": "--trusted-readonly-root",
}


def _runtime_error(
    code: str,
    message: str,
    recovery: str,
    detail: str | None = None,
) -> RuntimeConfigError:
    return RuntimeConfigError(code, message, recovery, detail)


def _load_toml(path: Path) -> RuntimeFileConfig:
    try:
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("target is not a regular file")
        payload = tomllib.loads(resolved.read_text(encoding="utf-8"))
        return RuntimeFileConfig.model_validate(payload)
    except (OSError, ValueError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise _runtime_error(
            "RUNTIME_CONFIG_INVALID",
            "Runtime TOML 无效。",
            "修正未知字段、类型或路径后重试。",
            type(error).__name__,
        ) from error


def _file_values(config: RuntimeFileConfig) -> dict[str, object]:
    values: dict[str, object] = {"schema_version": config.schema_version}
    if config.openfoam.distribution is not None:
        values["openfoam.distribution"] = config.openfoam.distribution
    if config.openfoam.version is not None:
        values["openfoam.version"] = config.openfoam.version
    if config.openfoam.root is not None:
        values["openfoam.root"] = config.openfoam.root
    if config.execution.isolation is not None:
        values["execution.isolation"] = config.execution.isolation
    if config.execution.bubblewrap is not None:
        values["execution.bubblewrap"] = config.execution.bubblewrap
    if config.execution.max_mpi_ranks is not None:
        values["execution.max_mpi_ranks"] = config.execution.max_mpi_ranks
    if config.execution.allow_dynamic_code_on_host is not None:
        values["execution.allow_dynamic_code_on_host"] = (
            config.execution.allow_dynamic_code_on_host
        )
    if config.execution.trusted_readonly_roots is not None:
        values["execution.trusted_readonly_roots"] = (
            config.execution.trusted_readonly_roots
        )
    return values


def _apply_values(
    values: dict[str, object],
    sources: dict[str, RuntimeFieldSource],
    updates: Mapping[str, object],
    *,
    source: str,
    locator: str,
) -> None:
    for field, value in updates.items():
        values[field] = value
        sources[field] = RuntimeFieldSource(source=source, locator=locator)


def _parse_environment_value(name: str, value: str) -> object:
    if value == "":
        raise ValueError("empty values are invalid")
    if name == "FOAMPILOT_MAX_MPI_RANKS":
        if not value.isdecimal() or int(value) < 1:
            raise ValueError("expected a positive integer")
        return int(value)
    if name == "FOAMPILOT_ALLOW_DYNAMIC_CODE_ON_HOST":
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("expected true or false")
    if name == "FOAMPILOT_OPENFOAM_ROOT":
        return Path(value)
    return value


def _environment_updates(environment: Mapping[str, str]) -> dict[str, object]:
    updates: dict[str, object] = {}
    for name, field in _ENVIRONMENT_FIELDS.items():
        if name not in environment:
            continue
        try:
            updates[field] = _parse_environment_value(name, environment[name])
        except ValueError as error:
            raise _runtime_error(
                "RUNTIME_CONFIG_INVALID",
                f"环境变量 {name} 无效。",
                "使用严格的路径、正整数或小写 true/false 后重试。",
                str(error),
            ) from error
    return updates


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sourced_environment(bashrc: Path) -> dict[str, str]:
    marker = b"\0__FOAMPILOT_ENV_START__\0"
    command = (
        'source "$1" >/dev/null 2>&1 || exit $?; '
        "printf '\\0__FOAMPILOT_ENV_START__\\0'; env -0"
    )
    with tempfile.TemporaryDirectory(prefix="foampilot-source-home-") as temporary:
        completed = subprocess.run(
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                command,
                "foampilot",
                str(bashrc),
            ],
            check=False,
            capture_output=True,
            timeout=15,
            env=isolated_source_environment(Path(temporary)),
        )
    if completed.returncode != 0 or marker not in completed.stdout:
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "无法加载 Foundation OpenFOAM 环境。",
            "检查 OpenFOAM root 下的 etc/bashrc 后重试。",
            f"bashrc source returned {completed.returncode}",
        )
    encoded = completed.stdout.split(marker, 1)[1]
    environment: dict[str, str] = {}
    for item in encoded.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        environment[key.decode(errors="replace")] = value.decode(errors="replace")
    return environment


def probe_openfoam_root(root: str | Path) -> OpenFOAMRootProbe:
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        raise _runtime_error(
            "RUNTIME_CONFIG_INVALID",
            "OpenFOAM root 必须是绝对路径。",
            "使用 Foundation v10 安装目录的绝对路径。",
        )
    resolved = candidate.resolve()
    bashrc = resolved / "etc/bashrc"
    if not bashrc.is_file():
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "OpenFOAM root 缺少 etc/bashrc。",
            "指定完整的 Foundation OpenFOAM v10 安装目录。",
        )
    environment = _sourced_environment(bashrc)
    if environment.get("WM_PROJECT") not in {"OpenFOAM", "openfoam"}:
        raise _runtime_error(
            "OPENFOAM_VERSION_MISMATCH",
            "当前运行时不是 Foundation OpenFOAM。",
            "指定 Foundation OpenFOAM v10 安装目录。",
        )
    if environment.get("WM_PROJECT_VERSION") != "10":
        raise _runtime_error(
            "OPENFOAM_VERSION_MISMATCH",
            "当前运行时不是 Foundation OpenFOAM v10。",
            "指定 Foundation OpenFOAM v10 安装目录。",
        )
    sourced_root_value = environment.get("WM_PROJECT_DIR")
    if not sourced_root_value:
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "source 后缺少 WM_PROJECT_DIR。",
            "修复 OpenFOAM etc/bashrc 后重试。",
        )
    sourced_root = Path(sourced_root_value).resolve()
    if sourced_root != resolved:
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "source 后的 OpenFOAM root 与候选不一致。",
            "指定与 etc/bashrc 匹配的 Foundation v10 root。",
        )
    appbin_value = environment.get("FOAM_APPBIN")
    if not appbin_value:
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "source 后缺少 FOAM_APPBIN。",
            "修复 OpenFOAM etc/bashrc 后重试。",
        )
    appbin = Path(appbin_value).resolve()
    if not appbin.is_dir() or not _is_within(appbin, resolved):
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "FOAM_APPBIN 不属于候选 OpenFOAM root。",
            "指定完整、未混用环境的 Foundation v10 安装目录。",
        )
    solver_value = next(
        (
            shutil.which(name, path=environment.get("PATH", ""))
            for name in ("icoFoam", "blockMesh")
            if shutil.which(name, path=environment.get("PATH", "")) is not None
        ),
        None,
    )
    if solver_value is None:
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "未能解析 Foundation v10 基础求解命令。",
            "确认 OpenFOAM 已编译并且 FOAM_APPBIN 正确。",
        )
    solver = Path(solver_value).resolve()
    if not _is_within(solver, resolved):
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "基础求解命令不属于候选 OpenFOAM root。",
            "清理混用的 OpenFOAM PATH 后重试。",
        )
    tutorial_value = environment.get("FOAM_TUTORIALS")
    tutorial_root = Path(tutorial_value).resolve() if tutorial_value else None
    if tutorial_root is not None and not tutorial_root.is_dir():
        tutorial_root = None
    return OpenFOAMRootProbe(
        root=resolved,
        environment=environment,
        foam_appbin=appbin,
        base_solver=solver,
        tutorial_root=tutorial_root,
    )


def _known_command_candidates(environment: Mapping[str, str]) -> list[Path]:
    executable = shutil.which("foamVersion", path=environment.get("PATH"))
    if executable is None:
        return []
    candidates: list[Path] = []
    current = Path(executable).resolve().parent
    for _ in range(8):
        if (current / "etc/bashrc").is_file():
            candidates.append(current)
        if current.parent == current:
            break
        current = current.parent
    return candidates


def _discover_openfoam_root(
    environment: Mapping[str, str],
    candidate_roots: Sequence[Path],
) -> OpenFOAMRootProbe:
    candidates: list[Path] = [Path(item) for item in candidate_roots]
    if environment.get("WM_PROJECT_DIR"):
        candidates.append(Path(environment["WM_PROJECT_DIR"]))
    candidates.extend(_known_command_candidates(environment))
    candidates.extend(_DEFAULT_CANDIDATES)
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in unique:
            unique.append(resolved)

    valid: list[OpenFOAMRootProbe] = []
    for candidate in unique:
        try:
            valid.append(probe_openfoam_root(candidate))
        except RuntimeConfigError:
            continue
    if not valid:
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            "未找到唯一的 Foundation OpenFOAM v10 运行时。",
            "通过 --openfoam-root、用户 TOML 或 FOAMPILOT_OPENFOAM_ROOT 指定安装目录。",
        )
    roots = tuple(dict.fromkeys(item.root for item in valid))
    if len(roots) != 1:
        summary = ", ".join(str(item) for item in roots)
        raise _runtime_error(
            "OPENFOAM_DISCOVERY_FAILED",
            f"发现多个有效的 Foundation OpenFOAM v10 候选：{summary}",
            "显式指定一个 OpenFOAM root 后重试。",
        )
    return next(item for item in valid if item.root == roots[0])


def _resolve_bubblewrap(value: object, environment: Mapping[str, str]) -> Path | None:
    text = str(value)
    if text == "auto":
        resolved = shutil.which("bwrap", path=environment.get("PATH"))
        return Path(resolved).resolve() if resolved else None
    path = Path(text).expanduser()
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise _runtime_error(
            "RUNTIME_CONFIG_INVALID",
            "bubblewrap 必须是 auto 或绝对 executable 路径。",
            "安装 bwrap，或提供有效的绝对路径。",
        )
    return path.resolve()


def resolve_runtime_config(
    *,
    cli_overrides: RuntimeOverrides | None = None,
    environ: Mapping[str, str] | None = None,
    explicit_config: Path | None = None,
    user_config: Path | None = None,
    candidate_roots: Sequence[Path] = (),
    default_isolation: IsolationPolicy = "sandbox_preferred",
) -> RuntimeResolution:
    environment = dict(os.environ if environ is None else environ)
    values: dict[str, object] = {
        "schema_version": 1,
        "openfoam.distribution": "foundation",
        "openfoam.version": "10",
        "openfoam.root": None,
        "execution.isolation": default_isolation,
        "execution.bubblewrap": "auto",
        "execution.max_mpi_ranks": 4,
        "execution.allow_dynamic_code_on_host": False,
        "execution.trusted_readonly_roots": (),
    }
    sources = {
        field: RuntimeFieldSource(source="default", locator=None)
        for field in values
    }

    if user_config is None:
        xdg_root = Path(
            environment.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        ).expanduser()
        discovered_user_config = xdg_root / "foampilot/runtime.toml"
        if discovered_user_config.exists():
            user_config = discovered_user_config
    if user_config is not None:
        resolved = user_config.expanduser().resolve()
        _apply_values(
            values,
            sources,
            _file_values(_load_toml(user_config)),
            source="user_toml",
            locator=str(resolved),
        )

    environment_config_value = environment.get("FOAMPILOT_RUNTIME_CONFIG")
    if environment_config_value is not None:
        if environment_config_value == "":
            raise _runtime_error(
                "RUNTIME_CONFIG_INVALID",
                "FOAMPILOT_RUNTIME_CONFIG 不能为空。",
                "删除该覆盖或提供有效 TOML 路径。",
            )
        environment_config = Path(environment_config_value).expanduser()
        _apply_values(
            values,
            sources,
            _file_values(_load_toml(environment_config)),
            source="environment_toml",
            locator=str(environment_config.resolve()),
        )

    if explicit_config is not None:
        _apply_values(
            values,
            sources,
            _file_values(_load_toml(explicit_config)),
            source="explicit_toml",
            locator=str(explicit_config.expanduser().resolve()),
        )

    environment_updates = _environment_updates(environment)
    for field, value in environment_updates.items():
        values[field] = value
        name = next(name for name, candidate in _ENVIRONMENT_FIELDS.items() if candidate == field)
        sources[field] = RuntimeFieldSource(source="environment", locator=name)

    if cli_overrides is not None:
        for name, value in cli_overrides.model_dump(exclude_none=True).items():
            field = _CLI_FIELDS[name]
            values[field] = value
            sources[field] = RuntimeFieldSource(
                source="cli",
                locator=_CLI_LOCATORS[name],
            )

    configured_root = values["openfoam.root"]
    if configured_root is None:
        openfoam = _discover_openfoam_root(environment, candidate_roots)
        values["openfoam.root"] = openfoam.root
        sources["openfoam.root"] = RuntimeFieldSource(
            source="discovery",
            locator=str(openfoam.root),
        )
    else:
        openfoam = probe_openfoam_root(Path(configured_root))
        values["openfoam.root"] = openfoam.root

    bubblewrap = _resolve_bubblewrap(values["execution.bubblewrap"], environment)
    values["execution.bubblewrap"] = bubblewrap
    try:
        config = RuntimeConfig(
            schema_version=values["schema_version"],
            distribution=values["openfoam.distribution"],
            version=values["openfoam.version"],
            openfoam_root=values["openfoam.root"],
            isolation=values["execution.isolation"],
            bubblewrap=bubblewrap,
            max_mpi_ranks=values["execution.max_mpi_ranks"],
            allow_dynamic_code_on_host=values[
                "execution.allow_dynamic_code_on_host"
            ],
            trusted_readonly_roots=values["execution.trusted_readonly_roots"],
        )
    except ValidationError as error:
        raise _runtime_error(
            "RUNTIME_CONFIG_INVALID",
            "Runtime 配置字段无效。",
            "检查路径、isolation 和资源限制后重试。",
            type(error).__name__,
        ) from error
    return RuntimeResolution(
        config=config,
        provenance=RuntimeConfigProvenance(fields=sources),
    )
