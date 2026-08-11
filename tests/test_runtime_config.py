from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from foampilot.runtime.config import resolve_runtime_config
from foampilot.runtime.models import (
    RuntimeConfig,
    RuntimeConfigError,
    RuntimeOverrides,
)


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_openfoam(root: Path, *, version: str = "10") -> Path:
    appbin = root / "platforms/fake/bin"
    tutorials = root / "tutorials"
    (root / "etc").mkdir(parents=True)
    tutorials.mkdir()
    _write_executable(appbin / "icoFoam", "#!/bin/sh\nexit 0\n")
    _write_executable(appbin / "foamVersion", f"#!/bin/sh\nprintf '{version}\\n'\n")
    (root / "etc/bashrc").write_text(
        "\n".join(
            (
                'export WM_PROJECT="OpenFOAM"',
                f'export WM_PROJECT_VERSION="{version}"',
                f'export WM_PROJECT_DIR="{root}"',
                f'export FOAM_APPBIN="{appbin}"',
                f'export FOAM_TUTORIALS="{tutorials}"',
                f'export PATH="{appbin}:$PATH"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def fake_openfoam(tmp_path: Path) -> Path:
    return _fake_openfoam(tmp_path / "OpenFOAM-10")


def _runtime_toml(
    path: Path,
    *,
    root: Path,
    ranks: int | None = None,
    isolation: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "schema_version = 1",
        "[openfoam]",
        'distribution = "foundation"',
        'version = "10"',
        f'root = "{root}"',
    ]
    if ranks is not None or isolation is not None:
        lines.append("[execution]")
    if ranks is not None:
        lines.append(f"max_mpi_ranks = {ranks}")
    if isolation is not None:
        lines.append(f'isolation = "{isolation}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_runtime_file_rejects_unknown_fields(
    tmp_path: Path,
    fake_openfoam: Path,
) -> None:
    path = _runtime_toml(tmp_path / "runtime.toml", root=fake_openfoam)
    path.write_text(
        path.read_text(encoding="utf-8") + "unknown = true\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeConfigError) as captured:
        resolve_runtime_config(
            explicit_config=path,
            candidate_roots=(),
            environ={},
        )

    assert captured.value.code == "RUNTIME_CONFIG_INVALID"


def test_runtime_defaults_to_sandbox_preferred(fake_openfoam: Path) -> None:
    resolution = resolve_runtime_config(
        environ={"FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam)},
        candidate_roots=(),
    )

    assert resolution.config.isolation == "sandbox_preferred"
    assert resolution.config.max_mpi_ranks == 4
    assert resolution.config.allow_dynamic_code_on_host is False
    assert resolution.provenance.fields["execution.isolation"].source == "default"


def test_runtime_accepts_qualification_default(fake_openfoam: Path) -> None:
    resolution = resolve_runtime_config(
        environ={"FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam)},
        candidate_roots=(),
        default_isolation="sandbox_required",
    )

    assert resolution.config.isolation == "sandbox_required"
    assert resolution.provenance.fields["execution.isolation"].source == "default"


def test_runtime_records_public_isolation_cli_locator(
    fake_openfoam: Path,
) -> None:
    resolution = resolve_runtime_config(
        cli_overrides=RuntimeOverrides(
            openfoam_root=fake_openfoam,
            isolation="sandbox_required",
        ),
        environ={},
        candidate_roots=(),
    )

    assert resolution.provenance.fields["execution.isolation"].model_dump() == {
        "source": "cli",
        "locator": "--execution-isolation",
    }


def test_runtime_rejects_legacy_execution_backend(fake_openfoam: Path) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(
            openfoam_root=fake_openfoam,
            execution_backend="auto",
        )


def test_runtime_precedence_is_leafwise(
    tmp_path: Path,
    fake_openfoam: Path,
) -> None:
    user = _runtime_toml(tmp_path / "user.toml", root=fake_openfoam, ranks=1)
    env_file = _runtime_toml(tmp_path / "env.toml", root=fake_openfoam, ranks=2)
    explicit = _runtime_toml(
        tmp_path / "explicit.toml",
        root=fake_openfoam,
        ranks=3,
    )
    result = resolve_runtime_config(
        cli_overrides=RuntimeOverrides(max_mpi_ranks=6),
        explicit_config=explicit,
        user_config=user,
        environ={
            "FOAMPILOT_RUNTIME_CONFIG": str(env_file),
            "FOAMPILOT_MAX_MPI_RANKS": "5",
            "FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam),
        },
        candidate_roots=(),
    )

    assert result.config.max_mpi_ranks == 6
    assert result.provenance.fields["execution.max_mpi_ranks"].model_dump() == {
        "source": "cli",
        "locator": "--max-mpi-ranks",
    }

    environment_only = resolve_runtime_config(
        environ={
            "FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam),
            "FOAMPILOT_MAX_MPI_RANKS": "5",
        },
        candidate_roots=(),
    )
    source = environment_only.provenance.fields["execution.max_mpi_ranks"]
    assert source.model_dump() == {
        "source": "environment",
        "locator": "FOAMPILOT_MAX_MPI_RANKS",
    }
    assert "5" not in source.model_dump_json()


@pytest.mark.parametrize("value", ["1", "yes", "TRUE", ""])
def test_runtime_boolean_environment_is_strict(
    value: str,
    fake_openfoam: Path,
) -> None:
    with pytest.raises(RuntimeConfigError) as captured:
        resolve_runtime_config(
            environ={
                "FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam),
                "FOAMPILOT_ALLOW_DYNAMIC_CODE_ON_HOST": value,
            },
            candidate_roots=(),
        )
    assert captured.value.code == "RUNTIME_CONFIG_INVALID"


def test_xdg_user_config_is_discovered(
    tmp_path: Path,
    fake_openfoam: Path,
) -> None:
    xdg = tmp_path / "xdg"
    _runtime_toml(
        xdg / "foampilot/runtime.toml",
        root=fake_openfoam,
        ranks=2,
    )

    result = resolve_runtime_config(
        environ={"XDG_CONFIG_HOME": str(xdg)},
        candidate_roots=(),
    )

    assert result.config.max_mpi_ranks == 2
    assert result.provenance.fields["execution.max_mpi_ranks"].source == "user_toml"


def test_discovery_refuses_multiple_valid_foundation_v10_roots(
    tmp_path: Path,
) -> None:
    first = _fake_openfoam(tmp_path / "first")
    second = _fake_openfoam(tmp_path / "second")

    with pytest.raises(RuntimeConfigError) as captured:
        resolve_runtime_config(
            environ={"PATH": os.defpath},
            candidate_roots=(first, second),
        )

    assert captured.value.code == "OPENFOAM_DISCOVERY_FAILED"
    assert str(first.resolve()) in captured.value.message
    assert str(second.resolve()) in captured.value.message
