"""Structured public physics checks that never consume golden results."""

from __future__ import annotations

import math
import re
import statistics

from pydantic import BaseModel, ConfigDict, Field

from foampilot.physics import RiemannSolution, WallHeatBalance
from foampilot.evidence import RunFacts

from .policies import BuoyantPolicy, ShockTubePolicy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicCheck(StrictModel):
    name: str
    passed: bool
    detail: str
    observed: dict[str, float | bool | str | None] = Field(
        default_factory=dict
    )
    limits: dict[str, float | bool | str] = Field(default_factory=dict)


class PublicCheckReport(StrictModel):
    family_id: str
    checks: list[PublicCheck]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def feedback(self) -> str:
        failed = [check.detail for check in self.checks if not check.passed]
        return "\n".join(f"- {detail}" for detail in failed)


class TimeControlEvidence(StrictModel):
    delta_t_initial_s: float | None = Field(default=None, gt=0)
    adjust_time_step: bool | None = None
    max_co: float | None = Field(default=None, gt=0)
    max_delta_t_s: float | None = Field(default=None, gt=0)


class ShockTubeRunEvidence(StrictModel):
    observed_max_courant: list[float] = Field(default_factory=list)
    cell_width_m: float = Field(gt=0)
    rarefaction_position_m: float | None = None
    contact_position_m: float | None = None
    shock_position_m: float | None = None


def _dictionary_value(text: str, key: str) -> str | None:
    without_blocks = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    without_comments = re.sub(r"//.*?$", "", without_blocks, flags=re.MULTILINE)
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}\s+([^;]+);",
        without_comments,
    )
    return match.group(1).strip() if match else None


def parse_time_control(text: str) -> TimeControlEvidence:
    """Extract adaptive controls from a public `controlDict`."""

    def number(key: str) -> float | None:
        value = _dictionary_value(text, key)
        return float(value) if value is not None else None

    adjust = _dictionary_value(text, "adjustTimeStep")
    parsed_adjust = (
        adjust.lower() in {"yes", "true", "on", "1"}
        if adjust is not None
        else None
    )
    return TimeControlEvidence(
        delta_t_initial_s=number("deltaT"),
        adjust_time_step=parsed_adjust,
        max_co=number("maxCo"),
        max_delta_t_s=number("maxDeltaT"),
    )


def check_shock_tube_run(
    policy: ShockTubePolicy,
    *,
    controls: TimeControlEvidence,
    evidence: ShockTubeRunEvidence,
    exact: RiemannSolution,
) -> PublicCheckReport:
    """Check time control, Courant behavior, and analytical wave positions."""

    time_ok = (
        controls.adjust_time_step is True
        and controls.delta_t_initial_s is not None
        and controls.max_delta_t_s is not None
        and controls.max_delta_t_s > controls.delta_t_initial_s
        and controls.max_co is not None
    )
    time_check = PublicCheck(
        name="adaptive_time_semantics",
        passed=time_ok,
        detail=(
            "Adaptive time control is coherent."
            if time_ok
            else "Set adjustTimeStep yes, maxCo, and maxDeltaT strictly "
            "greater than the initial deltaT."
        ),
        observed={
            "adjust_time_step": controls.adjust_time_step,
            "delta_t_initial_s": controls.delta_t_initial_s,
            "max_delta_t_s": controls.max_delta_t_s,
            "max_co": controls.max_co,
        },
        limits={"max_delta_t_gt_initial": True},
    )

    finite_co = [
        value
        for value in evidence.observed_max_courant
        if math.isfinite(value) and value >= 0
    ]
    observed_peak = max(finite_co) if finite_co else None
    lower = (
        controls.max_co * policy.min_observed_to_target_co_ratio
        if controls.max_co is not None
        else None
    )
    upper = (
        controls.max_co * policy.max_observed_to_target_co_ratio
        if controls.max_co is not None
        else None
    )
    courant_ok = (
        observed_peak is not None
        and lower is not None
        and upper is not None
        and lower <= observed_peak <= upper
    )
    courant_check = PublicCheck(
        name="observed_courant",
        passed=courant_ok,
        detail=(
            "Observed maximum Courant number uses the adaptive target."
            if courant_ok
            else "Observed Courant evidence is missing, exceeds maxCo, or "
            "stays too far below maxCo; inspect maxDeltaT and the solver log."
        ),
        observed={"peak_max_courant": observed_peak},
        limits={
            "minimum": lower if lower is not None else "missing maxCo",
            "maximum": upper if upper is not None else "missing maxCo",
        },
    )

    expected = {
        "rarefaction": exact.left_wave.head_position_m,
        "contact": exact.contact_position_m,
        "shock": exact.right_wave.head_position_m,
    }
    observed = {
        "rarefaction": evidence.rarefaction_position_m,
        "contact": evidence.contact_position_m,
        "shock": evidence.shock_position_m,
    }
    tolerance = (
        policy.position_tolerance_cell_widths * evidence.cell_width_m
    )
    errors = {
        name: (
            abs(value - expected[name]) if value is not None else math.inf
        )
        for name, value in observed.items()
    }
    wave_ok = all(error <= tolerance for error in errors.values())
    wave_check = PublicCheck(
        name="analytical_wave_positions",
        passed=wave_ok,
        detail=(
            "Detected wave locations agree with the public exact Riemann "
            "solution."
            if wave_ok
            else "One or more detected waves lie outside the exact "
            "Riemann-solution tolerance; refine time/space numerics before "
            "accepting the run."
        ),
        observed={
            f"{name}_absolute_error_m": (
                error if math.isfinite(error) else None
            )
            for name, error in errors.items()
        },
        limits={"absolute_tolerance_m": tolerance},
    )
    return PublicCheckReport(
        family_id="rho-central-foam",
        checks=[time_check, courant_check, wave_check],
    )


def _matches_residual_field(observed: str, expected: str) -> bool:
    if observed == expected:
        return True
    return expected == "U" and observed in {"Ux", "Uy", "Uz"}


def check_buoyant_run(
    policy: BuoyantPolicy,
    *,
    run_facts: RunFacts,
    wall_heat_balance: WallHeatBalance | None,
) -> PublicCheckReport:
    """Check convergence, continuity, and actual transport-model heat flow."""

    field_diagnostics: dict[str, tuple[float, float, float]] = {}
    missing: list[str] = []
    for field in policy.residual_fields:
        values = [
            item.initial
            for item in run_facts.residuals
            if _matches_residual_field(item.field, field)
        ]
        if len(values) < 2:
            missing.append(field)
            continue
        window = min(policy.residual_window, max(1, len(values) // 2))
        initial_median = statistics.median(values[:window])
        terminal_median = statistics.median(values[-window:])
        ratio = (
            terminal_median / initial_median
            if initial_median > 0
            else math.inf
        )
        field_diagnostics[field] = (
            initial_median,
            terminal_median,
            ratio,
        )
    residual_ok = (
        not missing
        and bool(field_diagnostics)
        and all(
            terminal <= policy.max_terminal_initial_residual
            and ratio <= policy.max_terminal_to_initial_median_ratio
            for _, terminal, ratio in field_diagnostics.values()
        )
    )
    residual_check = PublicCheck(
        name="residual_trend",
        passed=residual_ok,
        detail=(
            "Required equation residuals decrease and finish below the "
            "public threshold."
            if residual_ok
            else "Residual evidence is missing or unconverged; reduce all "
            "required terminal residuals and confirm a decreasing trend. "
            f"Missing fields: {', '.join(missing) or 'none'}."
        ),
        observed={
            f"{field}_terminal_median": values[1]
            for field, values in field_diagnostics.items()
        }
        | {
            f"{field}_terminal_to_initial_ratio": values[2]
            for field, values in field_diagnostics.items()
        },
        limits={
            "max_terminal_initial_residual": (
                policy.max_terminal_initial_residual
            ),
            "max_terminal_to_initial_median_ratio": (
                policy.max_terminal_to_initial_median_ratio
            ),
        },
    )

    last_continuity = (
        run_facts.continuity[-1] if run_facts.continuity else None
    )
    continuity_ok = (
        last_continuity is not None
        and abs(last_continuity.local)
        <= policy.max_terminal_local_continuity_error
        and abs(last_continuity.cumulative)
        <= policy.max_abs_cumulative_continuity_error
    )
    continuity_check = PublicCheck(
        name="continuity_error",
        passed=continuity_ok,
        detail=(
            "Terminal local and cumulative continuity errors are acceptable."
            if continuity_ok
            else "Continuity evidence is missing or exceeds the public "
            "terminal limits; continue or stabilize the steady solve."
        ),
        observed={
            "terminal_local": (
                last_continuity.local if last_continuity else None
            ),
            "terminal_cumulative": (
                last_continuity.cumulative if last_continuity else None
            ),
        },
        limits={
            "max_terminal_local": (
                policy.max_terminal_local_continuity_error
            ),
            "max_abs_cumulative": (
                policy.max_abs_cumulative_continuity_error
            ),
        },
    )

    imbalance = (
        wall_heat_balance.normalized_imbalance
        if wall_heat_balance is not None
        else None
    )
    heat_ok = (
        imbalance is not None
        and math.isfinite(imbalance)
        and imbalance <= policy.max_wall_heat_imbalance
    )
    heat_check = PublicCheck(
        name="wall_heat_balance",
        passed=heat_ok,
        detail=(
            "Transport-model hot/cold integrated wall heat flow balances."
            if heat_ok
            else "Run Foundation wallHeatFlux on the converged result and "
            "reduce the integrated hot/cold energy imbalance."
        ),
        observed={"normalized_imbalance": imbalance},
        limits={
            "maximum": policy.max_wall_heat_imbalance,
            "quantity": "thermophysicalTransportModel wallHeatFlux Q",
        },
    )
    return PublicCheckReport(
        family_id="buoyant-foam",
        checks=[residual_check, continuity_check, heat_check],
    )
