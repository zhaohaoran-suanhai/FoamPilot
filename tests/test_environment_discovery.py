from __future__ import annotations

import os
from pathlib import Path
import sys

from foampilot.environment import (
    discover_environment,
    enrich_command_help,
)
from foampilot.runtime import RuntimeConfig


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
        "export WM_PROJECT_VERSION=10\n",
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
            tutorial_root=root / "tutorials",
            python_executable=Path(sys.executable),
            bubblewrap=Path("/usr/local/bin/bwrap"),
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
    assert "tutorial_root" not in snapshot.agent_payload()
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
        tutorial_root=root / "tutorials",
        python_executable=Path(sys.executable),
        bubblewrap=Path("/usr/local/bin/bwrap"),
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


def test_discovery_uses_sourced_path_not_parent_process_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    monkeypatch.setenv("PATH", os.defpath)

    snapshot = discover_environment(
        RuntimeConfig(
            openfoam_root=root,
            tutorial_root=root / "tutorials",
            python_executable=Path(sys.executable),
            bubblewrap=Path("/usr/local/bin/bwrap"),
        ),
        workspace_root=tmp_path / "runs",
    )

    assert "icoFoam" in snapshot.executable_names
