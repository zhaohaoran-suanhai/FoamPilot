"""Deterministic registry for supported public validation checks."""

from __future__ import annotations

from collections.abc import Mapping

from foampilot.tasks import PublicCheck

from .models import DraftIssue, TaskFact


def _value(facts: Mapping[str, TaskFact], path: str, default=None):
    fact = facts.get(path)
    return default if fact is None else fact.value


def _numeric_value(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, Mapping):
        nested = value.get("value")
        if isinstance(nested, (int, float)) and not isinstance(nested, bool):
            return float(nested)
    return None


def build_public_checks(
    facts: Mapping[str, TaskFact],
) -> tuple[list[PublicCheck], list[DraftIssue]]:
    """Return only check kinds implemented by the public evaluator."""

    checks = [
        PublicCheck(name="mesh-quality", kind="mesh_ok"),
        PublicCheck(name="normal-completion", kind="completion"),
        PublicCheck(name="finite-fields", kind="finite_fields"),
    ]
    diagnostics: list[DraftIssue] = []
    solver = _value(facts, "physics.solver")
    if isinstance(solver, str):
        checks.append(
            PublicCheck(
                name="target-solver-started",
                kind="command_executed",
                parameters={"executable": solver},
            )
        )

    if _value(facts, "physics.regime") == "transient":
        end_time = _numeric_value(_value(facts, "operating.end_time"))
        if end_time is not None:
            checks.append(
                PublicCheck(
                    name="final-time",
                    kind="final_time",
                    parameters={"minimum": end_time},
                )
            )

    phase_field = _value(facts, "physics.phase_field")
    if _value(facts, "physics.phase_family") == "vof" and isinstance(
        phase_field, str
    ):
        initial = _value(facts, "initial.phase_fraction", {})
        initial_map = initial if isinstance(initial, Mapping) else {}
        minimum = _numeric_value(initial_map.get("minimum", 0.0))
        maximum = _numeric_value(initial_map.get("maximum", 1.0))
        checks.append(
            PublicCheck(
                name="phase-fraction-bounds",
                kind="bounded_field",
                parameters={
                    "field": phase_field,
                    "minimum": 0.0 if minimum is None else minimum,
                    "maximum": 1.0 if maximum is None else maximum,
                },
            )
        )
        conservation = _numeric_value(
            _value(facts, "acceptance.conservation_max")
        )
        if conservation is not None:
            checks.append(
                PublicCheck(
                    name="phase-conservation",
                    kind="conservation",
                    parameters={
                        "field": phase_field,
                        "maximum_normalized_error": conservation,
                    },
                )
            )

    paths = _value(facts, "outputs.paths", [])
    if isinstance(paths, list):
        for index, path in enumerate(paths, start=1):
            if isinstance(path, str) and path:
                checks.append(
                    PublicCheck(
                        name=f"requested-output-{index}",
                        kind="requested_output",
                        parameters={"path": path},
                    )
                )

    metrics = _value(facts, "outputs.metrics", [])
    if isinstance(metrics, list):
        for metric in metrics:
            if not isinstance(metric, Mapping):
                continue
            name = metric.get("name")
            if isinstance(name, str) and "tolerance" not in metric:
                diagnostics.append(
                    DraftIssue(
                        code="TASK_METRIC_TOLERANCE_MISSING",
                        severity="advisory",
                        field_path=f"outputs.metrics.{name}",
                        message_zh=(
                            f"指标 {name} 未声明工程容差，仅作为观测输出。"
                        ),
                        recovery_zh=(
                            "如需据此判定通过，请显式提供定义、参考值和容差。"
                        ),
                    )
                )
    return checks, diagnostics
