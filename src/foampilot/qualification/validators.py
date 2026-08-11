"""Case-specific observable extraction and golden comparison."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from foampilot.physics.wall_heat_flux import (
    audit_wall_heat_flux,
    parse_wall_heat_flux_data,
)
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
        lambda: audit_wall_heat_flux(
            data.case_dir,
            openfoam_root=data.openfoam_root,
            hot_patch="hot",
            cold_patch="cold",
        ).normalized_imbalance,
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


def _metric_coordinates(
    validation: PrivateValidation,
    metric_name: str,
) -> list[list[float]]:
    return next(
        metric.sample_coordinates
        for metric in validation.metrics
        if metric.name == metric_name
    )


def _all_boundary_flux_imbalance(
    data: OpenFOAMCaseData,
    *,
    field: str = "U",
) -> float:
    values = [value for _, value in data.boundary_fluxes(field=field)]
    return abs(sum(values)) / max(sum(map(abs, values)), 1e-12)


def _scalar_transport(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_time": data.latest_time}

    def scalar_inventory() -> float:
        mesh = data.internal_mesh()
        volume = float(
            np.sum(
                np.asarray(
                    mesh.compute_cell_sizes(
                        length=False,
                        area=False,
                        volume=True,
                    ).cell_data["Volume"],
                    dtype=float,
                )
            )
        )
        return data.volume_integral("T") / max(volume, 1e-12)

    _safe(
        "scalar_conservation",
        scalar_inventory,
        output,
    )
    coordinates = _metric_coordinates(
        validation,
        "downstream_scalar_profile",
    )
    _safe(
        "downstream_scalar_profile",
        lambda: flatten_arrays(data.sample("T", coordinates)),
        output,
    )
    return output


def _planar_poiseuille(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_time": data.latest_time}
    _safe(
        "flow_balance",
        lambda: _flux_imbalance(
            data.flux_on_plane(0, -0.1, tolerance=1e-6),
            data.flux_on_plane(0, 0.1, tolerance=1e-6),
        ),
        output,
    )
    coordinates = _metric_coordinates(validation, "velocity_profile")
    _safe(
        "velocity_profile",
        lambda: flatten_arrays(data.sample("U", coordinates)[:, 0]),
        output,
    )
    return output


def _porous_duct(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_iteration": data.latest_time}
    _safe(
        "flow_balance",
        lambda: _all_boundary_flux_imbalance(data),
        output,
    )

    def pressure_drop() -> float:
        inlet = np.asarray(
            data.boundary_patch("inlet").cell_data["p"],
            dtype=float,
        )
        outlet = np.asarray(
            data.boundary_patch("outlet").cell_data["p"],
            dtype=float,
        )
        return float(
            np.mean(inlet)
            - np.mean(outlet)
        )

    _safe("pressure_drop", pressure_drop, output)
    return output


def _compressible_blocked_channel(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_time": data.latest_time}
    _safe("total_mass", lambda: data.volume_integral("rho"), output)
    coordinates = _metric_coordinates(validation, "primitive_profiles")

    def profiles() -> list[float]:
        return flatten_arrays(
            data.sample("p", coordinates),
            data.sample("T", coordinates),
            data.sample("rho", coordinates),
            data.sample("U", coordinates),
        )

    _safe("primitive_profiles", profiles, output)
    return output


def _cht_region_heat_flow(
    case_dir: Path,
    region: str,
    *,
    openfoam_root: Path,
) -> float:
    function_name = f"wallHeatFlux(region={region})"
    script = (
        'source "$1/etc/bashrc"\n'
        'cd "$2"\n'
        'chtMultiRegionFoam -postProcess -func "$3" -latestTime'
    )
    completed = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            script,
            "_",
            str(openfoam_root),
            str(case_dir),
            function_name,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"{function_name} failed"
            + (f": {detail}" if detail else "")
        )
    candidates = sorted(
        case_dir.glob(
            f"postProcessing/{region}/{function_name}/*/wallHeatFlux.dat"
        )
    )
    if not candidates:
        raise RuntimeError(f"{function_name} produced no data")
    rows = parse_wall_heat_flux_data(
        candidates[-1].read_text(encoding="utf-8")
    )
    expected_patch = (
        "fluid_to_solid" if region == "fluid" else "solid_to_fluid"
    )
    selected = [row for row in rows if row.patch == expected_patch]
    if not selected:
        raise RuntimeError(
            f"{function_name} has no {expected_patch!r} patch"
        )
    return max(selected, key=lambda row: row.time).integrated_heat_flow_w


def _cht_cooling_cylinder(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    fluid = OpenFOAMCaseData(
        data.case_dir,
        openfoam_root=data.openfoam_root,
        region="fluid",
    )
    solid = OpenFOAMCaseData(
        data.case_dir,
        openfoam_root=data.openfoam_root,
        region="solid",
    )
    output = {"final_time": min(fluid.latest_time, solid.latest_time)}

    def heat_balance() -> float:
        fluid_flow = _cht_region_heat_flow(
            data.case_dir,
            "fluid",
            openfoam_root=data.openfoam_root,
        )
        solid_flow = _cht_region_heat_flow(
            data.case_dir,
            "solid",
            openfoam_root=data.openfoam_root,
        )
        return abs(fluid_flow + solid_flow) / max(
            abs(fluid_flow),
            abs(solid_flow),
            1e-12,
        )

    _safe("interface_heat_balance", heat_balance, output)

    def profiles() -> list[float]:
        fluid_coordinates = [
            [-0.02, 0, 0],
            [0.01, 0, 0],
            [0.03, 0, 0],
            [0.07, 0, 0],
        ]
        solid_coordinates = [
            [0, 0, 0],
            [0.0025, 0, 0],
            [0.0045, 0, 0],
        ]
        return flatten_arrays(
            fluid.sample("T", fluid_coordinates),
            solid.sample("T", solid_coordinates),
        )

    _safe("temperature_profiles", profiles, output)
    return output


def _srf_rotor(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_time": data.latest_time}
    _safe(
        "flow_balance",
        lambda: _all_boundary_flux_imbalance(data, field="Urel"),
        output,
    )
    coordinates = _metric_coordinates(
        validation,
        "rotating_velocity_profile",
    )
    _safe(
        "rotating_velocity_profile",
        lambda: flatten_arrays(data.sample("Urel", coordinates)),
        output,
    )
    return output


def _mhd_hartmann(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_time": data.latest_time}
    _safe(
        "divergence_conservation",
        lambda: _all_boundary_flux_imbalance(data),
        output,
    )
    coordinates = _metric_coordinates(validation, "velocity_profile")
    _safe(
        "velocity_profile",
        lambda: flatten_arrays(data.sample("U", coordinates)[:, 0]),
        output,
    )
    return output


def _capillary_rise(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_time": data.latest_time}

    # The bottom is an open liquid reservoir, so capillary rise must increase
    # the domain inventory. Compare the final inventory with the frozen
    # reference instead of imposing a physically invalid closed-volume check.
    _safe(
        "liquid_volume",
        lambda: data.volume_integral("alpha.water"),
        output,
    )
    coordinates = _metric_coordinates(validation, "interface_height")

    def interface_height() -> float:
        values = data.sample(
            "alpha.water",
            coordinates,
            allow_invalid=True,
        ).reshape(-1)
        heights = np.asarray(coordinates, dtype=float)[:, 1]
        wet = heights[values >= 0.5]
        return float(max(wet)) if len(wet) else 0.0

    _safe("interface_height", interface_height, output)
    return output


def _solid_plate_hole(
    data: OpenFOAMCaseData,
    validation: PrivateValidation,
) -> dict:
    output = {"final_iteration": data.latest_time}

    def symmetry_error() -> float:
        displacement = np.asarray(
            data.internal_mesh().cell_data["D"],
            dtype=float,
        )
        scale = max(
            float(np.linalg.norm(displacement, axis=1).max()),
            1e-30,
        )
        left = np.asarray(
            data.boundary_patch("left").cell_data["D"],
            dtype=float,
        )
        down = np.asarray(
            data.boundary_patch("down").cell_data["D"],
            dtype=float,
        )
        normal_displacement = max(
            float(np.abs(left[:, 0]).max()),
            float(np.abs(down[:, 1]).max()),
        )
        return normal_displacement / scale

    _safe("displacement_symmetry", symmetry_error, output)
    coordinates = _metric_coordinates(validation, "hole_edge_stress")

    def stress_profile() -> list[float]:
        return flatten_arrays(
            data.sample("sigmaxx", coordinates),
            data.sample("sigmayy", coordinates),
            data.sample("sigmaxy", coordinates),
        )

    _safe("hole_edge_stress", stress_profile, output)
    return output


EXTRACTORS = {
    "potential-cylinder": _potential,
    "laminar-cavity": _cavity,
    "rans-pitzdaily": _rans_channel,
    "compressible-shock-tube": _shock,
    "buoyant-cavity": _buoyant,
    "multiphase-dam-break": _multiphase,
    "scalar-transport-pitzdaily": _scalar_transport,
    "laminar-planar-poiseuille": _planar_poiseuille,
    "porous-angled-duct": _porous_duct,
    "compressible-blocked-channel": _compressible_blocked_channel,
    "cht-cooling-cylinder": _cht_cooling_cylinder,
    "srf-rotor": _srf_rotor,
    "mhd-hartmann": _mhd_hartmann,
    "multiphase-capillary-rise": _capillary_rise,
    "solid-plate-hole": _solid_plate_hole,
}


def extract_observations(
    case_id: str,
    case_dir: str | Path,
    validation: PrivateValidation,
    *,
    openfoam_root: Path,
) -> dict[str, Any]:
    return EXTRACTORS[case_id](
        OpenFOAMCaseData(case_dir, openfoam_root=openfoam_root),
        validation,
    )


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
