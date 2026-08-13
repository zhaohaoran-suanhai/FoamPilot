"""Deterministic policy for rebuilding unresolved input questions."""

from __future__ import annotations

from foampilot.tasks import GeometryInput, MeshIntent, PublicAsset

from .models import TaskFact, TaskQuestion
from .projection import compilable_fact_map_from_facts, effective_geometry_value


INPUT_QUESTION_PATHS = frozenset(
    {
        "geometry",
        "geometry.dimensionality",
        "geometry.length_unit",
        "geometry.patch_roles",
        "geometry.region_roles",
        "mesh",
    }
)


def rebuild_input_questions(
    facts: list[TaskFact],
    questions: list[TaskQuestion],
    assets: list[PublicAsset],
) -> list[TaskQuestion]:
    """Make the serialized draft state match deterministic input authority."""

    authoritative = compilable_fact_map_from_facts(facts)
    geometry = effective_geometry_value(authoritative)
    by_path = {item.path: item for item in questions}
    deterministic_conflicts = {
        item.path
        for item in questions
        if item.question_id
        in {
            "q_geometry_dimensionality_conflict",
            "q_geometry_patch_roles_conflict",
            "q_geometry_region_roles_conflict",
            "q_mesh_conflict",
        }
    }
    result: list[TaskQuestion] = []
    if not geometry and "geometry" not in by_path:
        result.append(
            TaskQuestion(
                question_id="q_geometry_input",
                path="geometry",
                kind="blocking",
                prompt_zh="请提供或声明用于本任务的几何或网格输入。",
                reason_zh="后续工程设计不能在没有权威几何输入时创建计算域。",
            )
        )
        return result
    if (
        geometry
        and "geometry.length_unit" not in authoritative
        and not geometry.get("length_unit")
        and "geometry.length_unit" not in by_path
    ):
        result.append(
            TaskQuestion(
                question_id="q_geometry_length_unit",
                path="geometry.length_unit",
                kind="blocking",
                prompt_zh="该几何或网格坐标的长度单位是什么？",
                reason_zh="物理尺度不能由坐标值或文件格式安全推断。",
            )
        )
    if (
        geometry
        and not geometry.get("dimensionality")
        and "geometry.dimensionality" not in by_path
    ):
        result.append(
            TaskQuestion(
                question_id="q_geometry_dimensionality",
                path="geometry.dimensionality",
                kind="blocking",
                prompt_zh="该几何或网格应按二维、轴对称还是三维求解？",
                reason_zh="求解维度不能由不完整的模型输出安全推断。",
            )
        )
    declared_assets = {item.path for item in assets}
    if geometry:
        referenced = {
            item.get("path")
            for item in geometry.get("assets", [])
            if isinstance(item, dict)
        }
        invalid_geometry = bool(referenced - declared_assets)
        if not invalid_geometry and geometry.get("length_unit") and geometry.get(
            "dimensionality"
        ):
            try:
                GeometryInput.model_validate(geometry)
            except ValueError:
                invalid_geometry = True
        if invalid_geometry and "geometry" not in by_path:
            result.append(
                TaskQuestion(
                    question_id="q_geometry_invalid",
                    path="geometry",
                    kind="blocking",
                    prompt_zh="几何输入与已声明资产或输入合同不一致。",
                    reason_zh="请修正几何声明后再继续，系统不会泛化放行。",
                )
            )
    mesh_fact = authoritative.get("mesh")
    if mesh_fact is not None:
        try:
            MeshIntent.model_validate(mesh_fact.value)
        except ValueError:
            if "mesh" not in by_path:
                result.append(
                    TaskQuestion(
                        question_id="q_mesh_invalid",
                        path="mesh",
                        kind="blocking",
                        prompt_zh="网格策略不符合当前输入合同。",
                        reason_zh="请修正网格策略声明后再继续。",
                    )
                )
    generated_paths = {item.path for item in result}
    for path in sorted(by_path):
        if path in generated_paths:
            continue
        item = by_path[path]
        is_conflict = path in deterministic_conflicts
        if (
            path == "geometry.length_unit"
            and geometry.get("length_unit")
            and not is_conflict
        ):
            continue
        if (
            path == "geometry.dimensionality"
            and geometry.get("dimensionality")
            and not is_conflict
        ):
            continue
        if path == "geometry" and geometry and not is_conflict:
            continue
        canonical_id = "q_" + path.replace(".", "_")
        if is_conflict:
            canonical_id += "_conflict"
        result.append(item.model_copy(update={"question_id": canonical_id}))
    return result


__all__ = ["INPUT_QUESTION_PATHS", "rebuild_input_questions"]
