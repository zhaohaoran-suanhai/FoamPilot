"""Model-backed extraction without execution authority."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.tasks import PublicAsset

from .models import (
    FactSource,
    TaskAssumption,
    TaskDraft,
    TaskFact,
    TaskQuestion,
)
from .context import TaskIngressContext


ExtractableFactPath = Literal[
    "task.title",
    "openfoam.distribution",
    "openfoam.version",
    "physics.family",
    "physics.regime",
    "physics.compressibility",
    "physics.phase_family",
    "physics.energy",
    "physics.turbulence",
    "physics.solver",
    "geometry",
    "mesh",
    "materials.fluid",
    "materials.solid",
    "materials.thermal",
    "boundaries",
    "initial.conditions",
    "initial.phase_fraction",
    "operating.end_time",
    "operating.time_step",
    "operating.write_interval",
    "outputs.required",
    "outputs.metrics",
    "outputs.paths",
    "acceptance.requirements",
    "acceptance.conservation_max",
    "resources.max_attempts",
    "resources.max_wall_seconds",
    "resources.max_mpi_ranks",
    "resources.memory_mib",
]

_INPUT_QUESTION_PATHS = {
    "geometry",
    "geometry.dimensionality",
    "geometry.length_unit",
    "geometry.patch_roles",
    "geometry.region_roles",
    "mesh",
}


class _ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: ExtractableFactPath
    value_json: str = Field(alias="value")
    source: FactSource
    evidence: str
    impact: Literal["low", "medium", "high"]
    confirmed: bool = False

    @field_validator("value_json")
    @classmethod
    def validate_json_text(cls, value: str) -> str:
        try:
            json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError("fact value must be valid JSON text") from error
        return value


class _ExtractedAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    assumption_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
    value_json: str = Field(alias="value")
    source: Literal["system_default", "model_inference"]
    impact: Literal["low", "medium", "high"]
    explanation_zh: str

    @field_validator("value_json")
    @classmethod
    def validate_json_text(cls, value: str) -> str:
        try:
            json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError("assumption value must be valid JSON text") from error
        return value


class _ExtractedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    question_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
    kind: Literal["blocking", "confirmable"]
    prompt_zh: str
    reason_zh: str
    candidate_json: str | None = Field(default=None, alias="candidate")
    evidence: str | None = None

    @field_validator("candidate_json")
    @classmethod
    def validate_candidate_json(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError("question candidate must be valid JSON text") from error
        return value


class _ExtractedTaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    facts: list[_ExtractedFact] = Field(default_factory=list)
    assumptions: list[_ExtractedAssumption] = Field(default_factory=list)
    unresolved_questions: list[_ExtractedQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_identifiers(self):
        assumption_ids = [item.assumption_id for item in self.assumptions]
        if len(assumption_ids) != len(set(assumption_ids)):
            raise ValueError("duplicate assumption IDs are not allowed")
        question_ids = [
            item.question_id for item in self.unresolved_questions
        ]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate question IDs are not allowed")
        return self


def _normalized_extracted_facts(
    facts: list[_ExtractedFact],
) -> list[_ExtractedFact]:
    """Collapse harmless repeats and fail closed on conflicting values."""

    by_path: dict[str, list[_ExtractedFact]] = {}
    for item in facts:
        by_path.setdefault(item.path, []).append(item)
    normalized: list[_ExtractedFact] = []
    for path in sorted(by_path):
        candidates = by_path[path]
        signatures = {
            (
                json.dumps(
                    json.loads(item.value_json),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                item.source,
                item.impact,
            )
            for item in candidates
        }
        if len(signatures) == 1:
            normalized.append(
                min(candidates, key=lambda item: (len(item.evidence), item.evidence))
            )
            continue
        normalized.append(
            candidates[0].model_copy(
                update={
                    "source": FactSource.MODEL_INFERENCE,
                    "evidence": f"conflicting duplicate model facts for {path}",
                    "confirmed": False,
                }
            )
        )
    return normalized


_SYSTEM_PROMPT = """你只负责把一段公开 CFD 请求提取为结构化 TaskDraft 事实。
不得编写 OpenFOAM case，不得调用工具，不得读取 tutorial、golden、私有 evaluator 或宿主机路径。
不得虚构物性、单位、边界数值、初始条件、终止时间、工程容差或 solver 能力。
用户文本中明确出现的事实标为 user_text，并保留最短证据；附件 metadata 中的事实标为
public_asset。任何解释性推断必须标为 model_inference 且 confirmed=false。只把后续工程设计无法
安全提出候选的输入权威缺口放入 unresolved_questions，例如未知几何长度单位、未声明的资产或
无法确定的几何维度。不要追问 solver、物性候选、边界数值、时间步、终止时间、输出路径或资源
预算；这些属于后续 CaseDesigner 和 RiskGate。不要用 assumption 补齐。fact/assumption 的 value 与 question 的非空
candidate 字段必须是 JSON 文本：字符串值也要编码为 JSON 字符串，对象和数组编码为 JSON
object/array 文本。fact.path 只能使用以下小写路径词表，不得创造带大写字段名的路径：
task.title、openfoam.distribution、openfoam.version、physics.family、physics.regime、
physics.compressibility、physics.phase_family、physics.energy、physics.turbulence、physics.solver、
geometry、mesh、materials.fluid、materials.solid、materials.thermal、boundaries、
initial.conditions、initial.phase_fraction、operating.end_time、operating.time_step、
operating.write_interval、outputs.required、outputs.metrics、outputs.paths、
acceptance.requirements、acceptance.conservation_max、resources.max_attempts、
resources.max_wall_seconds、resources.max_mpi_ranks、resources.memory_mib。
例如 U/p 初值应合并到 initial.conditions 的 JSON object，不得写 initial_conditions.U。
机器枚举值不得翻译成中文：openfoam.distribution 使用 "foundation"，openfoam.version 使用
"10"；physics.family 使用 "fluid"/"solid"，physics.regime 只能是 "steady" 或 "transient"，
physics.compressibility 使用 "incompressible"/"compressible"，physics.phase_family 使用
"single_phase"/"vof"/"multiphase"，physics.energy 使用 "disabled"/"enabled"。
geometry 必须直接符合 GeometryInput：只允许 mode、dimensionality、description、length_unit、
assets、parameters、patch_roles、region_roles；参数化几何使用 mode="parametric"，二维使用
dimensionality="two_d"，尺寸放入 parameters，例如
{"width":{"value":0.1,"unit":"m"}}，不得直接添加 shape/width/height 等字段。
patch_roles 必须是 object list，例如 [{"name":"top","role":"wall"}]，role 只使用
inlet/outlet/wall/opening/symmetry/empty/interface/other；region_roles 同样是 object list。
patch name 不得使用中文，只能使用 OpenFOAM 安全的 ASCII 名称，例如 top、fixedWalls、
frontAndBack；boundaries 中引用相同 patch 时也沿用这些 ASCII 名称。
mesh 必须直接符合 MeshIntent：只允许 strategy、target_cell_size、target_cell_count、
refinement_regions、boundary_layers、quality；blockMesh 使用 strategy="blockMesh"，总单元数范围
使用 target_cell_count={"min":整数,"max":整数}，不得添加 generator/cells/grading 字段。
quality 只允许 require_check_mesh_pass、max_non_orthogonality、max_skewness；boundary_layers 只允许
enabled、patches、layer_count；enabled=false 时必须使用 layer_count=null，不能用 0。
20×20×1 等不能无损放入 MeshIntent 的细节留在原 request_text，
不得塞入 quality 或创造 spanwise_cell_count/distribution 字段。
带单位的 end_time/time_step/write_interval 使用 {"value": 数值, "unit": 单位}，不得把起止
时间合并进 end_time。不要把 pRefCell、reference cell、reference point 等 OpenFOAM 实现选择
当作用户缺失的物理输入；用户已要求一致压力参考时，把该意图保留在 boundaries，由 case author
选择有效实现。
只返回请求 schema。"""


_VALUE_ALIASES = {
    "steady": ("steady", "稳态"),
    "transient": ("transient", "瞬态", "非稳态"),
    "incompressible": ("incompressible", "不可压缩"),
    "compressible": ("compressible", "可压缩"),
    "single_phase": ("single_phase", "single phase", "单相"),
    "multiphase": ("multiphase", "multi-phase", "多相"),
    "laminar": ("laminar", "层流"),
    "two_d": ("two_d", "two-dimensional", "2d", "二维"),
    "three_d": ("three_d", "three-dimensional", "3d", "三维"),
    "axisymmetric": ("axisymmetric", "轴对称"),
    "inlet": ("inlet", "入口"),
    "outlet": ("outlet", "出口"),
    "wall": ("wall", "壁面", "墙面"),
    "symmetry": ("symmetry", "对称"),
    "empty": ("empty", "二维前后"),
    "fluid": ("fluid", "流体"),
    "solid": ("solid", "固体"),
    "porous": ("porous", "多孔"),
}

_EXCLUSIVE_VALUE_GROUPS = (
    frozenset({"steady", "transient"}),
    frozenset({"incompressible", "compressible"}),
    frozenset({"single_phase", "multiphase"}),
    frozenset({"two_d", "three_d", "axisymmetric"}),
)
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    r"(?![A-Za-z0-9_.])"
)
_NEGATION_PREFIX = re.compile(
    r"(?:not(?:\s+(?:an?|the))?|no|non|without)\s*$",
    flags=re.IGNORECASE,
)


def _text_has_alias(text: str, alias: str) -> bool:
    if alias.isascii():
        for match in re.finditer(
            rf"(?<![A-Za-z0-9_]){re.escape(alias)}"
            rf"(?![A-Za-z0-9_])",
            text,
            flags=re.IGNORECASE,
        ):
            if not _NEGATION_PREFIX.search(text[max(0, match.start() - 16) : match.start()]):
                return True
        return False
    start = 0
    while (index := text.find(alias, start)) >= 0:
        prefix = text[max(0, index - 4) : index]
        if not any(marker in prefix for marker in ("不", "非", "无", "未")):
            return True
        start = index + 1
    return False


def _text_has_value(text: str, value: object) -> bool:
    if isinstance(value, str):
        for group in _EXCLUSIVE_VALUE_GROUPS:
            if value not in group:
                continue
            if any(
                _text_has_alias(text, alias)
                for other in group - {value}
                for alias in _VALUE_ALIASES.get(other, (other,))
            ):
                return False
        candidates = _VALUE_ALIASES.get(value, (value,))
        return any(_text_has_alias(text, candidate) for candidate in candidates)
    if isinstance(value, bool):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9_]){str(value)}(?![A-Za-z0-9_])",
                text,
                flags=re.IGNORECASE,
            )
        )
    if isinstance(value, (int, float)):
        try:
            expected = Decimal(str(value))
        except InvalidOperation:
            return False
        if not expected.is_finite():
            return False
        for match in _NUMBER_TOKEN.finditer(text):
            try:
                observed = Decimal(match.group())
            except InvalidOperation:
                continue
            if observed.is_finite() and observed == expected:
                return True
        return False
    return value is None


def _scalar_leaves(value: object):
    if isinstance(value, dict):
        for item in value.values():
            yield from _scalar_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scalar_leaves(item)
    elif isinstance(value, (str, int, float, bool)):
        yield value


_NON_SEMANTIC_KEYS = {
    "value",
    "unit",
    "condition",
    "type",
    "model",
    "role",
    "strategy",
    "enabled",
}


def _semantic_keys(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in _NON_SEMANTIC_KEYS:
                yield key
            yield from _semantic_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _semantic_keys(item)


def _user_fact_value_supported(path: str, value: object, evidence: str) -> bool:
    """Conservatively bind a model-extracted value to its quoted evidence."""

    if path == "geometry" and isinstance(value, dict):
        checks: list[bool] = []
        for key in ("dimensionality", "length_unit"):
            if value.get(key) is not None:
                checks.append(_text_has_value(evidence, value[key]))
        for role_key in ("patch_roles", "region_roles"):
            for item in value.get(role_key, []) or []:
                if isinstance(item, dict):
                    checks.extend(
                        (
                            _text_has_value(evidence, item.get("name")),
                            _text_has_value(evidence, item.get("role")),
                        )
                    )
        for name, parameter in (value.get("parameters") or {}).items():
            if isinstance(parameter, dict):
                checks.extend(
                    (
                        _text_has_value(evidence, name),
                        _text_has_value(evidence, parameter.get("value")),
                        _text_has_value(evidence, parameter.get("unit")),
                    )
                )
        return bool(checks) and all(checks)
    if isinstance(value, (list, dict)):
        scalar_values = list(_scalar_leaves(value))
        semantic_keys = list(_semantic_keys(value))
        return bool(scalar_values) and all(
            _text_has_value(evidence, item) for item in scalar_values
        ) and all(_text_has_value(evidence, key) for key in semantic_keys)
    return _text_has_value(evidence, value)


def _supported_geometry_component(
    value: object,
    evidence: str,
    *,
    trusted_confirmation: bool,
) -> bool:
    return trusted_confirmation or _text_has_value(evidence, value)


def _draft_id(request: str) -> str:
    digest = sha256(request.encode("utf-8")).hexdigest()[:16]
    return f"draft-{digest}"


_EVIDENCE_QUOTES = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
}


def _verified_user_evidence(evidence: str, request: str) -> bool:
    candidate = evidence.strip()
    if len(candidate) >= 2:
        closing = _EVIDENCE_QUOTES.get(candidate[0])
        if closing is not None and candidate[-1] == closing:
            candidate = candidate[1:-1].strip()
    return bool(candidate) and candidate in request


def _provided_mesh_route(
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
        and _verified_user_evidence(previous_geometry.evidence, request)
    ):
        user_source = FactSource.USER_TEXT
    trusted_confirmation = user_source == FactSource.USER_CONFIRMATION
    unit = (
        previous_value.get("length_unit")
        if previous_geometry is not None
        and user_source is not None
        and _supported_geometry_component(
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
        and _supported_geometry_component(
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
            and _supported_geometry_component(
                item.get("name"),
                previous_geometry.evidence,
                trusted_confirmation=trusted_confirmation,
            )
            and _supported_geometry_component(
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


_PUBLIC_FILE_GEOMETRY = {
    ".stl": ("surface", "stl", "surface_geometry"),
    ".obj": ("surface", "obj", "surface_geometry"),
    ".geo": ("gmsh", "geo", "gmsh_geometry"),
}


def _public_file_geometry_route(
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
    evidence_is_user = previous is not None and _verified_user_evidence(
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
                _supported_geometry_component(
                    value,
                    previous.evidence,
                    trusted_confirmation=False,
                )
                if not isinstance(value, list)
                else all(
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("role"), str)
                    and _supported_geometry_component(
                        item["name"],
                        previous.evidence,
                        trusted_confirmation=False,
                    )
                    and _supported_geometry_component(
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


def _ensure_input_questions(
    facts: list[TaskFact],
    questions: list[TaskQuestion],
    assets: list[PublicAsset],
) -> list[TaskQuestion]:
    """Make the serialized draft state match deterministic input authority."""

    authoritative = {
        item.path: item
        for item in facts
        if item.confirmed
        and item.source
        in {
            FactSource.USER_TEXT,
            FactSource.USER_CONFIRMATION,
            FactSource.PUBLIC_ASSET,
        }
    }
    from .projection import effective_geometry_value

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
            from foampilot.tasks import GeometryInput

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
        from foampilot.tasks import MeshIntent

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


def extract_task_draft(
    request: str,
    assets: list[PublicAsset],
    gateway: ModelGateway,
    *,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
    protected_paths: tuple[str, ...] = (),
    ingress_context: TaskIngressContext | None = None,
) -> TaskDraft:
    """Extract only facts and preserve every provenance boundary."""

    normalized = request.strip()
    if not normalized:
        raise ValueError("TASK_EXTRACTION_FAILED: request is blank")
    if any(path in normalized for path in protected_paths):
        raise ValueError("TASK_EXTRACTION_FAILED: request contains a protected path")
    response = gateway.generate_structured(
        ModelRequest(
            purpose="extract-cfd-task-draft",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "request": normalized,
                    "declared_assets": [
                        {
                            "path": item.path,
                            "sha256": item.sha256,
                            "purpose": item.purpose,
                            "kind": item.kind,
                            "install_path": item.install_path,
                            "bundle_manifest_sha256": (
                                item.bundle_manifest_sha256
                            ),
                        }
                        for item in assets
                    ],
                    "TaskIngressContext": (
                        (ingress_context or TaskIngressContext()).agent_payload()
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        ),
        _ExtractedTaskDraft,
        budget=budget,
        trace=trace,
    ).value
    serialized = response.model_dump_json()
    if any(path in serialized for path in protected_paths):
        raise ValueError("TASK_EXTRACTION_FAILED: model output contains a protected path")

    facts = []
    for extracted in _normalized_extracted_facts(response.facts):
        source = extracted.source
        confirmed = extracted.confirmed
        if source in {
            FactSource.USER_CONFIRMATION,
            FactSource.SYSTEM_DEFAULT,
        }:
            # Only the calling UI/user can create a confirmation, and only the
            # deterministic compiler can introduce system defaults.
            source = FactSource.MODEL_INFERENCE
        if (
            source == FactSource.USER_TEXT
            and not _verified_user_evidence(extracted.evidence, normalized)
        ):
            source = FactSource.MODEL_INFERENCE
        if source == FactSource.PUBLIC_ASSET:
            # The model may describe asset metadata, but only deterministic
            # ingress code can mint PUBLIC_ASSET authority.
            source = FactSource.MODEL_INFERENCE
        if source == FactSource.USER_TEXT:
            # Confirmation authority comes from verifiable provenance, not from
            # a boolean selected by the extraction model.
            confirmed = _user_fact_value_supported(
                extracted.path,
                json.loads(extracted.value_json),
                extracted.evidence,
            )
        if (
            source == FactSource.MODEL_INFERENCE
            and extracted.impact in {"medium", "high"}
        ):
            confirmed = False
        facts.append(
            TaskFact(
                path=extracted.path,
                value=json.loads(extracted.value_json),
                source=source,
                evidence=extracted.evidence,
                impact=extracted.impact,
                confirmed=confirmed,
            )
        )
    questions = [
        TaskQuestion(
            question_id=item.question_id,
            path=item.path,
            kind=item.kind,
            prompt_zh=item.prompt_zh,
            reason_zh=item.reason_zh,
            candidate=(
                json.loads(item.candidate_json)
                if item.candidate_json is not None
                else None
            ),
            evidence=item.evidence,
        )
        for item in response.unresolved_questions
        if item.path in _INPUT_QUESTION_PATHS
    ]
    active_context = ingress_context or TaskIngressContext()
    facts, questions = _provided_mesh_route(
        facts=facts,
        questions=questions,
        assets=assets,
        context=active_context,
        request=normalized,
    )
    facts, questions = _public_file_geometry_route(
        facts=facts,
        questions=questions,
        assets=assets,
        context=active_context,
        request=normalized,
    )
    questions = _ensure_input_questions(facts, questions, assets)
    has_blocking = any(item.kind == "blocking" for item in questions)
    has_confirmable = any(item.kind == "confirmable" for item in questions)
    status = (
        "incomplete"
        if has_blocking
        else (
            "ready_for_confirmation"
            if has_confirmable
            else "confirmed"
        )
    )
    return TaskDraft(
        draft_id=_draft_id(normalized),
        request_text=normalized,
        facts=facts,
        assumptions=[
            TaskAssumption(
                assumption_id=item.assumption_id,
                path=item.path,
                value=json.loads(item.value_json),
                source=(
                    "model_inference"
                    if item.source == "system_default"
                    else item.source
                ),
                impact=item.impact,
                explanation_zh=item.explanation_zh,
            )
            for item in response.assumptions
        ],
        unresolved_questions=questions,
        assets=assets,
        protected_paths=list(protected_paths),
        ingress_context=active_context,
        status=status,
    )
