"""Deterministic checks over provenance-bearing task drafts."""

from __future__ import annotations

from collections.abc import Mapping

from foampilot.environment import EnvironmentSnapshot
from foampilot.tasks import GeometryInput, MeshIntent

from .messages_zh import taskbuilder_message_zh
from .models import DraftIssue, DraftReview, FactSource, TaskDraft


def _issue(
    code: str,
    severity: str,
    field_path: str,
    *,
    detail: str | None = None,
) -> DraftIssue:
    message = taskbuilder_message_zh(code)
    return DraftIssue(
        code=code,
        severity=severity,
        field_path=field_path,
        message_zh=(
            message.message if detail is None else f"{message.message}{detail}"
        ),
        recovery_zh=message.recovery,
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def validate_task_draft(
    draft: TaskDraft,
    environment: EnvironmentSnapshot | None = None,
) -> DraftReview:
    """Separate blockers, confirmations and visible low-risk defaults."""

    facts = draft.fact_map()
    issues: list[DraftIssue] = []

    geometry_fact = facts.get("geometry")
    geometry = _mapping(geometry_fact.value) if geometry_fact is not None else {}
    if not geometry:
        issues.append(
            _issue("TASK_REQUEST_INCOMPLETE", "blocking", "geometry")
        )
    elif not geometry.get("length_unit"):
        issues.append(
            _issue("TASK_UNIT_AMBIGUOUS", "blocking", "geometry.length_unit")
        )
    if geometry:
        try:
            GeometryInput.model_validate(geometry)
        except ValueError:
            issues.append(
                _issue("TASK_REQUEST_INCOMPLETE", "blocking", "geometry")
            )
    mesh_fact = facts.get("mesh")
    if mesh_fact is not None:
        try:
            MeshIntent.model_validate(mesh_fact.value)
        except ValueError:
            issues.append(
                _issue("TASK_REQUEST_INCOMPLETE", "blocking", "mesh")
            )
    declared_assets = {item.path for item in draft.assets}
    geometry_assets = geometry.get("assets", [])
    if isinstance(geometry_assets, list):
        for item in geometry_assets:
            path = item.get("path") if isinstance(item, Mapping) else None
            if not isinstance(path, str) or path not in declared_assets:
                issues.append(
                    _issue(
                        "TASK_ASSET_UNRESOLVED",
                        "blocking",
                        "geometry.assets",
                    )
                )
                break

    for path in (
        "physics.regime",
        "physics.compressibility",
        "physics.phase_family",
    ):
        if path not in facts:
            issues.append(
                _issue("TASK_PHYSICS_AMBIGUOUS", "confirmable", path)
            )

    physics_family = (
        str(facts["physics.family"].value)
        if "physics.family" in facts
        else "fluid"
    )
    phase_family = (
        str(facts["physics.phase_family"].value)
        if "physics.phase_family" in facts
        else "unknown"
    )
    if physics_family in {"solid", "solid_mechanics"}:
        material_path = "materials.solid"
    else:
        material_path = "materials.fluid"
    if material_path not in facts:
        issues.append(
            _issue("TASK_REQUEST_INCOMPLETE", "blocking", material_path)
        )
    energy = (
        str(facts["physics.energy"].value)
        if "physics.energy" in facts
        else "unknown"
    )
    if energy == "enabled" and "materials.thermal" not in facts:
        issues.append(
            _issue(
                "TASK_REQUEST_INCOMPLETE",
                "blocking",
                "materials.thermal",
            )
        )
    if physics_family == "conjugate_heat_transfer":
        for path in ("materials.fluid", "materials.solid"):
            if path not in facts:
                issues.append(
                    _issue("TASK_REQUEST_INCOMPLETE", "blocking", path)
                )
        region_roles = geometry.get("region_roles", [])
        if not isinstance(region_roles, list) or len(region_roles) < 2:
            issues.append(
                _issue(
                    "TASK_REQUEST_INCOMPLETE",
                    "blocking",
                    "geometry.region_roles",
                )
            )
    if "boundaries" not in facts:
        issues.append(
            _issue("TASK_REQUEST_INCOMPLETE", "blocking", "boundaries")
        )
    if (
        facts.get("physics.regime") is not None
        and facts["physics.regime"].value == "transient"
        and "operating.end_time" not in facts
    ):
        issues.append(
            _issue(
                "TASK_REQUEST_INCOMPLETE",
                "blocking",
                "operating.end_time",
            )
        )
    if phase_family == "vof" and "initial.phase_fraction" not in facts:
        issues.append(
            _issue(
                "TASK_REQUEST_INCOMPLETE",
                "blocking",
                "initial.phase_fraction",
            )
        )

    for fact in draft.facts:
        if (
            not fact.confirmed
            and fact.impact in {"medium", "high"}
            and fact.source == FactSource.MODEL_INFERENCE
        ):
            issues.append(
                _issue("TASK_PHYSICS_AMBIGUOUS", "confirmable", fact.path)
            )

    for assumption in draft.assumptions:
        if (
            assumption.source == "model_inference"
            and assumption.impact in {"medium", "high"}
        ):
            issues.append(
                _issue(
                    "TASK_PHYSICS_AMBIGUOUS",
                    "confirmable",
                    assumption.path,
                )
            )

    for question in draft.unresolved_questions:
        issues.append(
            _issue(
                (
                    "TASK_REQUEST_INCOMPLETE"
                    if question.kind == "blocking"
                    else "TASK_PHYSICS_AMBIGUOUS"
                ),
                question.kind,
                question.path,
                detail=question.prompt_zh,
            )
        )

    solver = facts.get("physics.solver")
    if solver is not None and environment is not None:
        name = str(solver.value)
        if name not in environment.executable_names:
            issues.append(
                _issue(
                    "TASK_CAPABILITY_UNAVAILABLE",
                    "blocking",
                    "physics.solver",
                    detail=f"明确要求的 {name} 未被发现。",
                )
            )

    for path in (
        "resources.max_attempts",
        "resources.max_wall_seconds",
        "resources.max_mpi_ranks",
        "resources.memory_mib",
    ):
        if path not in facts:
            issues.append(
                DraftIssue(
                    code="TASK_DEFAULT_APPLIED",
                    severity="advisory",
                    field_path=path,
                    message_zh="未显式提供低风险运行预算，将在编译时使用可见默认值。",
                    recovery_zh="如需不同预算，请在草稿中显式设置该字段。",
                )
            )

    unique: list[DraftIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for item in issues:
        key = (item.code, item.severity, item.field_path)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    can_compile = (
        draft.status == "confirmed"
        and not any(
            item.severity in {"blocking", "confirmable"}
            for item in unique
        )
    )
    return DraftReview(
        draft=draft,
        issues=unique,
        can_compile=can_compile,
    )
