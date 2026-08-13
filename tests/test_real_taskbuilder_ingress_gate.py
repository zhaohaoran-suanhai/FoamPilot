from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path

import pytest

from foampilot.assets import BundleMember, compute_bundle_manifest_sha256
from foampilot.models import InMemoryModelTraceSink
from foampilot.taskbuilder import (
    build_task_ingress_context,
    extract_task_draft,
    validate_task_draft,
)
from foampilot.tasks import PublicAsset
from tests.support.taskbuilder import (
    RecordingExtractionGateway,
    extraction_payload,
    task_extraction_budget,
)


POLY_MESH_PATH = Path("mesh/openfoam/constant/polyMesh")


def _declared_poly_mesh(case_root: Path) -> PublicAsset:
    mesh_root = case_root / POLY_MESH_PATH
    if not mesh_root.is_dir():
        pytest.fail(
            "FOAMPILOT_REAL_POLYMESH_CASE_ROOT does not contain "
            f"{POLY_MESH_PATH.as_posix()}"
        )
    members: list[BundleMember] = []
    for path in sorted(mesh_root.rglob("*")):
        relative_path = path.relative_to(mesh_root).as_posix()
        if path.is_symlink():
            pytest.fail(f"real polyMesh member is a symlink: {relative_path}")
        if path.is_dir():
            continue
        if not path.is_file():
            pytest.fail(f"real polyMesh member is not regular: {relative_path}")
        logical_name = (
            relative_path[:-3]
            if "/" not in relative_path and relative_path.endswith(".gz")
            else relative_path
        )
        content = path.read_bytes()
        members.append(
            BundleMember(
                relative_path=relative_path,
                logical_name=logical_name,
                sha256=sha256(content).hexdigest(),
                bytes=len(content),
            )
        )
    manifest_sha256 = compute_bundle_manifest_sha256(
        adapter_id="foampilot.asset.openfoam-poly-mesh",
        kind="openfoam_poly_mesh",
        source_path=POLY_MESH_PATH.as_posix(),
        install_path="constant/polyMesh",
        region=None,
        members=members,
    )
    return PublicAsset(
        path=POLY_MESH_PATH.as_posix(),
        sha256=manifest_sha256,
        purpose="real provided polyMesh ingress gate",
        kind="directory",
        install_path="constant/polyMesh",
        bundle_manifest_sha256=manifest_sha256,
    )


def test_real_poly_mesh_reaches_refactored_extractor_and_validation() -> None:
    configured_root = os.environ.get("FOAMPILOT_REAL_POLYMESH_CASE_ROOT")
    if configured_root is None:
        pytest.skip("FOAMPILOT_REAL_POLYMESH_CASE_ROOT is unset")
    case_root = Path(configured_root).expanduser().resolve(strict=True)
    asset = _declared_poly_mesh(case_root)
    ingress_context = build_task_ingress_context([asset], case_root)
    gateway = RecordingExtractionGateway(extraction_payload(facts=[]))

    draft = extract_task_draft(
        "Use the supplied native mesh for a two-dimensional flow.",
        [asset],
        gateway,
        budget=task_extraction_budget(),
        trace=InMemoryModelTraceSink(),
        ingress_context=ingress_context,
    )
    review = validate_task_draft(draft)

    facts = draft.fact_map()
    assert facts["geometry"].value["mode"] == "openfoam_mesh"
    assert facts["mesh"].value["strategy"] == "provided"
    assert [
        (issue.code, issue.field_path)
        for issue in review.issues
        if issue.severity == "blocking"
    ] == [("TASK_UNIT_AMBIGUOUS", "geometry.length_unit")]
    assert len(gateway.requests) == 1
    model_prompt = gateway.requests[0].user_prompt
    assert "PolyMeshTopologyFacts" in model_prompt
    assert "FoamFile" not in model_prompt
    assert "OpenFOAM: The Open Source CFD Toolbox" not in model_prompt
