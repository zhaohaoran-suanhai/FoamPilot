from importlib.resources import files


def test_all_reusable_skills_are_packaged() -> None:
    root = files("foampilot").joinpath("skills")
    expected = {
        "openfoam-author-native-case",
        "openfoam-author-benchmark",
        "openfoam-buoyant-case",
        "openfoam-rhocentral-case",
    }
    assert expected <= {
        item.name
        for item in root.iterdir()
        if item.is_dir()
    }
    for name in expected:
        assert root.joinpath(name, "SKILL.md").is_file()


def test_native_skill_requires_vof_courant_headroom() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-author-native-case", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "strictly below" in text
