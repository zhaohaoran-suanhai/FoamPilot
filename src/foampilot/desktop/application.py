"""PySide6 application entry point, imported only on explicit request."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication


_ACTIVE_WINDOWS: set[object] = set()


def launch(
    run_dir: Path | None = None,
    runtime_cli_args: tuple[str, ...] = (),
) -> int:
    """Start the optional desktop application."""

    from .main_window import FoamPilotMainWindow

    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(sys.argv[:1])
    application.setOrganizationName("FoamPilot")
    application.setApplicationName("FoamPilot")
    window = FoamPilotMainWindow(
        settings=QSettings(),
        runtime_cli_args=runtime_cli_args,
    )
    if run_dir is not None:
        window.open_run(run_dir)
    else:
        window.restore_last_run()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    _ACTIVE_WINDOWS.add(window)
    window.destroyed.connect(
        lambda _object=None, reference=window: _ACTIVE_WINDOWS.discard(
            reference
        )
    )
    window.show()
    if not owns_application:
        return 0
    return int(application.exec())
