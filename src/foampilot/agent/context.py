"""Canonical capability-routed Agent context entrypoint."""

from __future__ import annotations

from pathlib import Path

from foampilot.context import AgentContext, assemble_agent_context
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec
from foampilot.preprocessing import GeometryFacts


def load_agent_context(
    task: TaskSpec,
    capability: CapabilityProfile,
    *,
    package_root: str | Path | None = None,
    repair: bool = False,
    repair_evidence: str = "",
    geometry_facts: GeometryFacts | None = None,
    payload_limit_bytes: int = 32 * 1024,
) -> AgentContext:
    return assemble_agent_context(
        task,
        capability,
        package_root=package_root,
        repair=repair,
        repair_evidence=repair_evidence,
        geometry_facts=geometry_facts,
        payload_limit_bytes=payload_limit_bytes,
    )


__all__ = ["AgentContext", "load_agent_context"]
