from __future__ import annotations

import pytest

from foampilot.runtime import (
    RuntimeConfig,
    parse_openfoam_log,
)


def test_runtime_schema_has_no_machine_specific_python_or_tutorial_fields() -> None:
    assert "python_executable" not in RuntimeConfig.model_fields
    assert "tutorial_root" not in RuntimeConfig.model_fields
    assert "execution_backend" not in RuntimeConfig.model_fields


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
