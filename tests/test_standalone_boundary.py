from pathlib import Path


def test_no_functional_source_worktree_or_legacy_references() -> None:
    root = Path(__file__).resolve().parents[1]
    local_plans = root / "docs" / "superpowers"
    excluded_parts = {
        ".git",
        ".pytest_cache",
        "dist",
        "build",
        "__pycache__",
    }
    forbidden = (
        "/home/edwin/workplace/Foam-Agent",
        "packages/foampilot",
        "openfoam-agent-kit",
        "openfoam_agent_kit",
        "foambench_main.py",
        "src.services",
        ".ofkit",
    )
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if excluded_parts.intersection(path.parts):
            continue
        if path.is_relative_to(local_plans):
            continue
        if path.name in {
            "LICENSE",
            "NOTICE.md",
            "test_brand_contract.py",
            Path(__file__).name,
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not any(token in text for token in forbidden), path
