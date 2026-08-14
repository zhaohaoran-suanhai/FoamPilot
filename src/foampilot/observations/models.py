"""Frozen contracts for requested CFD observations and evidence collection."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from foampilot.simulation.provenance import FactEvidence


ObservationKind = Literal[
    "residual",
    "continuity",
    "flow_rate",
    "pressure_difference",
    "region_average",
    "force",
    "heat_flux",
]
_OPENFOAM_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_QUANTITY_DESCRIPTION = (
    "Machine identifier in lower_snake_case; use the exact canonical quantity "
    "for this observation kind from AvailableObservationContracts."
)
EvidenceStrategyKind = Literal[
    "run_facts",
    "written_field",
    "postprocess_command",
    "runtime_configuration",
    "unavailable",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ObservationScope(_StrictFrozenModel):
    kind: Literal["global", "patch", "patch_pair", "cell_zone", "region"]
    names: tuple[str, ...] = ()
    region: str | None = None

    @field_validator("names")
    @classmethod
    def validate_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("scope names must be unique")
        if any(_OPENFOAM_WORD.fullmatch(value) is None for value in values):
            raise ValueError("scope names must be safe OpenFOAM identifiers")
        return values

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str | None) -> str | None:
        if value is not None and _OPENFOAM_WORD.fullmatch(value) is None:
            raise ValueError("region must be a safe OpenFOAM identifier")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        required = {
            "global": 0,
            "patch": 1,
            "patch_pair": 2,
            "cell_zone": 1,
            "region": 1,
        }[self.kind]
        if len(self.names) != required:
            raise ValueError(f"{self.kind} scope requires {required} names")
        if self.kind == "region":
            if self.region is None or self.region != self.names[0]:
                raise ValueError(
                    "region scope requires a matching explicit region binding"
                )
        return self


class TimeSelection(_StrictFrozenModel):
    kind: Literal["latest", "final", "history", "time_range"]
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.kind == "time_range":
            if self.start is None or self.end is None or self.end < self.start:
                raise ValueError("time_range requires ordered start and end")
        elif self.start is not None or self.end is not None:
            raise ValueError("only time_range accepts start and end")
        return self


class EvidenceStrategy(_StrictFrozenModel):
    kind: EvidenceStrategyKind
    collector_id: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_strategy(self) -> Self:
        if self.kind in {"postprocess_command", "runtime_configuration"}:
            if not self.collector_id:
                raise ValueError("collection strategy requires collector_id")
        if self.kind == "unavailable" and not self.reason:
            raise ValueError("unavailable strategy requires reason")
        return self


class ObservationItem(_StrictFrozenModel):
    observation_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")
    kind: ObservationKind
    quantity: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description=_QUANTITY_DESCRIPTION,
    )
    dimension: str = Field(min_length=1)
    scope: ObservationScope
    time_selection: TimeSelection
    evidence_strategy: EvidenceStrategy
    required_for_condition_ids: tuple[str, ...] = ()
    provenance: tuple[FactEvidence, ...] = Field(min_length=1)

    @field_validator("required_for_condition_ids")
    @classmethod
    def validate_condition_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("condition IDs must be unique")
        return values


class ObservationRequest(_StrictFrozenModel):
    """Intent-level observable with no collection or command authority."""

    observation_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")
    kind: ObservationKind
    quantity: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description=_QUANTITY_DESCRIPTION,
    )
    dimension: str = Field(min_length=1)
    scope: ObservationScope
    time_selection: TimeSelection
    provenance: tuple[FactEvidence, ...] = Field(min_length=1)

    def has_same_semantics_as(self, other: Self) -> bool:
        """Compare executable meaning while excluding audit provenance."""

        return self.model_dump(exclude={"provenance"}) == other.model_dump(
            exclude={"provenance"}
        )

    def merge_equivalent_provenance(self, other: Self) -> Self:
        """Preserve all evidence for two semantically identical requests."""

        if not self.has_same_semantics_as(other):
            raise ValueError("observation requests have different semantics")
        provenance = list(self.provenance)
        provenance.extend(
            item for item in other.provenance if item not in provenance
        )
        return self.model_copy(update={"provenance": tuple(provenance)})


def merge_compatible_observation_requests(
    previous: ObservationRequest,
    requested: ObservationRequest,
) -> ObservationRequest | None:
    """Merge identical requests or let history cover the same timed request."""

    if previous.has_same_semantics_as(requested):
        return previous.merge_equivalent_provenance(requested)
    previous_core = previous.model_dump(
        exclude={"provenance", "time_selection"}
    )
    requested_core = requested.model_dump(
        exclude={"provenance", "time_selection"}
    )
    if previous_core != requested_core:
        return None
    if "history" not in {
        previous.time_selection.kind,
        requested.time_selection.kind,
    }:
        return None
    broader = (
        previous
        if previous.time_selection.kind == "history"
        else requested
    )
    provenance = list(previous.provenance)
    provenance.extend(
        item for item in requested.provenance if item not in provenance
    )
    return broader.model_copy(update={"provenance": tuple(provenance)})


class ObservationWarning(_StrictFrozenModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    observation_id: str | None = None
    detail: str = Field(min_length=1)
    recovery: str = Field(min_length=1)


class ObservationPlan(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    items: tuple[ObservationItem, ...]
    warnings: tuple[ObservationWarning, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        ids = [item.observation_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("observation IDs must be unique")
        return self

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(payload).hexdigest()


__all__ = [
    "EvidenceStrategy",
    "EvidenceStrategyKind",
    "ObservationItem",
    "ObservationKind",
    "ObservationPlan",
    "ObservationRequest",
    "ObservationScope",
    "ObservationWarning",
    "TimeSelection",
    "merge_compatible_observation_requests",
]
