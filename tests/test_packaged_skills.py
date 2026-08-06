from importlib.resources import files


def test_all_reusable_skills_are_packaged() -> None:
    root = files("foampilot").joinpath("skills")
    expected = {
        "openfoam-author-native-case",
        "openfoam-author-benchmark",
        "openfoam-incompressible-pressure-velocity",
        "openfoam-compressible-transient",
        "openfoam-multiphase-vof",
        "openfoam-buoyant-cht",
        "openfoam-solid-mechanics",
        "openfoam-scalar-field-transport",
        "openfoam-mesh-workflow",
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

    assert "严格低于 TaskSpec 允许的最大值" in text


def test_multiphase_skill_checks_reader_contract_before_solver() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-multiphase-vof", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "按求解器读取顺序" in text
    assert "`Dab`、`alphatab`" in text
    assert "base/Final 成对" in text
    for keyword in ("`solver`", "`smoother`", "`tolerance`", "`relTol`"):
        assert keyword in text


def test_compressible_skill_checks_matrix_and_thermo_before_tuning() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-compressible-transient", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "矩阵类型" in text
    assert "守恒显式场" in text
    assert "派生隐式场" in text
    assert "先验证初始 thermo state" in text


def test_buoyant_skill_checks_thermo_inversion_before_linear_tuning() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-buoyant-cht", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "thermo inversion" in text
    assert "先验证参考状态" in text
    assert "不得只修改 `fvSolution`" in text


def test_incompressible_skill_bounds_maxwell_repair() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-incompressible-pressure-velocity", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Maxwell" in text
    assert "actual Courant" in text
    assert "stress residual" in text
    assert "不得违反 TaskSpec" in text
