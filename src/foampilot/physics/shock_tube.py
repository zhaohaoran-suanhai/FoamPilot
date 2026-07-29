"""Exact one-dimensional ideal-gas Riemann solution for shock-tube audits."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiemannState(StrictModel):
    pressure_pa: float = Field(gt=0)
    density_kg_m3: float = Field(gt=0)
    velocity_m_s: float


class RiemannWave(StrictModel):
    side: Literal["left", "right"]
    kind: Literal["shock", "rarefaction"]
    head_speed_m_s: float
    tail_speed_m_s: float
    head_position_m: float
    tail_position_m: float


class RiemannSolution(StrictModel):
    gamma: float
    star_pressure_pa: float
    contact_speed_m_s: float
    contact_position_m: float
    left_wave: RiemannWave
    right_wave: RiemannWave


class DetectedWavePositions(StrictModel):
    rarefaction_position_m: float
    contact_position_m: float
    shock_position_m: float


def detect_shock_tube_waves(
    coordinates_m: Sequence[float],
    pressure: Sequence[float],
    density: Sequence[float],
    *,
    end_state_fraction: float = 0.05,
    pressure_change_fraction: float = 0.01,
    shock_exclusion_width_m: float,
) -> DetectedWavePositions:
    """Detect Riemann waves from one-dimensional sampled public fields."""

    x = [float(value) for value in coordinates_m]
    p = [float(value) for value in pressure]
    rho = [float(value) for value in density]
    if len(x) < 5 or len(p) != len(x) or len(rho) != len(x):
        raise ValueError(
            "coordinates, pressure, and density need equal length >= 5"
        )
    if not all(
        math.isfinite(value) for value in (*x, *p, *rho)
    ):
        raise ValueError("wave-detection samples must be finite")
    if any(right <= left for left, right in zip(x, x[1:])):
        raise ValueError("coordinates must be strictly increasing")
    if not 0 < end_state_fraction < 0.5:
        raise ValueError("end_state_fraction must be between 0 and 0.5")
    if pressure_change_fraction <= 0 or shock_exclusion_width_m <= 0:
        raise ValueError("wave-detection thresholds must be positive")

    end_count = max(1, int(len(x) * end_state_fraction))
    left_state = sum(p[:end_count]) / end_count
    right_state = sum(p[-end_count:]) / end_count
    pressure_span = max(max(p) - min(p), 1e-12)
    changed_left = [
        index
        for index, value in enumerate(p)
        if abs(value - left_state)
        > pressure_change_fraction * pressure_span
    ]
    changed_right = [
        index
        for index, value in enumerate(p)
        if abs(value - right_state)
        > pressure_change_fraction * pressure_span
    ]
    if not changed_left or not changed_right:
        raise ValueError("could not detect rarefaction and shock")
    rarefaction = x[changed_left[0]]
    shock = x[changed_right[-1]]

    gradients = [0.0] * len(x)
    for index in range(1, len(x) - 1):
        gradients[index] = abs(
            (rho[index + 1] - rho[index - 1])
            / (x[index + 1] - x[index - 1])
        )
    contact_candidates = [
        index
        for index, coordinate in enumerate(x)
        if coordinate > rarefaction
        and coordinate < shock - shock_exclusion_width_m
    ]
    if not contact_candidates:
        raise ValueError("could not isolate contact-wave search interval")
    contact_index = max(
        contact_candidates, key=lambda index: gradients[index]
    )
    return DetectedWavePositions(
        rarefaction_position_m=rarefaction,
        contact_position_m=x[contact_index],
        shock_position_m=shock,
    )


def ideal_gas_density(
    pressure_pa: float,
    temperature_k: float,
    molecular_weight_kg_per_kmol: float,
    *,
    universal_gas_constant_j_per_kmol_k: float = 8314.46261815324,
) -> float:
    """Return density from public ideal-gas inputs."""

    if pressure_pa <= 0 or temperature_k <= 0:
        raise ValueError("pressure and temperature must be positive")
    if molecular_weight_kg_per_kmol <= 0:
        raise ValueError("molecular weight must be positive")
    specific_gas_constant = (
        universal_gas_constant_j_per_kmol_k
        / molecular_weight_kg_per_kmol
    )
    return pressure_pa / (specific_gas_constant * temperature_k)


def _pressure_function(
    pressure: float,
    state: RiemannState,
    gamma: float,
) -> tuple[float, float]:
    """Return Toro's pressure function and derivative for one state."""

    sound_speed = math.sqrt(
        gamma * state.pressure_pa / state.density_kg_m3
    )
    if pressure > state.pressure_pa:
        a_coeff = 2.0 / ((gamma + 1.0) * state.density_kg_m3)
        b_coeff = (
            (gamma - 1.0) / (gamma + 1.0) * state.pressure_pa
        )
        root = math.sqrt(a_coeff / (pressure + b_coeff))
        value = (pressure - state.pressure_pa) * root
        derivative = root * (
            1.0
            - 0.5
            * (pressure - state.pressure_pa)
            / (pressure + b_coeff)
        )
        return value, derivative

    exponent = (gamma - 1.0) / (2.0 * gamma)
    pressure_ratio = pressure / state.pressure_pa
    value = (
        2.0
        * sound_speed
        / (gamma - 1.0)
        * (pressure_ratio**exponent - 1.0)
    )
    derivative = (
        1.0
        / (state.density_kg_m3 * sound_speed)
        * pressure_ratio ** (-(gamma + 1.0) / (2.0 * gamma))
    )
    return value, derivative


def _star_pressure(
    left: RiemannState,
    right: RiemannState,
    gamma: float,
) -> tuple[float, float]:
    left_sound = math.sqrt(gamma * left.pressure_pa / left.density_kg_m3)
    right_sound = math.sqrt(
        gamma * right.pressure_pa / right.density_kg_m3
    )
    pressure = max(
        1e-12,
        0.5 * (left.pressure_pa + right.pressure_pa)
        - 0.125
        * (right.velocity_m_s - left.velocity_m_s)
        * (left.density_kg_m3 + right.density_kg_m3)
        * (left_sound + right_sound),
    )
    for _ in range(100):
        left_value, left_derivative = _pressure_function(
            pressure, left, gamma
        )
        right_value, right_derivative = _pressure_function(
            pressure, right, gamma
        )
        next_pressure = pressure - (
            left_value
            + right_value
            + right.velocity_m_s
            - left.velocity_m_s
        ) / (left_derivative + right_derivative)
        next_pressure = max(1e-12, next_pressure)
        relative_change = (
            2.0
            * abs(next_pressure - pressure)
            / (next_pressure + pressure)
        )
        pressure = next_pressure
        if relative_change <= 1e-10:
            break
    else:
        raise RuntimeError("Riemann star-pressure iteration did not converge")

    left_value, _ = _pressure_function(pressure, left, gamma)
    right_value, _ = _pressure_function(pressure, right, gamma)
    contact_speed = 0.5 * (
        left.velocity_m_s
        + right.velocity_m_s
        + right_value
        - left_value
    )
    return pressure, contact_speed


def _wave(
    *,
    side: Literal["left", "right"],
    state: RiemannState,
    star_pressure: float,
    contact_speed: float,
    gamma: float,
    diaphragm_position: float,
    observation_time: float,
) -> RiemannWave:
    sound_speed = math.sqrt(
        gamma * state.pressure_pa / state.density_kg_m3
    )
    direction = -1.0 if side == "left" else 1.0
    if star_pressure > state.pressure_pa:
        speed = state.velocity_m_s + direction * sound_speed * math.sqrt(
            (gamma + 1.0)
            / (2.0 * gamma)
            * (star_pressure / state.pressure_pa)
            + (gamma - 1.0) / (2.0 * gamma)
        )
        head_speed = speed
        tail_speed = speed
        kind: Literal["shock", "rarefaction"] = "shock"
    else:
        star_sound = sound_speed * (
            star_pressure / state.pressure_pa
        ) ** ((gamma - 1.0) / (2.0 * gamma))
        head_speed = state.velocity_m_s + direction * sound_speed
        tail_speed = contact_speed + direction * star_sound
        kind = "rarefaction"

    return RiemannWave(
        side=side,
        kind=kind,
        head_speed_m_s=head_speed,
        tail_speed_m_s=tail_speed,
        head_position_m=diaphragm_position
        + head_speed * observation_time,
        tail_position_m=diaphragm_position
        + tail_speed * observation_time,
    )


def solve_ideal_gas_riemann(
    *,
    left: RiemannState,
    right: RiemannState,
    gamma: float,
    diaphragm_position_m: float,
    observation_time_s: float,
) -> RiemannSolution:
    """Solve an ideal-gas Riemann problem without reference to golden output."""

    if gamma <= 1:
        raise ValueError("gamma must be greater than one")
    if observation_time_s < 0:
        raise ValueError("observation_time_s must be non-negative")
    star_pressure, contact_speed = _star_pressure(left, right, gamma)
    return RiemannSolution(
        gamma=gamma,
        star_pressure_pa=star_pressure,
        contact_speed_m_s=contact_speed,
        contact_position_m=diaphragm_position_m
        + contact_speed * observation_time_s,
        left_wave=_wave(
            side="left",
            state=left,
            star_pressure=star_pressure,
            contact_speed=contact_speed,
            gamma=gamma,
            diaphragm_position=diaphragm_position_m,
            observation_time=observation_time_s,
        ),
        right_wave=_wave(
            side="right",
            state=right,
            star_pressure=star_pressure,
            contact_speed=contact_speed,
            gamma=gamma,
            diaphragm_position=diaphragm_position_m,
            observation_time=observation_time_s,
        ),
    )
