"""Optional desktop application boundary.

Importing :mod:`foampilot.desktop` never imports Qt.  Desktop dependencies
are loaded only by the explicit desktop entry point.
"""

class DesktopDependencyError(RuntimeError):
    """An optional desktop dependency is unavailable."""


__all__ = ["DesktopDependencyError"]
