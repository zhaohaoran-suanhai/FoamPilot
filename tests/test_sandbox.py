from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.cli.main import main
from foampilot.runtime.models import RuntimeConfig
from foampilot.runtime.preflight import RuntimePreflightReport, run_preflight
from foampilot.runtime.sandbox import (
    SandboxBuildError,
    build_sandbox_argv,
    probe_sandbox,
)


def _write_executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _environment(
    root: Path,
    *,
    tutorial_root: Path | None,
    workspace: Path,
) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=root,
        tutorial_root=tutorial_root,
        workspace_root=workspace,
        workspace_writable=True,
        commands=[
            CommandFact(name="icoFoam", path=root / "platforms/fake/bin/icoFoam")
        ],
        mpi_launcher=None,
        gmsh=None,
        max_mpi_ranks=1,
    )


def _fake_openfoam(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "vendor/OpenFOAM-10"
    appbin = root / "platforms/fake/bin"
    tutorials = root / "tutorials"
    tutorials.mkdir(parents=True)
    (root / "etc").mkdir()
    _write_executable(appbin / "icoFoam")
    (root / "etc/bashrc").write_text(
        'export WM_PROJECT="OpenFOAM"\n'
        'export WM_PROJECT_VERSION="10"\n'
        f'export WM_PROJECT_DIR="{root}"\n'
        f'export FOAM_APPBIN="{appbin}"\n'
        f'export FOAM_TUTORIALS="{tutorials}"\n'
        f'export PATH="{appbin}:$PATH"\n',
        encoding="utf-8",
    )
    return root, tutorials


def test_mount_plan_is_dynamic_and_hides_tutorials(tmp_path: Path) -> None:
    root, tutorials = _fake_openfoam(tmp_path)
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(bwrap)
    case = tmp_path / "runs/run-1/attempt-01/case"
    case.mkdir(parents=True)
    environment = _environment(root, tutorial_root=tutorials, workspace=case.parent)

    launch = build_sandbox_argv(
        config=RuntimeConfig(openfoam_root=root, bubblewrap=bwrap),
        environment=environment,
        case_dir=case,
        protected_paths=(tutorials,),
        memory_mib=1024,
        cpu_seconds=30,
        typed_argv=(str(root / "platforms/fake/bin/icoFoam"),),
    )

    assert all("/home/edwin" not in item for item in launch.argv)
    bind_index = launch.argv.index(str(root.resolve()))
    hide_index = launch.argv.index(str(tutorials.resolve()), bind_index + 1)
    assert hide_index > bind_index
    assert launch.argv[-1] == str(root / "platforms/fake/bin/icoFoam")
    assert launch.hidden_paths == (tutorials.resolve(),)


def test_trusted_root_intersection_with_protected_path_is_rejected(
    tmp_path: Path,
) -> None:
    root, tutorials = _fake_openfoam(tmp_path)
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(bwrap)
    case = tmp_path / "case"
    case.mkdir()
    trusted = tmp_path / "trusted"
    protected = trusted / "private"
    protected.mkdir(parents=True)

    with pytest.raises(SandboxBuildError) as captured:
        build_sandbox_argv(
            config=RuntimeConfig(
                openfoam_root=root,
                bubblewrap=bwrap,
                trusted_readonly_roots=(trusted,),
            ),
            environment=_environment(root, tutorial_root=tutorials, workspace=case),
            case_dir=case,
            protected_paths=(protected,),
            memory_mib=128,
            cpu_seconds=5,
            typed_argv=("/usr/bin/true",),
        )

    assert captured.value.code == "TRUSTED_RUNTIME_ROOT_INVALID"


def test_probe_uses_full_builder_without_leaking_paths(tmp_path: Path) -> None:
    root, tutorials = _fake_openfoam(tmp_path)
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(bwrap)
    case = tmp_path / "case"
    case.mkdir()
    calls: list[tuple[str, ...]] = []

    def successful_executor(argv, **kwargs):
        del kwargs
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    probe = probe_sandbox(
        config=RuntimeConfig(openfoam_root=root, bubblewrap=bwrap),
        environment=_environment(root, tutorial_root=tutorials, workspace=case),
        case_dir=case,
        protected_paths=(tutorials,),
        memory_mib=256,
        cpu_seconds=5,
        executor=successful_executor,
    )

    assert probe.ok
    assert calls and calls[0][-1] == "/usr/bin/true"
    assert probe.builder_sha256 is not None
    assert str(tmp_path) not in probe.model_dump_json()
    assert probe.protected_path_count == 1


def test_preflight_records_actual_python_and_full_probe(tmp_path: Path) -> None:
    root, _ = _fake_openfoam(tmp_path)
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(bwrap)

    def successful_executor(argv, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    report = run_preflight(
        RuntimeConfig(
            openfoam_root=root,
            bubblewrap=bwrap,
            isolation="sandbox_required",
        ),
        workspace_root=tmp_path / "runs",
        sandbox_executor=successful_executor,
    )

    assert isinstance(report, RuntimePreflightReport)
    assert report.ok
    assert report.python_executable == Path(sys.executable).resolve()
    assert report.environment is not None
    assert report.sandbox_probe.status == "passed"
    assert "python_executable" not in report.environment.model_dump()


def test_preflight_preserves_environment_discovery_failure_code(
    tmp_path: Path,
) -> None:
    report = run_preflight(
        RuntimeConfig(openfoam_root=tmp_path / "missing-OpenFOAM-10"),
        workspace_root=tmp_path / "runs",
    )

    assert report.ok is False
    assert report.failure_code == "OPENFOAM_DISCOVERY_FAILED"
    assert report.sandbox_probe.status == "not_requested"


def test_preflight_reports_non_writable_workspace_as_structured_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, tutorials = _fake_openfoam(tmp_path)
    workspace = tmp_path / "runs"
    environment = _environment(
        root,
        tutorial_root=tutorials,
        workspace=workspace,
    ).model_copy(update={"workspace_writable": False})
    monkeypatch.setattr(
        "foampilot.runtime.preflight.discover_environment",
        lambda config, workspace_root: environment,
    )

    report = run_preflight(
        RuntimeConfig(openfoam_root=root, isolation="trusted_host"),
        workspace_root=workspace,
    )

    assert report.ok is False
    assert report.failure_code == "WORKSPACE_NOT_WRITABLE"
    assert report.failure_message
    assert report.failure_recovery


def test_required_sandbox_failure_is_structured_in_cli_json(
    tmp_path: Path,
    capsys,
) -> None:
    root, _ = _fake_openfoam(tmp_path)
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(
        bwrap,
        "#!/bin/sh\nprintf 'Operation not permitted' >&2\nexit 1\n",
    )

    exit_code = main(
        [
            "preflight",
            "--openfoam-root",
            str(root),
            "--execution-isolation",
            "sandbox_required",
            "--bubblewrap",
            str(bwrap),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert payload["status"] == "BLOCKED_ENVIRONMENT"
    assert payload["failure"]["code"] == "SANDBOX_REQUIRED_UNAVAILABLE"
    assert payload["failure"]["message"]
    assert payload["failure"]["recovery"]
