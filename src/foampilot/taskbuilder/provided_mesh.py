"""Deterministic reconciliation for declared OpenFOAM polyMesh assets."""

from __future__ import annotations

from collections import Counter

from foampilot.tasks import PublicAsset

from .authority import geometry_component_supported, verified_user_evidence
from .context import TaskIngressContext
from .models import FactSource, TaskFact, TaskQuestion


def reconcile_provided_mesh(
    *,
    facts: list[TaskFact],
    questions: list[TaskQuestion],
    assets: list[PublicAsset],
    context: TaskIngressContext,
    request: str,
) -> tuple[list[TaskFact], list[TaskQuestion]]:
    """Reconcile model output with immutable native-mesh authority."""

    topology_by_manifest = {
        item.bundle_manifest_sha256: item
        for item in context.poly_mesh_topologies
    }
    provided = [
        item
        for item in assets
        if item.kind == "directory"
        and item.bundle_manifest_sha256 in topology_by_manifest
    ]
    if not provided:
        return facts, [
            item
            for item in questions
            if item.path
            in {
                "geometry",
                "geometry.dimensionality",
                "geometry.length_unit",
            }
        ]

    by_path = {item.path: item for item in facts}
    previous_geometry = by_path.get("geometry")
    previous_value = (
        previous_geometry.value
        if previous_geometry is not None
        and isinstance(previous_geometry.value, dict)
        else {}
    )
    user_source = (
        previous_geometry.source
        if previous_geometry is not None
        and previous_geometry.source
        in {FactSource.USER_TEXT, FactSource.USER_CONFIRMATION}
        else None
    )
    if (
        user_source is None
        and previous_geometry is not None
        and verified_user_evidence(previous_geometry.evidence, request)
    ):
        user_source = FactSource.USER_TEXT
    trusted_confirmation = user_source == FactSource.USER_CONFIRMATION
    unit = (
        previous_value.get("length_unit")
        if previous_geometry is not None
        and user_source is not None
        and geometry_component_supported(
            previous_value.get("length_unit"),
            previous_geometry.evidence,
            trusted_confirmation=trusted_confirmation,
        )
        else None
    )
    if unit is not None and previous_geometry is not None:
        by_path["geometry.length_unit"] = TaskFact(
            path="geometry.length_unit",
            value=unit,
            source=previous_geometry.source,
            evidence=previous_geometry.evidence,
            impact="high",
            confirmed=True,
        )

    selected_topologies = [
        topology_by_manifest[item.bundle_manifest_sha256]
        for item in provided
        if item.bundle_manifest_sha256 is not None
    ]
    has_empty_patch = any(
        patch.patch_type == "empty"
        for topology in selected_topologies
        for patch in topology.patches
    )
    declared_dimensionality = previous_value.get("dimensionality")
    user_dimensionality = (
        declared_dimensionality
        if declared_dimensionality in {
            "two_d",
            "axisymmetric",
            "three_d",
        }
        and previous_geometry is not None
        and user_source is not None
        and geometry_component_supported(
            declared_dimensionality,
            previous_geometry.evidence,
            trusted_confirmation=trusted_confirmation,
        )
        else None
    )
    if user_dimensionality is not None:
        by_path["geometry.dimensionality"] = TaskFact(
            path="geometry.dimensionality",
            value=user_dimensionality,
            source=previous_geometry.source,
            evidence=previous_geometry.evidence,
            impact="high",
            confirmed=True,
        )
    patch_name_counts = Counter(
        patch.name
        for topology in selected_topologies
        for patch in topology.patches
    )
    region_name_counts = Counter(
        name
        for topology in selected_topologies
        for name in (
            *((topology.region,) if topology.region is not None else ()),
            *(zone.name for zone in topology.cell_zones),
        )
    )
    invalid_role_components: list[str] = []
    for component in ("patch_roles", "region_roles"):
        component_value = previous_value.get(component)
        if not component_value or user_source is None or previous_geometry is None:
            continue
        well_formed = isinstance(component_value, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("role"), str)
            for item in component_value
        )
        supported = well_formed and all(
            isinstance(item, dict)
            and geometry_component_supported(
                item.get("name"),
                previous_geometry.evidence,
                trusted_confirmation=trusted_confirmation,
            )
            and geometry_component_supported(
                item.get("role"),
                previous_geometry.evidence,
                trusted_confirmation=trusted_confirmation,
            )
            for item in component_value
        )
        name_counts = (
            patch_name_counts
            if component == "patch_roles"
            else region_name_counts
        )
        names_exist = well_formed and all(
            name_counts[item["name"]] == 1 for item in component_value
        )
        if supported and names_exist:
            by_path[f"geometry.{component}"] = TaskFact(
                path=f"geometry.{component}",
                value=component_value,
                source=user_source,
                evidence=previous_geometry.evidence,
                impact="high",
                confirmed=True,
            )
        elif component_value:
            invalid_role_components.append(component)
    dimensionality = "two_d" if has_empty_patch else None
    evidence = "; ".join(
        f"{item.path} manifest {item.bundle_manifest_sha256}"
        for item in provided
    )
    geometry = {
        "mode": "openfoam_mesh",
        "dimensionality": dimensionality,
        "description": "user-declared native OpenFOAM polyMesh",
        "length_unit": None,
        "assets": [
            {
                "path": item.path,
                "format": "openfoam_mesh",
                "role": "volume_mesh",
            }
            for item in provided
        ],
        "patch_roles": [],
        "region_roles": [],
    }
    by_path["geometry"] = TaskFact(
        path="geometry",
        value=geometry,
        source=FactSource.PUBLIC_ASSET,
        evidence=evidence,
        impact="high",
        confirmed=True,
    )
    by_path["mesh"] = TaskFact(
        path="mesh",
        value={"strategy": "provided"},
        source=FactSource.PUBLIC_ASSET,
        evidence=evidence,
        impact="high",
        confirmed=True,
    )

    input_questions: list[TaskQuestion] = []
    for component in invalid_role_components:
        input_questions.append(
            TaskQuestion(
                question_id=f"q_geometry_{component}_conflict",
                path=f"geometry.{component}",
                kind="blocking",
                prompt_zh=(
                    "用户声明的角色名称未出现在已检查的 polyMesh "
                    f"{component} 名称集合中。"
                ),
                reason_zh="角色映射必须与权威网格 topology 精确对应。",
            )
        )
    if (
        has_empty_patch
        and user_dimensionality is not None
        and user_dimensionality != "two_d"
    ):
        input_questions.append(
            TaskQuestion(
                question_id="q_geometry_dimensionality_conflict",
                path="geometry.dimensionality",
                kind="blocking",
                prompt_zh=(
                    "用户声明的求解维度与网格中的 empty patch 约束冲突；"
                    "请确认应修正用户声明还是更换网格。"
                ),
                reason_zh=(
                    "FoamPilot 不会静默覆盖相互矛盾的用户语义与网格事实。"
                ),
            )
        )
    if unit is None:
        input_questions.append(
            TaskQuestion(
                question_id="q_geometry_length_unit",
                path="geometry.length_unit",
                kind="blocking",
                prompt_zh="该原生 polyMesh 的坐标长度单位是什么？",
                reason_zh=(
                    "patch、zone 与拓扑不依赖单位，但物理尺度、Reynolds 数、"
                    "时间步和模型参数不能在未知单位时可靠设计。"
                ),
            )
        )
    if dimensionality is None and user_dimensionality is None:
        input_questions.append(
            TaskQuestion(
                question_id="q_geometry_dimensionality",
                path="geometry.dimensionality",
                kind="blocking",
                prompt_zh="该网格应按二维、轴对称还是三维求解？",
                reason_zh="网格拓扑和已确认用户文本未给出唯一维度解释。",
            )
        )
    return [by_path[path] for path in sorted(by_path)], input_questions


__all__ = ["reconcile_provided_mesh"]
