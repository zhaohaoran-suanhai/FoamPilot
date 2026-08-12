from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from foampilot.assets import (
    BundleMember,
    OpenFOAMPolyMeshAdapter,
    compute_bundle_manifest_sha256,
)
from foampilot.environment import discover_environment
from foampilot.preprocessing import probe_provided_mesh
from foampilot.runtime import RuntimeConfigError, resolve_runtime_config, run_preflight
from foampilot.tasks import PublicAsset


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/poly_mesh/minimal"


def _fixture_declaration(source_root: Path) -> PublicAsset:
    source_path = FIXTURE.relative_to(source_root).as_posix()
    members = tuple(
        BundleMember(
            relative_path=path.relative_to(FIXTURE).as_posix(),
            logical_name=(
                path.relative_to(FIXTURE).as_posix()[:-3]
                if path.suffix == ".gz"
                else path.relative_to(FIXTURE).as_posix()
            ),
            sha256=sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )
        for path in sorted(FIXTURE.rglob("*"))
        if path.is_file()
    )
    manifest = compute_bundle_manifest_sha256(
        adapter_id="foampilot.asset.openfoam-poly-mesh",
        kind="openfoam_poly_mesh",
        source_path=source_path,
        install_path="constant/polyMesh",
        region=None,
        members=members,
    )
    return PublicAsset(
        path=source_path,
        sha256=manifest,
        purpose="synthetic real checkMesh gate",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256=manifest,
    )


@pytest.mark.real_openfoam
def test_real_provided_poly_mesh_probe(tmp_path: Path) -> None:
    runtime_file = tmp_path / "runtime.toml"
    runtime_file.write_text("schema_version = 1\n", encoding="utf-8")
    try:
        resolution = resolve_runtime_config(
            environ={},
            user_config=runtime_file,
            candidate_roots=(ROOT.parent / "OpenFOAM-10",),
        )
    except (OSError, RuntimeError, ValueError, RuntimeConfigError) as error:
        pytest.skip(f"OPENFOAM10_NOT_AVAILABLE: {error}")
    runtime = resolution.config
    report = run_preflight(runtime, workspace_root=tmp_path)
    if not report.ok or report.environment is None:
        pytest.skip(
            "OPENFOAM10_NOT_AVAILABLE: "
            f"{report.failure_code or report.failure_message or 'preflight failed'}"
        )
    environment = discover_environment(
        runtime,
        tmp_path,
        shortlisted=("checkMesh",),
    )
    adapter = OpenFOAMPolyMeshAdapter()
    source_root = ROOT
    bundle = adapter.inspect(source_root, _fixture_declaration(source_root))
    case_root = tmp_path / "probe-case"
    adapter.stage(bundle, source_root, case_root)

    facts = probe_provided_mesh(
        case_root,
        environment,
        runtime,
        budget_seconds=60,
    )

    assert facts.mesh_check.return_code == 0
    assert facts.mesh_check.mesh_ok is True
    assert facts.metrics.cells == 2
