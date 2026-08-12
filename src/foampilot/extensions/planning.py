"""Pure deterministic planning protocol for trusted extensions."""

from __future__ import annotations

from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foampilot.environment import CommandFact
from foampilot.manifests import CaseManifest
from foampilot.plans import NativeCommand
from foampilot.simulation import CaseDesign
from foampilot.tasks import OpenFOAMTarget, ResourceBudget

from .models import CapabilityDescriptor


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanContributionError(ValueError):
    pass


class PlanContext(StrictFrozenModel):
    design: CaseDesign
    manifest: CaseManifest
    target: OpenFOAMTarget
    resource_budget: ResourceBudget
    command_facts: tuple[CommandFact, ...]
    mpi_available: bool

    def design_value(self, field_path: str, default=None):
        for fact in self.design.proposal.iter_values():
            if fact.field_path == field_path:
                return fact.value
        return default

    @property
    def available_executables(self) -> frozenset[str]:
        return frozenset(item.name for item in self.command_facts)


class PlanFragment(StrictFrozenModel):
    contributor_id: str
    contributor_identity: str
    commands: tuple[NativeCommand, ...]
    required_authored_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_fragment(self) -> Self:
        step_ids = [item.step_id for item in self.commands]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan fragment step IDs must be unique")
        if any(
            item.executable in {"mpirun", "mpiexec", "orterun"}
            for item in self.commands
        ):
            raise ValueError("plan fragment must not contain an MPI launcher")
        paths = list(self.required_authored_paths)
        if len(paths) != len(set(paths)):
            raise ValueError("required authored paths must be unique")
        return self


class ComposedPlanFragments(StrictFrozenModel):
    commands: tuple[NativeCommand, ...]
    required_authored_paths: tuple[str, ...]
    contributor_identities: dict[str, str]


class PlanContributor(Protocol):
    descriptor: CapabilityDescriptor

    def contribute(self, context: PlanContext) -> PlanFragment: ...


def descriptor_identity(descriptor: CapabilityDescriptor) -> str:
    return (
        f"{descriptor.extension_version}/protocol-{descriptor.protocol_version}"
    )


def command_timeout(
    context: PlanContext,
    *,
    fraction: float,
    floor: int = 1,
) -> int:
    return max(floor, int(context.resource_budget.max_wall_seconds * fraction))


def require_context(
    context: PlanContext,
    descriptor: CapabilityDescriptor,
) -> None:
    if not descriptor.supports_target(
        context.target.distribution,
        context.target.version,
    ):
        raise PlanContributionError(
            f"PLAN_TARGET_UNSUPPORTED: {descriptor.extension_id}"
        )
    missing = sorted(
        set(descriptor.required_executables)
        - set(context.available_executables)
    )
    if missing:
        raise PlanContributionError(
            "PLAN_EXECUTABLE_UNAVAILABLE: " + ", ".join(missing)
        )
