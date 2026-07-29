from pathlib import Path


def test_root_documents_define_the_foampilot_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "# FoamPilot" in readme
    assert "foampilot solve" in readme
    assert "Foundation OpenFOAM v10" in readme
    assert "foampilot solve" in agents
    assert "Do not read or copy the target tutorial" in agents
    assert "Do not commit or push unless the user explicitly asks" in agents


def test_repository_ignores_runtime_artifacts() -> None:
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".foampilot/", ".pytest_cache/", "dist/", "build/"):
        assert pattern in ignore
