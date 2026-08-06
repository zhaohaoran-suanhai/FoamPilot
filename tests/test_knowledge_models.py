from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from foampilot.knowledge import (
    KnowledgeEntry,
    build_knowledge_manifest,
    knowledge_entry_json_schema,
    load_knowledge_corpus,
    load_knowledge_entry,
    verify_knowledge_manifest,
)


PROJECT = Path(__file__).parents[1]
SCHEMA = PROJECT / "schemas" / "knowledge-entry-v1.schema.json"
SOLVER_GUIDES = PROJECT / "src" / "foampilot" / "knowledge" / "openfoam10" / "solver-guides"


def _payload(entry_id: str = "of10.numerics.pressure-reference") -> dict:
    return {
        "schema_version": "1.0.0",
        "id": entry_id,
        "title": "Closed pressure reference",
        "fork": "foundation",
        "version": "10",
        "knowledge_type": "numerics",
        "solvers": ["icoFoam"],
        "models": ["PISO"],
        "tags": ["pressure", "piso"],
        "applicability": {
            "conditions": ["All pressure boundaries are Neumann-like."],
            "not_applicable": ["A fixed pressure boundary anchors pressure."],
        },
        "source": {
            "kind": "official_source",
            "title": "Foundation v10 pressure reference implementation",
            "locator": "src/finiteVolume/cfdTools/general/findRefCell",
            "sha256": "a" * 64,
            "license_spdx": "GPL-3.0-or-later",
        },
        "leakage": {
            "visibility": "public",
            "families": [],
            "contains_target_case_solution": False,
        },
        "content": {
            "summary": "Closed incompressible pressure needs a reference.",
            "rules": ["Set one pressure reference cell and value."],
            "failure_signals": ["Unable to set reference cell for field p"],
            "validation": ["Solver reaches the requested end time."],
        },
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_strict_entry_and_checked_in_schema_agree() -> None:
    entry = KnowledgeEntry.model_validate(_payload())
    assert entry.id == "of10.numerics.pressure-reference"
    assert entry.source.sha256 == "a" * 64
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == (
        knowledge_entry_json_schema()
    )
    assert knowledge_entry_json_schema()["additionalProperties"] is False


def test_unknown_fields_and_bad_source_hash_are_rejected() -> None:
    payload = _payload()
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="unexpected"):
        KnowledgeEntry.model_validate(payload)
    payload = _payload()
    payload["source"]["sha256"] = "not-a-hash"
    with pytest.raises(ValidationError, match="sha256"):
        KnowledgeEntry.model_validate(payload)


def test_pilot_knowledge_must_be_development_only_and_family_scoped() -> None:
    payload = _payload()
    payload["source"]["kind"] = "pilot_derived"
    with pytest.raises(ValidationError, match="development_only"):
        KnowledgeEntry.model_validate(payload)

    payload["leakage"]["visibility"] = "development_only"
    with pytest.raises(ValidationError, match="pilot leakage family"):
        KnowledgeEntry.model_validate(payload)

    payload["leakage"]["families"] = ["laminar-cavity"]
    assert KnowledgeEntry.model_validate(payload).source.kind == "pilot_derived"


def test_public_entry_cannot_claim_pilot_family_or_target_solution() -> None:
    payload = _payload()
    payload["leakage"]["families"] = ["laminar-cavity"]
    with pytest.raises(ValidationError, match="public entry"):
        KnowledgeEntry.model_validate(payload)
    payload = _payload()
    payload["leakage"]["contains_target_case_solution"] = True
    with pytest.raises(ValidationError, match="False"):
        KnowledgeEntry.model_validate(payload)


def test_corpus_rejects_duplicate_ids_and_manifest_detects_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    _write(root / "first.yaml", _payload())
    _write(root / "second.yaml", _payload())
    with pytest.raises(ValueError, match="duplicate knowledge ID"):
        load_knowledge_corpus(root)

    (root / "second.yaml").unlink()
    entry = load_knowledge_entry(root / "first.yaml")
    assert entry.title == "Closed pressure reference"
    manifest = build_knowledge_manifest(root)
    assert manifest["entry_count"] == 1
    assert verify_knowledge_manifest(root, manifest) == []
    (root / "first.yaml").write_text(
        (root / "first.yaml").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    assert verify_knowledge_manifest(root, manifest) == [
        "hash mismatch: first.yaml"
    ]


@pytest.mark.parametrize(
    ("filename", "required_markers"),
    [
        (
            "driftfluxfoam-contract.yaml",
            (
                "Vc",
                "[0 0 1 0 0 0 0]",
                "nLimiterIter",
                "physicalProperties.<dispersed>",
                "viscosityModel slurry",
            ),
        ),
        (
            "multiphaseeulerfoam-contract.yaml",
            (
                "div(phi,alpha.<phase>)",
                "escaped",
                "div\\(phi,alpha.*\\)",
                "thermo:rho.<phase>",
                "div((((alpha.<phase>*thermo:rho.<phase>)*nuEff.<phase>)*dev2(T(grad(U.<phase>)))))",
            ),
        ),
        (
            "reactingfoam-contract.yaml",
            (
                "#include \"reactions\"",
                "reactions 子字典",
                "method 默认",
                "YiFinal",
            ),
        ),
        (
            "compressibleinterfoam-contract.yaml",
            (
                "alpha.<phase>Final",
                "nuEff.<phase>",
                "dev2(T(grad(U)))",
                "顶层 sigma",
            ),
        ),
    ],
)
def test_solver_guides_cover_atomic_reader_contracts(
    filename: str,
    required_markers: tuple[str, ...],
) -> None:
    text = (SOLVER_GUIDES / filename).read_text(encoding="utf-8")

    for marker in required_markers:
        assert marker in text
