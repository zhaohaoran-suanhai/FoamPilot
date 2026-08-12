"""Historical validation-artifact compatibility only.

Canonical runs use :mod:`foampilot.evidence.assessment` and
:mod:`foampilot.acceptance`.
"""

from .legacy import (
    LegacyFailureLayer,
    LegacyPublicValidationCheck,
    LegacyPublicValidationReport,
)

__all__ = [
    "LegacyFailureLayer",
    "LegacyPublicValidationCheck",
    "LegacyPublicValidationReport",
]
