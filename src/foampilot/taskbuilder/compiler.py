"""Compile a confirmed draft into the one canonical TaskSpec."""

from __future__ import annotations

from hashlib import sha256
import json

from foampilot.tasks import TaskSpec

from .checks import build_public_checks
from .models import (
    DraftReview,
    TaskAssumption,
    TaskCompilation,
    TaskFact,
)


_RESOURCE_DEFAULTS = {
    "resources.max_attempts": 2,
    "resources.max_wall_seconds": 600,
    "resources.max_mpi_ranks": 1,
    "resources.memory_mib": 2048,
}


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _fact_value(facts: dict[str, TaskFact], path: str, default=None):
    fact = facts.get(path)
    return default if fact is None else fact.value


def _system_assumption(
    assumption_id: str,
    path: str,
    value,
    explanation: str,
) -> TaskAssumption:
    return TaskAssumption(
        assumption_id=assumption_id,
        path=path,
        value=value,
        source="system_default",
        impact="low",
        explanation_zh=explanation,
    )


def _resource_value(facts: dict[str, TaskFact], path: str) -> int:
    value = _fact_value(facts, path, _RESOURCE_DEFAULTS[path])
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"TASK_COMPILATION_FAILED: {path} must be an integer")
    return value


def _required_outputs(facts: dict[str, TaskFact]) -> list[str]:
    values = _fact_value(facts, "outputs.required", [])
    outputs = (
        [str(item).strip() for item in values if str(item).strip()]
        if isinstance(values, list)
        else []
    )
    metrics = _fact_value(facts, "outputs.metrics", [])
    if isinstance(metrics, list):
        for metric in metrics:
            if isinstance(metric, dict) and isinstance(metric.get("name"), str):
                outputs.append(f"{metric['name']} observation")
    return list(dict.fromkeys(["mesh quality report", "solver log", *outputs]))


def compile_task_draft(review: DraftReview) -> TaskCompilation:
    """Compile without solver routing, case authoring or native execution."""

    if not review.can_compile:
        raise ValueError(
            "TASK_COMPILATION_FAILED: blocking or confirmable issues remain"
        )
    draft = review.draft
    facts = draft.fact_map()
    checks, compiler_diagnostics = build_public_checks(facts)
    assumptions = list(draft.assumptions)

    distribution = _fact_value(facts, "openfoam.distribution", "foundation")
    version = _fact_value(facts, "openfoam.version", "10")
    if "openfoam.version" not in facts:
        assumptions.append(
            _system_assumption(
                "default-openfoam-version",
                "openfoam.version",
                "10",
                "使用 FoamPilot 当前验证目标 Foundation OpenFOAM v10。",
            )
        )
    for path, default in _RESOURCE_DEFAULTS.items():
        if path not in facts:
            assumptions.append(
                _system_assumption(
                    "default-" + path.replace(".", "-"),
                    path,
                    default,
                    "使用可见且有界的本地运行预算默认值。",
                )
            )

    geometry = _fact_value(facts, "geometry")
    mesh = _fact_value(facts, "mesh")
    if mesh is None:
        mesh = {"strategy": "auto"}
        assumptions.append(
            _system_assumption(
                "default-mesh-strategy",
                "mesh.strategy",
                "auto",
                "根据已经确认的 geometry mode 确定网格工具路线。",
            )
        )

    confirmed_facts = {
        path: {
            "value": fact.value,
            "source": fact.source.value,
            "evidence": fact.evidence,
        }
        for path, fact in sorted(facts.items())
    }
    prompt = (
        draft.request_text
        + "\n\n已确认任务事实（不得被模型改写）：\n"
        + json.dumps(confirmed_facts, ensure_ascii=False, sort_keys=True)
    )
    title = _fact_value(facts, "task.title")
    if not isinstance(title, str) or not title.strip():
        title = draft.request_text.splitlines()[0][:120].strip()

    acceptance = [
        "mesh passes the declared public quality requirements",
        "target solver completes normally",
        "required fields remain finite",
    ]
    if _fact_value(facts, "physics.regime") == "transient":
        end_time_fact = _fact_value(facts, "operating.end_time")
        end_time = (
            end_time_fact.get("value")
            if isinstance(end_time_fact, dict)
            else end_time_fact
        )
        acceptance.append(f"solver reaches the declared end time {end_time}")
    explicit_acceptance = _fact_value(facts, "acceptance.requirements", [])
    if isinstance(explicit_acceptance, list):
        acceptance.extend(
            str(item).strip() for item in explicit_acceptance if str(item).strip()
        )

    payload = {
        "schema_version": 2,
        "task_id": draft.draft_id,
        "title": title,
        "prompt": prompt,
        "openfoam_target": {
            "distribution": distribution,
            "version": str(version),
        },
        "resource_budget": {
            "max_attempts": _resource_value(facts, "resources.max_attempts"),
            "max_wall_seconds": _resource_value(
                facts, "resources.max_wall_seconds"
            ),
            "max_mpi_ranks": _resource_value(facts, "resources.max_mpi_ranks"),
            "memory_mib": _resource_value(facts, "resources.memory_mib"),
        },
        "required_outputs": _required_outputs(facts),
        "acceptance_requirements": list(dict.fromkeys(acceptance)),
        "public_checks": [item.model_dump(mode="json") for item in checks],
        "public_assets": [item.model_dump(mode="json") for item in draft.assets],
        "protected_paths": draft.protected_paths,
        "geometry": geometry,
        "mesh": mesh,
    }
    task = TaskSpec.model_validate(payload)
    return TaskCompilation(
        task=task,
        assumptions=assumptions,
        diagnostics=[*review.issues, *compiler_diagnostics],
        task_sha256=_canonical_hash(task.model_dump(mode="json")),
    )
