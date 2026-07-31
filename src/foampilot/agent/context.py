"""Canonical capability-routed Agent context entrypoint."""

from __future__ import annotations

from pathlib import Path

from foampilot.context import AgentContext, assemble_agent_context
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec


def load_agent_context(
    task: TaskSpec,
    capability: CapabilityProfile,
    *,
    package_root: str | Path | None = None,
    repair: bool = False,
    payload_limit_bytes: int = 32 * 1024,
) -> AgentContext:
    return assemble_agent_context(
        task,
        capability,
        package_root=package_root,
        repair=repair,
        payload_limit_bytes=payload_limit_bytes,
    )


__all__ = ["AgentContext", "load_agent_context"]
