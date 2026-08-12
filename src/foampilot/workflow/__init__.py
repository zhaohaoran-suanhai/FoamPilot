"""Public workflow state and persistence boundary."""

from .events import WorkflowEvent
from .models import (
    FailureDomain,
    FailureRecord,
    ParentRun,
    ResumeCompatibility,
    ResumeCompatibilityError,
    ResumeMetadata,
    WorkflowEventState,
    WorkflowStage,
    WorkflowState,
)
from .store import WorkflowStore
from .coordinator import (
    WorkflowCoordinator,
    WorkflowCoordinatorOutcome,
    WorkflowCoordinatorSummary,
)
from .services import (
    ArtifactSink,
    CANONICAL_STAGE_DESCRIPTORS,
    CANONICAL_STAGE_ORDER,
    StageDescriptor,
    StageOutcome,
    StageService,
    WorkflowContext,
)


def __getattr__(name: str):
    if name in {
        "ConfirmationAnswer",
        "ConfirmationAnswers",
        "ConfirmationContinuation",
        "ConfirmationError",
        "ConfirmationParent",
        "apply_confirmation_records",
        "load_confirmation_parent",
        "parse_answers",
        "persist_confirmation_continuation",
    }:
        from .confirmation import (
            ConfirmationAnswer,
            ConfirmationAnswers,
            ConfirmationContinuation,
            ConfirmationError,
            ConfirmationParent,
            apply_confirmation_records,
            load_confirmation_parent,
            parse_answers,
            persist_confirmation_continuation,
        )

        return locals()[name]
    raise AttributeError(name)

__all__ = [
    "FailureDomain",
    "FailureRecord",
    "ParentRun",
    "ResumeCompatibility",
    "ResumeCompatibilityError",
    "ResumeMetadata",
    "WorkflowEvent",
    "WorkflowEventState",
    "WorkflowStage",
    "WorkflowState",
    "WorkflowStore",
    "ArtifactSink",
    "CANONICAL_STAGE_DESCRIPTORS",
    "CANONICAL_STAGE_ORDER",
    "StageDescriptor",
    "StageOutcome",
    "StageService",
    "WorkflowContext",
    "WorkflowCoordinator",
    "WorkflowCoordinatorOutcome",
    "WorkflowCoordinatorSummary",
    "ConfirmationAnswer",
    "ConfirmationAnswers",
    "ConfirmationContinuation",
    "ConfirmationError",
    "ConfirmationParent",
    "apply_confirmation_records",
    "load_confirmation_parent",
    "parse_answers",
    "persist_confirmation_continuation",
]
