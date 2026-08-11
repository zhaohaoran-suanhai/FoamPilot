from __future__ import annotations

from foampilot.runtime.telemetry import IncrementalOpenFOAMLogParser


def test_incremental_parser_retains_step_across_split_chunks() -> None:
    parser = IncrementalOpenFOAMLogParser()

    assert parser.feed("Time = 0.") == ()
    assert parser.feed("5\nSolving for Ux, Initial residual = 1.2e-1, ") == ()
    metrics = parser.feed(
        "Final residual = 2.0e-4, No Iterations 3\n"
    )

    assert len(metrics) == 1
    assert metrics[0].simulation_time == 0.5
    assert metrics[0].iteration is None
    assert metrics[0].field == "Ux"
    assert metrics[0].initial_residual == 0.12
    assert metrics[0].final_residual == 0.0002
    assert metrics[0].solver_iterations == 3


def test_incremental_parser_does_not_synthesize_missing_residuals() -> None:
    parser = IncrementalOpenFOAMLogParser()

    assert parser.feed("Time = 1\nExecutionTime = 2 s\n") == ()
    assert parser.finish() == ()


def test_incremental_parser_finishes_one_unterminated_line() -> None:
    parser = IncrementalOpenFOAMLogParser()
    parser.feed("Iteration: 7\n")
    parser.feed(
        "Solving for p, Initial residual = 0.2, "
        "Final residual = 0.01, No Iterations 2"
    )

    metrics = parser.finish()

    assert len(metrics) == 1
    assert metrics[0].iteration == 7
    assert metrics[0].simulation_time is None
