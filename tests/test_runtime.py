from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.runtime import (
    RuntimeConfig,
    parse_openfoam_log,
    run_preflight,
)


def test_local_foundation_v10_preflight_detects_icofoam() -> None:
    config = RuntimeConfig.local_foundation_v10()
    assert config.openfoam_root == Path("/home/edwin/workplace/OpenFOAM-10")
    assert config.python_executable == Path(
        "/home/edwin/feal-venv-py312/bin/python"
    )
    assert config.bubblewrap == Path("/usr/local/bin/bwrap")
    checks = {check.name: check for check in run_preflight(config)}
    assert checks["solver:icoFoam"].ok, checks["solver:icoFoam"].detail
    assert checks["bubblewrap_launch"].ok, checks["bubblewrap_launch"].detail


def test_openfoam_log_parser_tracks_completion_and_failures() -> None:
    text = """Time = 0.5s
Courant Number mean: 0.04 max: 0.19
Mean and max Courant Numbers = 0.025 0.034
DILUPBiCGStab: Solving for Ux, Initial residual = 0.2, Final residual = 0.01, No Iterations 2
time step continuity errors : sum local = 1e-10, global = -2e-12, cumulative = 1e-12
End
"""
    summary = parse_openfoam_log(text)
    assert summary.latest_time == 0.5
    assert summary.last_cumulative_continuity_error == 1e-12
    assert summary.courant_maxima == [0.19, 0.034]
    assert summary.residuals[0].field == "Ux"
    assert summary.residuals[0].initial == 0.2
    assert summary.continuity[0].local == 1e-10
    assert summary.normal_end
    assert summary.completed

    failed = parse_openfoam_log("Time = 0.1\nFOAM FATAL ERROR\nnan\n")
    assert failed.fatal
    assert failed.non_finite
    assert not failed.completed


def test_openfoam_log_parser_accepts_iteration_progress_as_time() -> None:
    summary = parse_openfoam_log(
        "Iteration = 0.01995\n"
        "Iteration = 0.02\n"
        "End\n"
    )

    assert summary.latest_time == 0.02
    assert summary.completed


def test_openfoam_log_parser_accepts_colon_iteration_progress() -> None:
    summary = parse_openfoam_log(
        "Iteration: 9.99999999996\n"
        "End\n"
    )

    assert summary.latest_time == pytest.approx(10.0)
    assert summary.completed
