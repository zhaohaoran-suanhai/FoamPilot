from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from foampilot.knowledge import load_knowledge_corpus
from tools.audit_source_provenance import audit_repository


PROJECT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = PROJECT / "src/foampilot/knowledge/openfoam10"
REPLAY_INDEX = PROJECT / "tests/fixtures/artifact-replay/index.yaml"
GENERATOR = PROJECT / "tools/generate_synthetic_replay.py"


def test_no_embedded_oauth_or_private_provider_protocol() -> None:
    report = audit_repository(PROJECT)

    assert report.forbidden_matches == ()


def test_all_knowledge_has_traceable_fact_sources() -> None:
    entries = load_knowledge_corpus(KNOWLEDGE_ROOT)

    assert entries
    assert all(entry.source.title for entry in entries)
    assert all(entry.source.locator for entry in entries)
    assert all(len(entry.source.sha256) == 64 for entry in entries)
    assert all(entry.source.license_spdx for entry in entries)
    assert all(not entry.source.locator.startswith("/") for entry in entries)
    for entry in entries:
        if entry.source.kind != "reviewed_engineering":
            continue
        source = PROJECT / entry.source.locator
        assert source.is_file()
        assert sha256(source.read_bytes()).hexdigest() == entry.source.sha256


def test_replay_assets_are_owned_synthetic_outputs() -> None:
    payload = yaml.safe_load(REPLAY_INDEX.read_text(encoding="utf-8"))
    generator_hash = sha256(GENERATOR.read_bytes()).hexdigest()

    assert payload["schema_version"] == 2
    assert payload["fixtures"]
    assert all(
        item["source_kind"] == "synthetic_foampilot"
        for item in payload["fixtures"]
    )
    assert all(
        item["generator_sha256"] == generator_hash
        for item in payload["fixtures"]
    )


def test_provenance_documents_state_the_packaging_boundary() -> None:
    license_text = (PROJECT / "LICENSE").read_text(encoding="utf-8")
    notice = (PROJECT / "NOTICE.md").read_text(encoding="utf-8")
    provenance = (PROJECT / "PROVENANCE.md").read_text(encoding="utf-8")
    notices = (PROJECT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "Copyright (c) 2025 Ling Yue" in license_text
    assert "Copyright (c) 2026 Haoran Zhao" in license_text
    assert "版权、工程来源与第三方边界" in notice
    assert "不表示 FoamPilot 是 Foam-Agent 的官方延续" in notice
    assert "版权与许可" in provenance
    assert "FoamPilot 原创代码与文本" in provenance
    assert "FoamPilot 合成资产" in provenance
    assert "带来源的事实总结" in provenance
    assert "外部运行时依赖" in provenance
    assert "Foam-Agent 历史参考" in notices
    assert "不打包 OpenFOAM 源码" in notices
    assert "外部运行时" in notices


def test_compare_root_detects_unexplained_text_reuse(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    upstream = tmp_path / "upstream"
    candidate.mkdir()
    upstream.mkdir()
    shared = (
        "This deliberately distinctive implementation sentence contains enough "
        "tokens to represent an unexplained copied source line in an audit gate.\n"
    )
    (candidate / "module.py").write_text(shared, encoding="utf-8")
    (upstream / "legacy.py").write_text(shared, encoding="utf-8")

    report = audit_repository(candidate, compare_root=upstream)

    assert report.long_line_matches
    assert report.shingle_matches
    assert report.passed is False


def test_compare_root_accepts_independently_written_text(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    upstream = tmp_path / "upstream"
    candidate.mkdir()
    upstream.mkdir()
    (candidate / "module.py").write_text(
        "def compute_flux(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    (upstream / "legacy.py").write_text(
        "class ArchiveReader:\n    pass\n",
        encoding="utf-8",
    )

    report = audit_repository(candidate, compare_root=upstream)

    assert report.passed is True


def test_explicit_missing_compare_root_fails_closed(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    with pytest.raises(FileNotFoundError, match="compare root"):
        audit_repository(candidate, compare_root=tmp_path / "missing")


def test_standard_mit_template_is_not_reported_as_source_reuse(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    upstream = tmp_path / "upstream"
    candidate.mkdir()
    upstream.mkdir()
    license_bytes = (PROJECT / "LICENSE").read_bytes()
    (candidate / "LICENSE").write_bytes(license_bytes)
    (upstream / "LICENSE").write_bytes(license_bytes)

    report = audit_repository(candidate, compare_root=upstream)

    assert report.long_line_matches == ()
    assert report.shingle_matches == ()
    assert report.passed is True


def test_generated_audit_report_is_not_part_of_its_own_digest(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    report_path = candidate / "docs/reports/clean-source-audit.json"
    report_path.parent.mkdir(parents=True)
    (candidate / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = audit_repository(candidate)

    report_path.write_text("{}\n", encoding="utf-8")
    after = audit_repository(candidate)

    assert after.scanned_files == before.scanned_files
    assert after.root_sha256 == before.root_sha256


def test_build_metadata_is_not_part_of_repository_digest(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = audit_repository(candidate)

    metadata = candidate / "src/foampilot.egg-info"
    metadata.mkdir(parents=True)
    (metadata / "SOURCES.txt").write_text(
        "generated build metadata\n",
        encoding="utf-8",
    )
    after = audit_repository(candidate)

    assert after.scanned_files == before.scanned_files
    assert after.root_sha256 == before.root_sha256
