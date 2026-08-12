"""Model-backed extraction without execution authority."""

from __future__ import annotations

from hashlib import sha256
import json
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


class _ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
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
        fact_paths = [item.path for item in self.facts]
        if len(fact_paths) != len(set(fact_paths)):
            raise ValueError("duplicate fact paths are not allowed")
        assumption_ids = [item.assumption_id for item in self.assumptions]
        if len(assumption_ids) != len(set(assumption_ids)):
            raise ValueError("duplicate assumption IDs are not allowed")
        question_ids = [
            item.question_id for item in self.unresolved_questions
        ]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate question IDs are not allowed")
        return self


_SYSTEM_PROMPT = """你只负责把一段公开 CFD 请求提取为结构化 TaskDraft 事实。
不得编写 OpenFOAM case，不得调用工具，不得读取 tutorial、golden、私有 evaluator 或宿主机路径。
不得虚构物性、单位、边界数值、初始条件、终止时间、工程容差或 solver 能力。
用户文本中明确出现的事实标为 user_text，并保留最短证据；附件 metadata 中的事实标为
public_asset。任何解释性推断必须标为 model_inference 且 confirmed=false。缺失的高影响信息放入
unresolved_questions；不要用 assumption 补齐。fact/assumption 的 value 与 question 的非空
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


def _draft_id(request: str) -> str:
    digest = sha256(request.encode("utf-8")).hexdigest()[:16]
    return f"draft-{digest}"


def extract_task_draft(
    request: str,
    assets: list[PublicAsset],
    gateway: ModelGateway,
    *,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
    protected_paths: tuple[str, ...] = (),
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
    needs_confirmation = False
    asset_evidence_tokens = {
        token
        for item in assets
        for token in (item.path, item.sha256)
    }
    for extracted in response.facts:
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
            and extracted.evidence not in normalized
        ):
            source = FactSource.MODEL_INFERENCE
        if (
            source == FactSource.PUBLIC_ASSET
            and not any(
                token in extracted.evidence
                for token in asset_evidence_tokens
            )
        ):
            source = FactSource.MODEL_INFERENCE
        if source in {FactSource.USER_TEXT, FactSource.PUBLIC_ASSET}:
            # Confirmation authority comes from verifiable provenance, not from
            # a boolean selected by the extraction model.
            confirmed = True
        if (
            source == FactSource.MODEL_INFERENCE
            and extracted.impact in {"medium", "high"}
        ):
            confirmed = False
            needs_confirmation = True
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
    ]
    has_blocking = any(item.kind == "blocking" for item in questions)
    has_confirmable = any(item.kind == "confirmable" for item in questions)
    status = (
        "incomplete"
        if has_blocking
        else (
            "ready_for_confirmation"
            if needs_confirmation or has_confirmable
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
        status=status,
    )
