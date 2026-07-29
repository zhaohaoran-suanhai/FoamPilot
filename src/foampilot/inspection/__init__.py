"""Generic native OpenFOAM case inspection."""

from .models import InspectionIssue, InspectionReport
from .native_case import inspect_native_case

__all__ = [
    "InspectionIssue",
    "InspectionReport",
    "inspect_native_case",
]
