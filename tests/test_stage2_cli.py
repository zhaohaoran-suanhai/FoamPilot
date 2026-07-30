from __future__ import annotations

import json
import shutil
from pathlib import Path

from foampilot.cli.main import main


PROJECT = Path(__file__).parents[1]
CORPUS = PROJECT / "src/foampilot/knowledge/openfoam10"
MANIFEST = PROJECT / "src/foampilot/knowledge/knowledge-manifest.json"
SCENARIOS = PROJECT / "src/foampilot/skills/scenarios.yaml"
SKILLS = PROJECT / "src/foampilot/skills"
NATIVE_SKILL = (
    PROJECT / "src/foampilot/skills/openfoam-author-native-case"
)


def test_root_help_lists_knowledge_and_skill_commands(capsys) -> None:
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "knowledge" in output
    assert "skill" in output


def test_knowledge_validate_verifies_entries_and_frozen_manifest(capsys) -> None:
    assert main(["knowledge", "validate", str(CORPUS), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "entry_count": 28,
        "issues": [],
        "manifest": str(MANIFEST),
        "status": "PASS",
    }


def test_knowledge_validate_reports_manifest_drift(
    tmp_path: Path, capsys
) -> None:
    knowledge = tmp_path / "knowledge"
    copied = knowledge / "openfoam10"
    shutil.copytree(CORPUS, copied)
    shutil.copy2(MANIFEST, knowledge / "knowledge-manifest.json")
    entry = next(copied.rglob("*.yaml"))
    entry.write_text(
        entry.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    assert main(["knowledge", "validate", str(copied), "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL_KNOWLEDGE_VALIDATION"
    assert len(payload["issues"]) == 1
    assert payload["issues"][0].startswith("hash mismatch:")


def test_knowledge_search_applies_formal_leakage_gate(
    capsys,
) -> None:
    base = [
        "knowledge",
        "search",
        str(CORPUS),
        "pilot physics golden validation gate",
        "--family",
        "new-holdout-family",
        "--json",
    ]
    assert main([*base[:-1], "--formal", "--json"]) == 0
    formal = json.loads(capsys.readouterr().out)
    assert formal["status"] == "PASS"
    assert all(
        match["visibility"] == "public" for match in formal["matches"]
    )

def test_knowledge_search_supports_solver_type_and_limit(capsys) -> None:
    assert (
        main(
            [
                "knowledge",
                "search",
                str(CORPUS),
                "icoFoam PISO closed pressure reference",
                "--solver",
                "icoFoam",
                "--type",
                "numerics",
                "--limit",
                "1",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"]["solver"] == "icoFoam"
    assert payload["query"]["knowledge_types"] == ["numerics"]
    assert len(payload["matches"]) == 1
    assert (
        payload["matches"][0]["entry_id"]
        == "of10.numerics.piso-closed-pressure-reference"
    )


def test_skill_validate_uses_explicit_scenario_suite(capsys) -> None:
    skill = SKILLS / "openfoam-author-benchmark"
    assert (
        main(
            [
                "skill",
                "validate",
                str(skill),
                "--scenarios",
                str(SCENARIOS),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "issues": [],
        "skill_name": "openfoam-author-benchmark",
        "status": "PASS",
    }


def test_packaged_native_skill_uses_its_own_scenarios_by_default(
    capsys,
) -> None:
    assert (
        main(["skill", "validate", str(NATIVE_SKILL), "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "issues": [],
        "skill_name": "openfoam-author-native-case",
        "status": "PASS",
    }


def test_skill_validate_returns_nonzero_for_invalid_skill(
    tmp_path: Path, capsys
) -> None:
    copied = tmp_path / "openfoam-author-benchmark"
    shutil.copytree(SKILLS / "openfoam-author-benchmark", copied)
    (copied / "agents" / "openai.yaml").unlink()

    assert (
        main(
            [
                "skill",
                "validate",
                str(copied),
                "--scenarios",
                str(SCENARIOS),
                "--json",
            ]
        )
        == 4
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL_SKILL_VALIDATION"
    assert payload["issues"][0]["code"] == "openai_metadata"
