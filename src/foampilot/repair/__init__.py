"""Bounded automatic repair contracts."""

from .models import (
    DesignChange,
    DerivedCaseDesignRecord,
    NumericalRepairEnvelope,
    NumericalRepairRule,
    RepairAuthorization,
    RepairCategory,
    RepairChangeSet,
    RepairDecision,
    RepairFileOperation,
    RepairPolicy,
    RepairProposal,
)

__all__ = [
    "DesignChange",
    "DerivedCaseDesignRecord",
    "NumericalRepairEnvelope",
    "NumericalRepairRule",
    "RepairAuthorization",
    "RepairCategory",
    "RepairChangeSet",
    "RepairDecision",
    "RepairFileOperation",
    "RepairPolicy",
    "RepairProposal",
    "authorize_repair",
    "AuthorizedRepairResult",
    "apply_authorized_repair",
    "coordinate_repair",
]


def __getattr__(name: str):
    if name == "authorize_repair":
        from .envelope import authorize_repair

        return authorize_repair
    if name in {
        "AuthorizedRepairResult",
        "apply_authorized_repair",
        "coordinate_repair",
    }:
        from .coordinator import (
            AuthorizedRepairResult,
            apply_authorized_repair,
            coordinate_repair,
        )

        return {
            "AuthorizedRepairResult": AuthorizedRepairResult,
            "apply_authorized_repair": apply_authorized_repair,
            "coordinate_repair": coordinate_repair,
        }[name]
    raise AttributeError(name)
