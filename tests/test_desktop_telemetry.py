from __future__ import annotations

from foampilot.desktop.telemetry import parse_residual_series


def test_parse_residual_series_associates_time_and_iteration() -> None:
    samples = parse_residual_series(
        "Time = 0.1\n"
        "smoothSolver: Solving for Ux, Initial residual = 2e-1, "
        "Final residual = 1e-2, No Iterations 2\n"
        "Time = 0.2\n"
        "GAMG: Solving for p, Initial residual = 0.1, "
        "Final residual = 0.001, No Iterations 3\n"
        "Iteration = 7\n"
        "smoothSolver: Solving for Uy, Initial residual = 0.05, "
        "Final residual = 0.005, No Iterations 1\n",
        attempt=1,
        source_log="attempt-01/case/.foampilot/logs/solve.stdout.log",
    )

    assert [item.sequence for item in samples] == [1, 2, 3]
    assert [(item.field, item.simulation_time) for item in samples] == [
        ("Ux", 0.1),
        ("p", 0.2),
        ("Uy", None),
    ]
    assert samples[2].iteration == 7
    assert samples[0].initial_residual == 0.2
    assert samples[0].final_residual == 0.01
    assert samples[0].solver_iterations == 2


def test_parse_residual_series_ignores_invalid_and_nonpositive_values() -> None:
    samples = parse_residual_series(
        "Solving for Ux, Initial residual = 0, Final residual = 0, "
        "No Iterations 0\n"
        "not a residual line\n"
        "Solving for p, Initial residual = 1e-3, Final residual = 1e-5, "
        "No Iterations 2\n",
        attempt=2,
        source_log="solve.log",
    )

    assert len(samples) == 1
    assert samples[0].attempt == 2
    assert samples[0].field == "p"
    assert samples[0].source_log == "solve.log"


def test_parse_residual_series_returns_empty_for_unrelated_log() -> None:
    assert parse_residual_series(
        "Create time\nEnd\n",
        attempt=None,
        source_log="blockMesh.stdout.log",
    ) == ()
