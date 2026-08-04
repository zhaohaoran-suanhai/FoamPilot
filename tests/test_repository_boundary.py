from __future__ import annotations

from pathlib import Path

from foampilot.cli.main import COMMANDS, build_parser, main


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_COMMANDS = (
    "validate",
    "plan",
    "solve",
    "resume",
    "inspect",
    "report",
    "preflight",
    "model",
    "knowledge",
    "skill",
    "audit",
    "qualify",
    "improve",
)


def test_repository_has_no_functional_legacy_references() -> None:
    excluded_parts = {
        ".git",
        ".pytest_cache",
        "dist",
        "build",
        "__pycache__",
    }
    excluded_files = {
        "LICENSE",
        "NOTICE.md",
        Path(__file__).name,
    }
    local_plans = ROOT / "docs" / "superpowers"
    forbidden = (
        "/home/edwin/workplace/Foam-Agent",
        "packages/foampilot",
        "openfoam-agent-kit",
        "openfoam_agent_kit",
        "foambench_main.py",
        "src.services",
        ".ofkit",
    )

    violations: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if excluded_parts.intersection(path.parts):
            continue
        if path.is_relative_to(local_plans) or path.name in excluded_files:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        matches = [token for token in forbidden if token in text]
        if matches:
            violations.append(f"{path.relative_to(ROOT)}: {matches}")

    assert violations == []


def test_cli_exposes_the_foampilot_command_surface(capsys) -> None:
    assert build_parser().prog == "foampilot"
    assert COMMANDS == SUPPORTED_COMMANDS
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "FoamPilot" in output
    assert "preflight" in output
    assert "casespec" not in output
    assert "agent" not in output
