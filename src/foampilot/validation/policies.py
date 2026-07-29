"""Standalone reviewed thresholds for public solver-family checks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShockTubePolicy(StrictModel):
    min_observed_to_target_co_ratio: float = Field(gt=0)
    max_observed_to_target_co_ratio: float = Field(gt=0)
    position_tolerance_cell_widths: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_ratio_order(self) -> "ShockTubePolicy":
        if (
            self.max_observed_to_target_co_ratio
            < self.min_observed_to_target_co_ratio
        ):
            raise ValueError("maximum Courant ratio must be at least minimum")
        return self


class BuoyantPolicy(StrictModel):
    residual_fields: list[str] = Field(min_length=1)
    residual_window: int = Field(ge=1)
    max_terminal_initial_residual: float = Field(ge=0)
    max_terminal_to_initial_median_ratio: float = Field(ge=0)
    max_terminal_local_continuity_error: float = Field(ge=0)
    max_abs_cumulative_continuity_error: float = Field(ge=0)
    max_wall_heat_imbalance: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_residual_fields(self) -> "BuoyantPolicy":
        if len(self.residual_fields) != len(set(self.residual_fields)):
            raise ValueError("residual fields must be unique")
        if any(not field.strip() for field in self.residual_fields):
            raise ValueError("residual fields must not be blank")
        return self
