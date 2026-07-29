from pathlib import Path

from foampilot.cli.main import build_parser


def test_distribution_uses_only_foampilot_names() -> None:
    root = Path(__file__).resolve().parents[1]
    scanned = [
        root / "pyproject.toml",
        *sorted((root / "src").rglob("*.py")),
        *sorted((root / "tests").rglob("*.py")),
    ]
    forbidden = (
        "openfoam-agent-kit",
        "openfoam_agent_kit",
        ".ofkit",
    )
    for path in scanned:
        if path.name in {Path(__file__).name, "test_standalone_boundary.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), path


def test_cli_program_name_is_foampilot() -> None:
    assert build_parser().prog == "foampilot"
