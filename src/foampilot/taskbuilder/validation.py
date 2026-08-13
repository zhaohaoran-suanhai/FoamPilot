"""Deterministic checks over provenance-bearing task drafts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from foampilot.tasks import GeometryInput, MeshIntent

from .messages_zh import taskbuilder_message_zh
from .models import DraftIssue, DraftReview, TaskDraft
from .projection import compilable_fact_map, effective_geometry_value


_INPUT_QUESTION_PATHS = {
    "geometry",
    "geometry.dimensionality",
    "geometry.length_unit",
    "geometry.patch_roles",
    "geometry.region_roles",
    "mesh",
}


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
    environment: object | None = None,
) -> DraftReview:
    """Separate blockers, confirmations and visible low-risk defaults."""

    del environment  # Capability availability belongs to the later RiskGate.
    facts = compilable_fact_map(draft)
    issues: list[DraftIssue] = []

    geometry = _mapping(effective_geometry_value(facts))
    if not geometry:
        issues.append(
            _issue("TASK_REQUEST_INCOMPLETE", "blocking", "geometry")
        )
    elif not geometry.get("length_unit"):
        issues.append(
            _issue("TASK_UNIT_AMBIGUOUS", "blocking", "geometry.length_unit")
        )
    if geometry and geometry.get("length_unit"):
        try:
            GeometryInput.model_validate(geometry)
        except ValueError:
            issues.append(
                _issue("TASK_REQUEST_INCOMPLETE", "blocking", "geometry")
            )
    mesh_fact = facts.get("mesh")
    if mesh_fact is None:
        if geometry.get("mode") == "openfoam_mesh":
            issues.append(
                _issue("TASK_REQUEST_INCOMPLETE", "blocking", "mesh")
            )
    else:
        try:
            mesh = MeshIntent.model_validate(mesh_fact.value)
        except ValueError:
            issues.append(
                _issue("TASK_REQUEST_INCOMPLETE", "blocking", "mesh")
            )
        else:
            compatible = {
                "openfoam_mesh": {"provided"},
                "surface": {"auto", "snappyHexMesh", "gmsh", "provided"},
                "gmsh": {"auto", "gmsh"},
                "parametric": {"auto", "blockMesh", "gmsh"},
            }.get(str(geometry.get("mode")))
            if compatible is not None and mesh.strategy not in compatible:
                issues.append(
                    _issue("TASK_REQUEST_INCOMPLETE", "blocking", "mesh")
                )
            if mesh.strategy == "provided" and not any(
                item.kind == "directory"
                and item.install_path is not None
                and PurePosixPath(item.install_path).name == "polyMesh"
                for item in draft.assets
            ):
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

    for question in draft.unresolved_questions:
        if question.path not in _INPUT_QUESTION_PATHS:
            continue
        if question.path == "geometry.length_unit" and not geometry.get(
            "length_unit"
        ):
            continue
        if question.path == "geometry" and not geometry:
            continue
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
    can_compile = not any(
        item.severity in {"blocking", "confirmable"}
        for item in unique
    )
    return DraftReview(
        draft=draft,
        issues=unique,
        can_compile=can_compile,
    )
