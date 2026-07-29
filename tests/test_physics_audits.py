from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foampilot.physics import wall_heat_flux
from foampilot.physics import (
    RiemannState,
    audit_wall_heat_flux,
    detect_shock_tube_waves,
    heat_balance,
    parse_wall_heat_flux_data,
    solve_ideal_gas_riemann,
)
from foampilot.cli.main import main


def test_exact_sod_riemann_solution_matches_reference_wave_speeds() -> None:
    solution = solve_ideal_gas_riemann(
        left=RiemannState(
            pressure_pa=1.0,
            density_kg_m3=1.0,
            velocity_m_s=0.0,
        ),
        right=RiemannState(
            pressure_pa=0.1,
            density_kg_m3=0.125,
            velocity_m_s=0.0,
        ),
        gamma=1.4,
        diaphragm_position_m=0.0,
        observation_time_s=1.0,
    )

    assert solution.star_pressure_pa == pytest.approx(0.30313, rel=2e-5)
    assert solution.contact_speed_m_s == pytest.approx(0.92745, rel=2e-5)
    assert solution.left_wave.kind == "rarefaction"
    assert solution.left_wave.head_speed_m_s == pytest.approx(-1.18322, rel=2e-5)
    assert solution.left_wave.tail_speed_m_s == pytest.approx(-0.07027, rel=2e-4)
    assert solution.right_wave.kind == "shock"
    assert solution.right_wave.head_speed_m_s == pytest.approx(1.75216, rel=2e-5)
    assert solution.contact_position_m == pytest.approx(0.92745, rel=2e-5)


def test_wave_detector_returns_coordinates_not_sample_indices() -> None:
    x = [-5 + 0.01 * index for index in range(1001)]
    pressure = [
        1.0 if value < -2.6 else 0.3 if value < 3.88 else 0.1
        for value in x
    ]
    density = [
        1.0
        if value < -2.6
        else 0.4
        if value < 2.05
        else 0.25
        if value < 3.88
        else 0.125
        for value in x
    ]

    detected = detect_shock_tube_waves(
        x,
        pressure,
        density,
        shock_exclusion_width_m=0.05,
    )

    assert detected.rarefaction_position_m == pytest.approx(-2.6)
    assert detected.contact_position_m == pytest.approx(2.05, abs=0.01)
    assert detected.shock_position_m == pytest.approx(3.87, abs=0.01)


def test_wall_heat_flux_data_parser_and_balance_use_integrated_q() -> None:
    rows = parse_wall_heat_flux_data(
        """
# Wall heat-flux
# Time patch min [W/m^2] max [W/m^2] Q [W] q [W/m^2]
1000 hotWall 9.0 11.0 100.0 10.0
1000 coldWall -10.0 -8.0 -95.0 -9.5
"""
    )

    assert [row.patch for row in rows] == ["hotWall", "coldWall"]
    assert rows[0].integrated_heat_flow_w == pytest.approx(100.0)
    balance = heat_balance(rows, hot_patch="hotWall", cold_patch="coldWall")
    assert balance.hot_heat_flow_w == pytest.approx(100.0)
    assert balance.cold_heat_flow_w == pytest.approx(-95.0)
    assert balance.normalized_imbalance == pytest.approx(0.05)


def test_wall_heat_flux_audit_runs_only_on_temporary_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-case"
    (source / "system").mkdir(parents=True)
    (source / "system" / "controlDict").write_text(
        "application buoyantFoam;\n",
        encoding="utf-8",
    )
    seen_copy: list[Path] = []

    def fake_runner(case_copy: Path, openfoam_root: Path) -> subprocess.CompletedProcess[str]:
        assert openfoam_root == tmp_path / "OpenFOAM-10"
        assert case_copy != source
        seen_copy.append(case_copy)
        output = (
            case_copy
            / "postProcessing"
            / "wallHeatFlux"
            / "1000"
            / "wallHeatFlux.dat"
        )
        output.parent.mkdir(parents=True)
        output.write_text(
            "1000 hotWall 9 11 100 10\n"
            "1000 coldWall -10 -8 -98 -9.8\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=["postProcess"],
            returncode=0,
            stdout="End\n",
            stderr="",
        )

    result = audit_wall_heat_flux(
        source,
        openfoam_root=tmp_path / "OpenFOAM-10",
        hot_patch="hotWall",
        cold_patch="coldWall",
        command_runner=fake_runner,
    )

    assert result.normalized_imbalance == pytest.approx(0.02)
    assert len(seen_copy) == 1
    assert not seen_copy[0].exists()
    assert not (source / "postProcessing").exists()


def test_default_wall_heat_runner_uses_solver_postprocess_to_build_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text(
        "application buoyantFoam;\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(wall_heat_flux.subprocess, "run", fake_run)
    wall_heat_flux._run_wall_heat_flux(case, tmp_path / "OpenFOAM-10")

    assert calls[0][-1] == "buoyantFoam"
    assert '"$3" -postProcess -func wallHeatFlux -latestTime' in calls[0][4]


def test_shock_tube_audit_cli_reports_exact_public_wave_positions(
    capsys,
) -> None:
    assert (
        main(
            [
                "audit",
                "shock-tube",
                "--left-pressure",
                "100000",
                "--left-temperature",
                "348.432",
                "--right-pressure",
                "10000",
                "--right-temperature",
                "278.746",
                "--molecular-weight",
                "28.96",
                "--cp",
                "1004.5",
                "--time",
                "0.007",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["solution"]["left_wave"]["head_position_m"] < -2.61
    assert payload["solution"]["right_wave"]["head_position_m"] > 3.87
