from __future__ import annotations

import importlib
from pathlib import Path

from foampilot.desktop import DesktopDependencyError


cli = importlib.import_module("foampilot.cli.main")


def test_desktop_command_forwards_explicit_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    seen: list[tuple[Path | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        cli,
        "_desktop_launcher",
        lambda path, runtime_args: seen.append((path, runtime_args)) or 0,
    )

    assert cli.main(["desktop", "--open-run", str(tmp_path)]) == 0
    assert seen == [(tmp_path, ())]


def test_desktop_command_forwards_only_explicit_runtime_options(
    monkeypatch,
) -> None:
    seen: list[tuple[Path | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        cli,
        "_desktop_launcher",
        lambda path, runtime_args: seen.append((path, runtime_args)) or 0,
    )

    assert (
        cli.main(
            [
                "desktop",
                "--runtime-config",
                "/tmp/runtime.toml",
                "--execution-isolation",
                "sandbox_required",
                "--max-mpi-ranks",
                "3",
                "--trusted-readonly-root",
                "/opt/solvers",
            ]
        )
        == 0
    )

    assert seen == [
        (
            None,
            (
                "--runtime-config",
                "/tmp/runtime.toml",
                "--execution-isolation",
                "sandbox_required",
                "--max-mpi-ranks",
                "3",
                "--trusted-readonly-root",
                "/opt/solvers",
            ),
        )
    ]


def test_desktop_command_reports_missing_optional_dependency(
    monkeypatch,
    capsys,
) -> None:
    def unavailable(
        path: Path | None,
        runtime_args: tuple[str, ...],
    ) -> int:
        del runtime_args
        raise DesktopDependencyError("PySide6 is not installed")

    monkeypatch.setattr(cli, "_desktop_launcher", unavailable)

    assert cli.main(["desktop"]) == 3
    captured = capsys.readouterr()
    assert "DESKTOP_DEPENDENCY_MISSING" in captured.err
    assert "请安装 foampilot[desktop]" in captured.err


def test_desktop_package_does_not_import_qt() -> None:
    desktop = importlib.import_module("foampilot.desktop")

    assert not hasattr(desktop, "QApplication")
