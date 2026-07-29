"""Case-specific observable extraction and golden comparison."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .models import (
    PrivateValidation,
    QualificationMetric,
    compare_metric,
)

from .profiles import OpenFOAMCaseData, flatten_arrays


def _safe(metric_name: str, callback: Callable[[], Any], output: dict) -> None:
    try:
        output[metric_name] = callback()
    except Exception as error:
        output[metric_name] = None
        output.setdefault("_errors", {})[metric_name] = (
            f"{type(error).__name__}: {error}"
        )


def _flux_imbalance(left: float, right: float) -> float:
    denominator = max(abs(left), abs(right), 1e-12)
    return abs(left + right) / denominator


def _potential(data: OpenFOAMCaseData, validation: PrivateValidation) -> dict:
    output = {"final_state": data.latest_time}
    _safe(
        "boundary_flux_balance",
        lambda: _flux_imbalance(
            data.flux_on_plane(0, -2.0, tolerance=1e-6),
            data.flux_on_plane(0, 2.0, tolerance=1e-6),
        ),
        output,
    )
    coordinates = validation.metrics[2].sample_coordinates
    _safe(
        "analytical_velocity_profile",
        lambda: flatten_arrays(data.sample("U", coordinates)),
        output,
    )
    return output


def _cavity(data: OpenFOAMCaseData, validation: PrivateValidation) -> dict:
    output = {"final_time": data.latest_time}

    def flux() -> float:
        values = [value for _, value in data.boundary_fluxes()]
        return abs(sum(values)) / max(sum(map(abs, values)), 1e-12)

    _safe("closed_domain_flux", flux, output)
    coordinates = next(
        metric.sample_coordinates
        for metric in validation.metrics
        if metric.name == "centerline_velocity_profiles"
    )
    _safe(
        "centerline_velocity_profiles",
        lambda: flatten_arrays(data.sample("U", coordinates)),
        output,
    )

    def energy_history() -> list[float]:
        values = []
        for time_value in data.times:
            mesh = data.internal_mesh(time_value)
            velocity = np.asarray(mesh.cell_data["U"], dtype=float)
            volumes = np.asarray(
                mesh.compute_cell_sizes(
                    length=False, area=False, volume=True
                ).cell_data["Volume"]
            )
            values.append(
                float(
                    np.sum(0.5 * np.sum(velocity**2, axis=1) * volumes)
                    / np.sum(volumes)
                )
            )
        return values

    _safe("kinetic_energy_history", energy_history, output)
    return output


def _rans_channel(
    data: OpenFOAMCaseData, validation: PrivateValidation
) -> dict:
    output = {"final_iteration": data.latest_time}
    _safe(
        "inlet_outlet_flow_balance",
        lambda: _flux_imbalance(
            data.flux_on_plane(0, -0.0206, tolerance=2e-5),
            data.flux_on_plane(0, 0.29, tolerance=2e-5),
        ),
        output,
    )

    def pressure_change() -> float:
        inlet = [
            [-0.0205, y, 0.0] for y in np.linspace(0.001, 0.024, 20)
        ]
        outlet = [
            [0.289, y, 0.0] for y in np.linspace(-0.015, 0.015, 20)
        ]
        return float(
            np.mean(data.sample("p", inlet))
            - np.mean(data.sample("p", outlet))
        )

    _safe("pressure_change", pressure_change, output)
    coordinates = next(
        metric.sample_coordinates
        for metric in validation.metrics
        if metric.name == "downstream_velocity_profile"
    )
    _safe(
        "downstream_velocity_profile",
        lambda: flatten_arrays(data.sample("U", coordinates)),
        output,
    )
    return output


def _shock(data: OpenFOAMCaseData, validation: PrivateValidation) -> dict:
    output = {"final_time": data.latest_time}

    def mass_error() -> float:
        gas_constant = 8314.462618 / 28.96
        initial = 20.0 * (
            100000.0 / (gas_constant * 348.432)
            + 10000.0 / (gas_constant * 278.746)
        )
        final = data.volume_integral("rho")
        return abs(final - initial) / max(abs(initial), 1e-12)

    _safe("total_mass", mass_error, output)
    x = np.linspace(-4.995, 4.995, 1000)
    coordinates = np.column_stack((x, np.zeros_like(x), np.zeros_like(x)))

    def profiles() -> list[float]:
        return flatten_arrays(
            data.sample("p", coordinates),
            data.sample("T", coordinates),
            data.sample("rho", coordinates),
            data.sample("U", coordinates)[:, 0],
        )

    _safe("primitive_profiles", profiles, output)

    def wave_positions() -> list[float]:
        pressure = data.sample("p", coordinates).reshape(-1)
        density = data.sample("rho", coordinates).reshape(-1)
        p_span = max(float(np.ptp(pressure)), 1e-12)
        left = pressure[:50].mean()
        right = pressure[-50:].mean()
        changed_left = np.where(abs(pressure - left) > 0.01 * p_span)[0]
        changed_right = np.where(abs(pressure - right) > 0.01 * p_span)[0]
        rarefaction = x[changed_left[0]]
        shock = x[changed_right[-1]]
        density_gradient = np.abs(np.gradient(density, x))
        mask = (x > rarefaction) & (x < shock - 0.05)
        contact = x[np.where(mask)[0][np.argmax(density_gradient[mask])]]
        return [float(rarefaction), float(contact), float(shock)]

    _safe("wave_positions", wave_positions, output)
    return output


def _buoyant(data: OpenFOAMCaseData, validation: PrivateValidation) -> dict:
    output = {"final_time": data.latest_time}
    conductivity = 1.831e-5 * 1004.4 / 0.705
    width = 0.076
    temperature_difference = 307.75 - 288.15

    def wall_fluxes() -> tuple[float, float]:
        mesh = data.internal_mesh()
        centers = np.asarray(mesh.cell_centers().points)
        temperature = np.asarray(mesh.cell_data["T"], dtype=float)
        x_values = np.unique(np.round(centers[:, 0], 12))
        cold_x, hot_x = x_values[0], x_values[-1]
        cold = temperature[np.isclose(centers[:, 0], cold_x)]
        hot = temperature[np.isclose(centers[:, 0], hot_x)]
        cold_gradient = (np.mean(cold) - 288.15) / cold_x
        hot_gradient = (307.75 - np.mean(hot)) / (width - hot_x)
        return conductivity * cold_gradient, conductivity * hot_gradient

    _safe(
        "wall_heat_balance",
        lambda: abs(wall_fluxes()[0])
        / max(abs(wall_fluxes()[1]), 1e-12),
        output,
    )

    def profiles() -> list[float]:
        heights = [0.218, 0.654, 0.872, 1.09, 1.308, 1.526, 1.962]
        x = np.linspace(0.0011, 0.0749, 35)
        coordinates = np.asarray(
            [[x_value, height, 0.0] for height in heights for x_value in x]
        )
        return flatten_arrays(
            data.sample("T", coordinates),
            data.sample("U", coordinates)[:, 1],
        )

    _safe("temperature_velocity_profiles", profiles, output)
    _safe(
        "mean_nusselt",
        lambda: float(
            np.mean(np.abs(wall_fluxes()))
            * width
            / (conductivity * temperature_difference)
        ),
        output,
    )
    return output


def _multiphase(
    data: OpenFOAMCaseData, validation: PrivateValidation
) -> dict:
    output = {"final_time": data.latest_time}

    def volume_error() -> float:
        initial = data.volume_integral(
            "alpha.water", time_value=min(data.times)
        )
        if abs(initial) < 1e-12:
            # The official source retains alpha.water.orig beside the restored
            # field; VTK can select the zero-valued template at time zero.
            initial = 0.1461 * 0.292 * 0.0146
        final = data.volume_integral("alpha.water")
        return abs(final - initial) / max(abs(initial), 1e-12)

    _safe("water_volume", volume_error, output)
    x_samples = np.asarray([0.1, 0.2, 0.3, 0.4, 0.5])
    y_samples = np.linspace(0.003, 0.581, 100)

    def interface_history() -> list[float]:
        heights = []
        for time_value in data.times:
            for x_value in x_samples:
                coordinates = [
                    [x_value, y_value, 0.0073]
                    for y_value in y_samples
                ]
                alpha = data.sample(
                    "alpha.water",
                    coordinates,
                    time_value=time_value,
                    allow_invalid=True,
                ).reshape(-1)
                wet = y_samples[alpha >= 0.5]
                heights.append(float(max(wet)) if len(wet) else 0.0)
        return heights

    def front_history() -> list[float]:
        x = np.linspace(0.003, 0.581, 200)
        coordinates = [[value, 0.01, 0.0073] for value in x]
        fronts = []
        for time_value in data.times:
            alpha = data.sample(
                "alpha.water",
                coordinates,
                time_value=time_value,
                allow_invalid=True,
            ).reshape(-1)
            wet = x[alpha >= 0.5]
            fronts.append(float(max(wet)) if len(wet) else 0.0)
        return fronts

    _safe("free_surface_profiles", interface_history, output)
    _safe("leading_front_position", front_history, output)
    return output


EXTRACTORS = {
    "potential-cylinder": _potential,
    "laminar-cavity": _cavity,
    "rans-pitzdaily": _rans_channel,
    "compressible-shock-tube": _shock,
    "buoyant-cavity": _buoyant,
    "multiphase-dam-break": _multiphase,
}


def extract_observations(
    case_id: str,
    case_dir: str | Path,
    validation: PrivateValidation,
) -> dict[str, Any]:
    return EXTRACTORS[case_id](OpenFOAMCaseData(case_dir), validation)


def validate_observations(
    observations: dict[str, Any], golden: dict[str, Any]
) -> list[QualificationMetric]:
    results = []
    errors = observations.get("_errors", {})
    for metric in golden["metrics"]:
        name = metric["name"]
        comparison = compare_metric(
            observed=observations.get(name),
            reference=metric["reference"],
            tolerance=metric["final_tolerance"],
            mode=metric["comparison_mode"],
        )
        detail = comparison.detail
        if name in errors:
            detail = f"{detail}; extraction error: {errors[name]}"
        results.append(
            QualificationMetric(
                name=name,
                value=comparison.error,
                unit=metric["unit"],
                passed=comparison.passed,
                required=metric.get("required", True),
                detail=detail,
            )
        )
    return results
