from __future__ import annotations

import tomllib
from pathlib import Path

import foampilot


ROOT = Path(__file__).resolve().parents[1]


def test_package_version_uses_foampilot_module_as_single_source() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "foampilot.__version__"
    }


def test_release_version_is_0_2_0() -> None:
    assert foampilot.__version__ == "0.2.0"
