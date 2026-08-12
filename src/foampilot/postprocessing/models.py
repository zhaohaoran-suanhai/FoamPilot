"""Derived public CFD metric contracts with evidence provenance."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foampilot.observations import ObservationScope


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MetricSample(_StrictFrozenModel):
    time: float | None = Field(default=None, ge=0)
    value: float | tuple[float, ...]
    unit: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class MetricSeries(_StrictFrozenModel):
    observation_id: str
    quantity: str
    dimension: str
    scope: ObservationScope
    status: Literal["AVAILABLE", "UNAVAILABLE", "PARTIAL"]
    samples: tuple[MetricSample, ...] = ()
    detail: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "AVAILABLE" and not self.samples:
            raise ValueError("available metric requires samples")
        if self.status == "UNAVAILABLE" and not self.detail:
            raise ValueError("unavailable metric requires detail")
        if len(self.samples) > 1000:
            raise ValueError("metric series exceeds bounded projection")
        return self


class DerivedMetrics(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    run_facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    series: tuple[MetricSeries, ...]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        ids = [item.observation_id for item in self.series]
        if len(ids) != len(set(ids)):
            raise ValueError("metric observation IDs must be unique")
        return self

    def require(self, observation_id: str) -> MetricSeries:
        for item in self.series:
            if item.observation_id == observation_id:
                return item
        raise KeyError(observation_id)

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(payload).hexdigest()


__all__ = ["DerivedMetrics", "MetricSample", "MetricSeries"]
