"""Deterministic registry for trusted first-party capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import CapabilityDescriptor


class TargetLike(Protocol):
    distribution: str
    version: str


class CapabilityRegistrationError(ValueError):
    pass


class CapabilityResolutionError(LookupError):
    pass


@dataclass(frozen=True)
class _Registration:
    descriptor: CapabilityDescriptor
    provider: object


class CapabilityRegistry:
    """Closed registry; dynamic Python entry points are intentionally disabled."""

    def __init__(self, *, entry_points_enabled: bool = False) -> None:
        if entry_points_enabled:
            raise CapabilityRegistrationError(
                "ENTRY_POINTS_DISABLED: third-party entry points are not trusted"
            )
        self._registrations: dict[str, _Registration] = {}
        self.entry_points_enabled = False

    @classmethod
    def first_party(cls) -> "CapabilityRegistry":
        registry = cls(entry_points_enabled=False)
        from foampilot.assets.openfoam_mesh import OpenFOAMPolyMeshAdapter
        from foampilot.assets.public_file import PublicFileAdapter

        for adapter in (PublicFileAdapter(), OpenFOAMPolyMeshAdapter()):
            registry.register(adapter.descriptor, adapter)
        return registry

    @classmethod
    def planning_first_party(cls) -> "CapabilityRegistry":
        """Build the closed set of command-authority contributors."""

        from .mesh import BlockMeshPlanContributor, ProvidedMeshPlanContributor
        from .solver import (
            Foundation10ParallelSolverPlanContributor,
            Foundation10SerialSolverPlanContributor,
        )

        registry = cls(entry_points_enabled=False)
        for contributor in (
            ProvidedMeshPlanContributor(),
            BlockMeshPlanContributor(),
            Foundation10SerialSolverPlanContributor(),
            Foundation10ParallelSolverPlanContributor(),
        ):
            registry.register(contributor.descriptor, contributor)
        return registry

    def register(
        self,
        descriptor: CapabilityDescriptor,
        provider: object,
    ) -> None:
        if descriptor.extension_id in self._registrations:
            raise CapabilityRegistrationError(
                f"DUPLICATE_EXTENSION_ID: {descriptor.extension_id}"
            )
        self._registrations[descriptor.extension_id] = _Registration(
            descriptor=descriptor,
            provider=provider,
        )

    def extension_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def descriptor(self, extension_id: str) -> CapabilityDescriptor:
        try:
            return self._registrations[extension_id].descriptor
        except KeyError as error:
            raise CapabilityResolutionError(
                f"CAPABILITY_EXTENSION_UNKNOWN: {extension_id}"
            ) from error

    def provider(self, extension_id: str) -> object:
        try:
            return self._registrations[extension_id].provider
        except KeyError as error:
            raise CapabilityResolutionError(
                f"CAPABILITY_EXTENSION_UNKNOWN: {extension_id}"
            ) from error

    def plan_for(self, context):
        """Compose frozen-design planning contributors, never execute them."""

        from .planning import ComposedPlanFragments, PlanContributionError

        fragments = []
        selected_ids = set(context.design.extension_identities)
        for extension_id in sorted(selected_ids):
            descriptor = self.descriptor(extension_id)
            incompatible = sorted(
                selected_ids & set(descriptor.incompatible_extensions)
            )
            if incompatible:
                raise PlanContributionError(
                    "PLAN_CONTRIBUTORS_INCOMPATIBLE: "
                    + ", ".join((extension_id, *incompatible))
                )
            provider = self.provider(extension_id)
            if not hasattr(provider, "contribute"):
                continue
            fragment = provider.contribute(context)
            if fragment.contributor_id != extension_id:
                raise PlanContributionError(
                    "PLAN_CONTRIBUTOR_ID_MISMATCH: " + extension_id
                )
            expected = context.design.extension_identities[extension_id]
            if fragment.contributor_identity != expected:
                raise PlanContributionError(
                    "PLAN_CONTRIBUTOR_IDENTITY_MISMATCH: " + extension_id
                )
            fragments.append(fragment)
        fragments.sort(key=lambda item: item.contributor_id)
        commands = tuple(
            command
            for fragment in fragments
            for command in fragment.commands
        )
        step_ids = [item.step_id for item in commands]
        if len(step_ids) != len(set(step_ids)):
            raise PlanContributionError("PLAN_DUPLICATE_STEP_ID")
        if (
            sum(item.timeout_seconds for item in commands)
            > context.resource_budget.max_wall_seconds
        ):
            raise PlanContributionError("PLAN_TIMEOUT_BUDGET_EXCEEDED")
        paths = tuple(
            path
            for fragment in fragments
            for path in fragment.required_authored_paths
        )
        if len(paths) != len(set(paths)):
            raise PlanContributionError("PLAN_DUPLICATE_REQUIRED_PATH")
        if not commands:
            raise PlanContributionError("PLAN_NO_COMMAND_CONTRIBUTORS")
        return ComposedPlanFragments(
            commands=commands,
            required_authored_paths=paths,
            contributor_identities={
                fragment.contributor_id: fragment.contributor_identity
                for fragment in fragments
            },
        )

    def resolve(self, kind: str, target: TargetLike) -> object:
        kind_matches = [
            item
            for item in self._registrations.values()
            if kind in item.descriptor.capability_kinds
        ]
        matches = [
            item
            for item in kind_matches
            if item.descriptor.supports_target(
                target.distribution,
                target.version,
            )
        ]
        if len(matches) > 1:
            identities = ", ".join(
                sorted(item.descriptor.extension_id for item in matches)
            )
            raise CapabilityResolutionError(
                f"CAPABILITY_AMBIGUOUS: {kind}: {identities}"
            )
        if len(matches) == 1:
            return matches[0].provider
        if kind_matches:
            raise CapabilityResolutionError(
                "CAPABILITY_TARGET_UNSUPPORTED: "
                f"{kind} for {target.distribution} {target.version}"
            )
        raise CapabilityResolutionError(f"CAPABILITY_UNAVAILABLE: {kind}")


__all__ = [
    "CapabilityRegistrationError",
    "CapabilityRegistry",
    "CapabilityResolutionError",
]
