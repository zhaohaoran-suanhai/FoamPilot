from pathlib import Path
import json

import pytest

from foampilot.artifacts import ArtifactStore


def test_finalize_is_exclusive_and_verify_detects_mutation(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    artifact = run_dir / "evidence.txt"
    artifact.write_text("frozen\n", encoding="utf-8")

    manifest = store.finalize(run_dir)
    manifest_hash = store.manifest_sha256(run_dir)

    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["build_seconds"] >= 0
    assert len(manifest_hash) == 64
    assert store.verify(run_dir) == []
    with pytest.raises(FileExistsError):
        store.finalize(run_dir)

    artifact.write_text("mutated\n", encoding="utf-8")

    assert store.verify(run_dir) == ["hash mismatch: evidence.txt"]


def test_artifact_store_rejects_run_outside_root(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "runs")
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside artifact store"):
        store.finalize(outside)
