from __future__ import annotations

import importlib
from pathlib import Path

from foampilot.desktop import DesktopDependencyError


cli = importlib.import_module("foampilot.cli.main")


def test_desktop_command_forwards_explicit_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    seen: list[Path | None] = []
    monkeypatch.setattr(
        cli,
        "_desktop_launcher",
        lambda path: seen.append(path) or 0,
    )

    assert cli.main(["desktop", "--open-run", str(tmp_path)]) == 0
    assert seen == [tmp_path]


def test_desktop_command_reports_missing_optional_dependency(
    monkeypatch,
    capsys,
) -> None:
    def unavailable(path: Path | None) -> int:
        raise DesktopDependencyError("PySide6 is not installed")

    monkeypatch.setattr(cli, "_desktop_launcher", unavailable)

    assert cli.main(["desktop"]) == 3
    captured = capsys.readouterr()
    assert "DESKTOP_DEPENDENCY_MISSING" in captured.err
    assert "请安装 foampilot[desktop]" in captured.err


def test_desktop_package_does_not_import_qt() -> None:
    desktop = importlib.import_module("foampilot.desktop")

    assert not hasattr(desktop, "QApplication")
