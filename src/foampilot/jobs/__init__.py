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
    RecoveryAction,
    RecoveryDecision,
    RecoveryState,
)
from .recovery import reconcile_job, terminate_orphan
from .store import LocalJobStore, build_job_spec
from .worker import launch_local_job, run_local_job

__all__ = [
    "CancelRequest",
    "JobOperation",
    "JobSpec",
    "JobState",
    "JobStatus",
    "LocalJobStore",
    "ProcessIdentity",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryState",
    "build_job_spec",
    "current_process_identity",
    "launch_local_job",
    "process_identity",
    "process_identity_matches",
    "reconcile_job",
    "run_local_job",
    "terminate_orphan",
]
