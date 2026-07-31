"""Strict, auditable capability-routing contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RouteEvidence(StrictModel):
    source: str = Field(min_length=1)
    fact: str = Field(min_length=1)


class RouteSuggestion(StrictModel):
    """A model may suggest a candidate, but never its confidence."""

    candidate: str | None = None
    evidence: list[RouteEvidence] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class CapabilityProfile(StrictModel):
    schema_version: Literal[1] = 1
    physics_family: str
    regime: Literal["steady", "transient", "unknown"]
    compressibility: Literal[
        "incompressible",
        "compressible",
        "unknown",
    ]
    phase_family: Literal[
        "single_phase",
        "vof",
        "multiphase",
        "unknown",
    ]
    energy: Literal["enabled", "disabled", "unknown"]
    turbulence: Literal["laminar", "rans", "les", "unknown"]
    solver_family: str | None = None
    solver_executable: str | None = None
    mesh_family: str
    parallel_expected: bool
    confidence: CapabilityConfidence
    evidence: list[RouteEvidence] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class RoutingError(ValueError):
    def __init__(
        self,
        code: Literal["ROUTING_UNRESOLVED", "REQUEST_INCOMPLETE"],
        profile: CapabilityProfile,
        *,
        model_route_used: bool = False,
    ) -> None:
        super().__init__(f"{code}: {'; '.join(profile.unresolved_questions)}")
        self.code = code
        self.profile = profile
        self.model_route_used = model_route_used
