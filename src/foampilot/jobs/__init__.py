"""Durable single-machine job supervision contracts."""

from .identity import (
    current_process_identity,
    process_identity,
    process_identity_matches,
)
from .models import (
    CancelRequest,
    JobOperation,
    JobSpec,
    JobState,
    JobStatus,
    ProcessIdentity,
)
from .store import LocalJobStore, build_job_spec

__all__ = [
    "CancelRequest",
    "JobOperation",
    "JobSpec",
    "JobState",
    "JobStatus",
    "LocalJobStore",
    "ProcessIdentity",
    "build_job_spec",
    "current_process_identity",
    "process_identity",
    "process_identity_matches",
]
