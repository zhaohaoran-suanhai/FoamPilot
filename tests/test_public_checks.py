from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from foampilot.physics import (
    PatchHeatFlow,
    RiemannState,
    heat_balance,
    solve_ideal_gas_riemann,
)
from foampilot.evidence import OpenFOAM10EvidenceExtractor
from foampilot.plans import NativeCommand
from foampilot.runtime import PlanRunResult, PlanStepResult
from foampilot.validation import (
    BuoyantPolicy,
    ShockTubePolicy,
    ShockTubeRunEvidence,
    TimeControlEvidence,
    check_buoyant_run,
    check_shock_tube_run,
    parse_time_control,
)
from tests.test_execution_plan import valid_plan


RHOCENTRAL = ShockTubePolicy(
    min_observed_to_target_co_ratio=0.25,
    max_observed_to_target_co_ratio=1.05,
    position_tolerance_cell_widths=10,
)
BUOYANT = BuoyantPolicy(
    residual_fields=["p_rgh", "h", "k", "omega"],
    residual_window=20,
    max_terminal_initial_residual=0.01,
    max_terminal_to_initial_median_ratio=0.5,
    max_terminal_local_continuity_error=0.01,
    max_abs_cumulative_continuity_error=1.0,
    max_wall_heat_imbalance=0.1,
)


def _exact_solution():
    return solve_ideal_gas_riemann(
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


def test_parse_time_control_reads_public_openfoam_entries() -> None:
    controls = parse_time_control(
        """
deltaT 1e-6;
adjustTimeStep yes;
maxCo 0.2;
maxDeltaT 1e-5;
"""
    )
    assert controls == TimeControlEvidence(
        delta_t_initial_s=1e-6,
        adjust_time_step=True,
        max_co=0.2,
        max_delta_t_s=1e-5,
    )


def test_shock_self_check_passes_safe_adaptive_and_analytical_evidence() -> None:
    exact = _exact_solution()
    report = check_shock_tube_run(
        RHOCENTRAL,
        controls=TimeControlEvidence(
            delta_t_initial_s=1e-6,
            adjust_time_step=True,
            max_co=0.2,
            max_delta_t_s=1e-5,
        ),
        evidence=ShockTubeRunEvidence(
            observed_max_courant=[0.01, 0.18, 0.199],
            cell_width_m=0.01,
            rarefaction_position_m=exact.left_wave.head_position_m + 0.02,
            contact_position_m=exact.contact_position_m - 0.02,
            shock_position_m=exact.right_wave.head_position_m + 0.01,
        ),
        exact=exact,
    )

    assert report.passed
    assert {check.name for check in report.checks} == {
        "adaptive_time_semantics",
        "observed_courant",
        "analytical_wave_positions",
    }


def test_shock_self_check_rejects_fixed_cap_low_co_and_shifted_wave() -> None:
    exact = _exact_solution()
    report = check_shock_tube_run(
        RHOCENTRAL,
        controls=TimeControlEvidence(
            delta_t_initial_s=1e-6,
            adjust_time_step=True,
            max_co=0.2,
            max_delta_t_s=1e-6,
        ),
        evidence=ShockTubeRunEvidence(
            observed_max_courant=[0.001, 0.01],
            cell_width_m=0.01,
            rarefaction_position_m=exact.left_wave.head_position_m + 0.2,
            contact_position_m=exact.contact_position_m,
            shock_position_m=exact.right_wave.head_position_m,
        ),
        exact=exact,
    )

    assert not report.passed
    failures = {check.name for check in report.checks if not check.passed}
    assert failures == {
        "adaptive_time_semantics",
        "observed_courant",
        "analytical_wave_positions",
    }


def _buoyant_log(
    *,
    first: float,
    last: float,
    local_continuity: float,
) -> str:
    fields = ("p_rgh", "Ux", "h", "k", "omega")
    lines: list[str] = []
    for value in (first, first * 0.8, last * 1.2, last):
        for field in fields:
            lines.append(
                f"solver: Solving for {field}, Initial residual = "
                f"{value}, Final residual = {value / 10}, "
                "No Iterations 1"
            )
        lines.append(
            "time step continuity errors : sum local = "
            f"{local_continuity}, global = 1e-8, cumulative = 2e-8"
        )
    lines.append("End")
    return "\n".join(lines)


def _balance(imbalance: float):
    return heat_balance(
        [
            PatchHeatFlow(
                time=1000,
                patch="hot",
                minimum_w_m2=1,
                maximum_w_m2=1,
                integrated_heat_flow_w=100,
                mean_heat_flux_w_m2=1,
            ),
            PatchHeatFlow(
                time=1000,
                patch="cold",
                minimum_w_m2=-1,
                maximum_w_m2=-1,
                integrated_heat_flow_w=-100 * (1 - imbalance),
                mean_heat_flux_w_m2=-1,
            ),
        ],
        hot_patch="hot",
        cold_patch="cold",
    )


def _run_facts(tmp_path: Path, text: str):
    stdout = tmp_path / "solve.out"
    stderr = tmp_path / "solve.err"
    stdout.write_text(text, encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    now = datetime.now(timezone.utc)
    result = PlanRunResult(
        case_dir=tmp_path,
        steps=[
            PlanStepResult(
                step_id="solve",
                command=["buoyantFoam"],
                return_code=0,
                started_at=now,
                finished_at=now,
                elapsed_seconds=0,
                timed_out=False,
                stdout_path=stdout,
                stderr_path=stderr,
                execution_backend="host",
            )
        ],
    )
    plan = valid_plan().model_copy(
        update={
            "commands": [
                NativeCommand(
                    step_id="solve",
                    stage="solve",
                    executable="buoyantFoam",
                    timeout_seconds=30,
                )
            ]
        }
    )
    return OpenFOAM10EvidenceExtractor().extract(result, plan, tmp_path)


def test_buoyant_self_check_uses_residual_continuity_and_true_heat_flux(
    tmp_path: Path,
) -> None:
    report = check_buoyant_run(
        BUOYANT,
        run_facts=_run_facts(
            tmp_path,
            _buoyant_log(first=0.5, last=1e-3, local_continuity=1e-4),
        ),
        wall_heat_balance=_balance(0.02),
    )

    assert report.passed
    assert {check.name for check in report.checks} == {
        "residual_trend",
        "continuity_error",
        "wall_heat_balance",
    }


def test_buoyant_self_check_rejects_unconverged_or_unbalanced_run(
    tmp_path: Path,
) -> None:
    report = check_buoyant_run(
        BUOYANT,
        run_facts=_run_facts(
            tmp_path,
            _buoyant_log(first=0.01, last=0.02, local_continuity=0.03),
        ),
        wall_heat_balance=_balance(0.2),
    )

    assert not report.passed
    assert {
        check.name for check in report.checks if not check.passed
    } == {
        "residual_trend",
        "continuity_error",
        "wall_heat_balance",
    }
