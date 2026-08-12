"""Bounded automatic repair contracts."""

from .models import (
    DesignChange,
    NumericalRepairEnvelope,
    NumericalRepairRule,
    RepairAuthorization,
    RepairCategory,
    RepairFileOperation,
    RepairPolicy,
    RepairProposal,
)

__all__ = [
    "DesignChange",
    "NumericalRepairEnvelope",
    "NumericalRepairRule",
    "RepairAuthorization",
    "RepairCategory",
    "RepairFileOperation",
    "RepairPolicy",
    "RepairProposal",
    "authorize_repair",
]


def __getattr__(name: str):
    if name == "authorize_repair":
        from .envelope import authorize_repair

        return authorize_repair
    raise AttributeError(name)
