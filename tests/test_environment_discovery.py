from __future__ import annotations

import os
from pathlib import Path

import pytest

from foampilot.environment import (
    CommandFact,
    EnvironmentSnapshot,
    discover_environment,
    enrich_command_help,
)
from foampilot.runtime import RuntimeConfig, RuntimeConfigError
from foampilot.runtime.plan_runner import PlanRunner
from foampilot.runtime.protection import runtime_protected_paths


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _make_fake_openfoam_tree(tmp_path: Path) -> Path:
    root = tmp_path / "OpenFOAM-10"
    binary = root / "platforms/fake/bin"
    binary.mkdir(parents=True)
    (root / "tutorials").mkdir()
    (root / "etc").mkdir()
    (root / "etc/bashrc").write_text(
        f'export PATH="{binary}:$PATH"\n'
        f'export FOAM_APPBIN="{binary}"\n'
        f'export FOAM_TUTORIALS="{root / "tutorials"}"\n'
        'export WM_PROJECT="OpenFOAM"\n'
        "export WM_PROJECT_VERSION=10\n"
        f'export WM_PROJECT_DIR="{root}"\n',
        encoding="utf-8",
    )
    _write_executable(
        binary / "foamVersion",
        "#!/bin/sh\nprintf 'OpenFOAM-10\\n'\n",
    )
    _write_executable(
        binary / "icoFoam",
        "#!/bin/sh\nprintf 'icoFoam deterministic help\\n'\n",
    )
    _write_executable(
        binary / "blockMesh",
        "#!/bin/sh\nprintf 'blockMesh deterministic help\\n'\n",
    )
    return root


def test_discovery_reports_facts_without_physics_ontology(
    tmp_path: Path,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    snapshot = discover_environment(
        RuntimeConfig(
            openfoam_root=root,
            max_mpi_ranks=4,
        ),
        workspace_root=tmp_path / "runs",
        shortlisted=("icoFoam",),
    )

    assert snapshot.distribution == "foundation"
    assert snapshot.version == "10"
    assert {"foamVersion", "icoFoam", "blockMesh"} <= (
        snapshot.executable_names
    )
    assert "solver_families" not in snapshot.model_dump()
    agent_payload = snapshot.agent_payload()
    assert "tutorial_root" not in agent_payload
    assert "openfoam_root" not in agent_payload
    assert "workspace_root" not in agent_payload
    assert "commands" not in agent_payload
    assert agent_payload["executable_names"] == sorted(
        snapshot.executable_names
    )
    assert agent_payload["mpi_available"] is (
        snapshot.mpi_launcher is not None
    )
    assert agent_payload["gmsh_available"] is (snapshot.gmsh is not None)
    assert snapshot.workspace_writable
    assert next(
        item for item in snapshot.commands if item.name == "icoFoam"
    ).help_excerpt == "icoFoam deterministic help"
    assert next(
        item for item in snapshot.commands if item.name == "blockMesh"
    ).help_excerpt is None


def test_command_help_can_be_enriched_after_agent_planning(
    tmp_path: Path,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    config = RuntimeConfig(
        openfoam_root=root,
    )
    snapshot = discover_environment(
        config,
        workspace_root=tmp_path / "runs",
    )

    enriched = enrich_command_help(
        config,
        snapshot,
        shortlisted=("blockMesh", "missingCommand"),
    )

    assert next(
        item for item in enriched.commands if item.name == "blockMesh"
    ).help_excerpt == "blockMesh deterministic help"
    assert "missingCommand" not in enriched.executable_names
    assert all(
        command.help_excerpt is None
        for command in snapshot.commands
    )


def test_command_help_executes_canonical_discovered_path(
    tmp_path: Path,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    untrusted_bin = tmp_path / "untrusted/bin"
    untrusted_bin.mkdir(parents=True)
    marker = tmp_path / "shadow-executed"
    _write_executable(
        untrusted_bin / "blockMesh",
        f'#!/bin/sh\nprintf untrusted\nprintf hit > "{marker}"\n',
    )
    bashrc = root / "etc/bashrc"
    bashrc.write_text(
        bashrc.read_text(encoding="utf-8")
        + f'export PATH="{untrusted_bin}:$PATH"\n',
        encoding="utf-8",
    )

    snapshot = discover_environment(
        RuntimeConfig(openfoam_root=root),
        workspace_root=tmp_path / "runs",
        shortlisted=("blockMesh",),
    )

    block_mesh = next(
        item for item in snapshot.commands if item.name == "blockMesh"
    )
    assert block_mesh.help_excerpt == "blockMesh deterministic help"
    assert not marker.exists()


def test_discovery_uses_sourced_path_not_parent_process_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    monkeypatch.setenv("PATH", os.defpath)

    snapshot = discover_environment(
        RuntimeConfig(
            openfoam_root=root,
        ),
        workspace_root=tmp_path / "runs",
    )

    assert "icoFoam" in snapshot.executable_names


def test_discovery_sources_openfoam_with_isolated_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    real_home = tmp_path / "real-home"
    prefs = real_home / ".OpenFOAM/10/prefs.sh"
    prefs.parent.mkdir(parents=True)
    marker = tmp_path / "prefs-executed"
    prefs.write_text(
        f'printf triggered > "{marker}"\n'
        'export WM_OPTIONS="host-user-custom"\n',
        encoding="utf-8",
    )
    bashrc = root / "etc/bashrc"
    bashrc.write_text(
        bashrc.read_text(encoding="utf-8")
        + '\nif [ -f "$HOME/.OpenFOAM/10/prefs.sh" ]; then\n'
        + '    source "$HOME/.OpenFOAM/10/prefs.sh"\n'
        + "fi\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(real_home))

    snapshot = discover_environment(
        RuntimeConfig(openfoam_root=root),
        workspace_root=tmp_path / "runs",
    )

    assert "icoFoam" in snapshot.executable_names
    assert not marker.exists()


def test_discovery_rejects_executable_symlink_escaping_approved_roots(
    tmp_path: Path,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    outside = tmp_path / "outside-tool"
    _write_executable(outside, "#!/bin/sh\nexit 0\n")
    binary = root / "platforms/fake/bin"
    (binary / "escapedTool").symlink_to(outside)

    snapshot = discover_environment(
        RuntimeConfig(openfoam_root=root),
        workspace_root=tmp_path / "runs",
    )

    assert "escapedTool" not in snapshot.executable_names


def test_discovery_does_not_advertise_untrusted_mpi_launcher_for_serial_run(
    tmp_path: Path,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    untrusted_bin = tmp_path / "custom-mpi/bin"
    untrusted_bin.mkdir(parents=True)
    _write_executable(untrusted_bin / "mpirun", "#!/bin/sh\nexit 0\n")
    bashrc = root / "etc/bashrc"
    bashrc.write_text(
        bashrc.read_text(encoding="utf-8")
        + f'export PATH="{untrusted_bin}:$PATH"\n',
        encoding="utf-8",
    )
    config = RuntimeConfig(openfoam_root=root)

    snapshot = discover_environment(config, tmp_path / "runs")
    runner = PlanRunner(
        runtime_config=config,
        environment=snapshot,
        available_executables={"icoFoam"},
        workspace_root=tmp_path / "runs",
    )

    assert snapshot.mpi_launcher is None
    assert "icoFoam" in runner.available_executables


def test_available_executable_names_include_optional_external_gmsh(
    tmp_path: Path,
) -> None:
    snapshot = EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=tmp_path / "OpenFOAM-10",
        tutorial_root=tmp_path / "OpenFOAM-10/tutorials",
        workspace_root=tmp_path / "runs",
        workspace_writable=True,
        commands=[
            CommandFact(name="gmshToFoam", path=tmp_path / "gmshToFoam"),
        ],
        mpi_launcher=None,
        gmsh=Path("/usr/bin/gmsh"),
        max_mpi_ranks=1,
    )

    assert snapshot.executable_names == {"gmshToFoam"}
    assert snapshot.available_executable_names == {"gmsh", "gmshToFoam"}
    assert snapshot.agent_payload()["executable_names"] == [
        "gmsh",
        "gmshToFoam",
    ]


def test_discovery_rejects_sourced_root_mismatch(tmp_path: Path) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    bashrc = root / "etc/bashrc"
    bashrc.write_text(
        bashrc.read_text(encoding="utf-8").replace(
            f'WM_PROJECT_DIR="{root}"',
            'WM_PROJECT_DIR="/different"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeConfigError, match="OPENFOAM_DISCOVERY_FAILED"):
        discover_environment(RuntimeConfig(openfoam_root=root), tmp_path / "runs")


def test_discovery_derives_optional_tutorial_root(tmp_path: Path) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    snapshot = discover_environment(
        RuntimeConfig(openfoam_root=root),
        tmp_path / "runs",
    )
    assert snapshot.tutorial_root == (root / "tutorials").resolve()

    bashrc = root / "etc/bashrc"
    bashrc.write_text(
        "\n".join(
            line
            for line in bashrc.read_text(encoding="utf-8").splitlines()
            if "FOAM_TUTORIALS" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    without_tutorials = discover_environment(
        RuntimeConfig(openfoam_root=root),
        tmp_path / "other-runs",
    )
    assert without_tutorials.tutorial_root is None


def test_runtime_protected_paths_adds_environment_and_evaluator_roots(
    tmp_path: Path,
) -> None:
    tutorials = tmp_path / "OpenFOAM-10/tutorials"
    evaluator = tmp_path / "wheel/foampilot/qualification/data"
    snapshot = EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=tmp_path / "OpenFOAM-10",
        tutorial_root=tutorials,
        workspace_root=tmp_path / "runs",
        workspace_writable=True,
        commands=[],
        mpi_launcher=None,
        gmsh=None,
        max_mpi_ranks=1,
    )

    protected = runtime_protected_paths(
        [str(tmp_path / "declared")],
        snapshot,
        (evaluator,),
    )

    assert protected == (
        (tmp_path / "declared").resolve(),
        tutorials.resolve(),
        evaluator.resolve(),
    )
