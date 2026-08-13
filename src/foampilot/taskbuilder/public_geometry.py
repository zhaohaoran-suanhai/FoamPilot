"""Deterministic reconciliation for verified public geometry files."""

from __future__ import annotations

from pathlib import Path

from foampilot.tasks import PublicAsset

from .authority import geometry_component_supported, verified_user_evidence
from .context import TaskIngressContext
from .models import FactSource, TaskFact, TaskQuestion


_PUBLIC_FILE_GEOMETRY = {
    ".stl": ("surface", "stl", "surface_geometry"),
    ".obj": ("surface", "obj", "surface_geometry"),
    ".geo": ("gmsh", "geo", "gmsh_geometry"),
}


def reconcile_public_geometry(
    *,
    facts: list[TaskFact],
    questions: list[TaskQuestion],
    assets: list[PublicAsset],
    context: TaskIngressContext,
    request: str,
) -> tuple[list[TaskFact], list[TaskQuestion]]:
    """Mint file-asset geometry authority without trusting model labels."""

    if context.poly_mesh_topologies:
        return facts, questions
    verified_paths = {
        bundle.source_path
        for bundle in context.asset_bundles
        if bundle.kind == "public_file"
    }
    declared = [item for item in assets if item.path in verified_paths]
    recognized = [
        (item, _PUBLIC_FILE_GEOMETRY.get(Path(item.path).suffix.casefold()))
        for item in declared
    ]
    recognized = [(item, route) for item, route in recognized if route is not None]
    if not recognized:
        return facts, questions
    modes = {route[0] for _, route in recognized}
    if len(modes) != 1:
        return facts, questions

    by_path = {item.path: item for item in facts}
    previous = by_path.get("geometry")
    previous_value = (
        previous.value
        if previous is not None and isinstance(previous.value, dict)
        else {}
    )
    evidence_is_user = previous is not None and verified_user_evidence(
        previous.evidence, request
    )
    source = (
        FactSource.USER_CONFIRMATION
        if previous is not None
        and previous.source == FactSource.USER_CONFIRMATION
        else FactSource.USER_TEXT
    )
    trusted = source == FactSource.USER_CONFIRMATION
    invalid_components: list[str] = []
    for component in (
        "length_unit",
        "dimensionality",
        "patch_roles",
        "region_roles",
    ):
        value = previous_value.get(component)
        if value in (None, []) or previous is None:
            continue
        supported = trusted or (
            evidence_is_user
            and (
                geometry_component_supported(
                    value,
                    previous.evidence,
                    trusted_confirmation=False,
                )
                if not isinstance(value, list)
                else all(
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("role"), str)
                    and geometry_component_supported(
                        item["name"],
                        previous.evidence,
                        trusted_confirmation=False,
                    )
                    and geometry_component_supported(
                        item["role"],
                        previous.evidence,
                        trusted_confirmation=False,
                    )
                    for item in value
                )
            )
        )
        if supported:
            by_path[f"geometry.{component}"] = TaskFact(
                path=f"geometry.{component}",
                value=value,
                source=source,
                evidence=previous.evidence,
                impact="high",
                confirmed=True,
            )
        elif value not in (None, []):
            invalid_components.append(component)
    mode = next(iter(modes))
    asset_refs = [
        {"path": item.path, "format": route[1], "role": route[2]}
        for item, route in recognized
    ]
    authority = "; ".join(
        f"{item.path} sha256 {item.sha256}" for item, _ in recognized
    )
    by_path["geometry"] = TaskFact(
        path="geometry",
        value={
            "mode": mode,
            "dimensionality": None,
            "description": f"user-declared public {mode} geometry",
            "length_unit": None,
            "assets": asset_refs,
            "patch_roles": [],
            "region_roles": [],
        },
        source=FactSource.PUBLIC_ASSET,
        evidence=authority,
        impact="high",
        confirmed=True,
    )
    existing_mesh = by_path.get("mesh")
    explicit_mesh_strategy = (
        existing_mesh.value.get("strategy")
        if existing_mesh is not None
        and existing_mesh.confirmed
        and existing_mesh.source
        in {FactSource.USER_TEXT, FactSource.USER_CONFIRMATION}
        and isinstance(existing_mesh.value, dict)
        else None
    )
    compatible = (
        {"auto", "gmsh"}
        if mode == "gmsh"
        else {"auto", "snappyHexMesh", "gmsh", "provided"}
    )
    has_provided_mesh = any(
        item.kind == "directory"
        and item.install_path is not None
        and Path(item.install_path).name == "polyMesh"
        for item in assets
    )
    mesh_conflict = (
        explicit_mesh_strategy is not None
        and (
            explicit_mesh_strategy not in compatible
            or (
                explicit_mesh_strategy == "provided"
                and not has_provided_mesh
            )
        )
    )
    if mode == "gmsh" and not mesh_conflict:
        by_path["mesh"] = TaskFact(
            path="mesh",
            value={"strategy": "gmsh"},
            source=FactSource.PUBLIC_ASSET,
            evidence=authority,
            impact="high",
            confirmed=True,
        )
    elif mode == "surface" and explicit_mesh_strategy is None:
        by_path.pop("mesh", None)

    input_questions = [
        TaskQuestion(
            question_id=f"q_geometry_{component}_conflict",
            path=f"geometry.{component}",
            kind="blocking",
            prompt_zh="用户声明的几何角色结构无法安全绑定到公开几何资产。",
            reason_zh="角色信息必须具有 name/role 结构并能逐项绑定到用户证据。",
        )
        for component in invalid_components
    ]
    if mesh_conflict:
        input_questions.append(
            TaskQuestion(
                question_id="q_mesh_conflict",
                path="mesh",
                kind="blocking",
                prompt_zh="用户声明的网格策略与公开几何资产类型冲突。",
                reason_zh="相互矛盾的权威输入必须由用户修正，不能静默覆盖。",
            )
        )
    return [by_path[path] for path in sorted(by_path)], input_questions


__all__ = ["reconcile_public_geometry"]
