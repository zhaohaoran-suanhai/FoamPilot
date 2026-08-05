"""Model-backed extraction without execution authority."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    model_config = ConfigDict(extra="forbid")
    path: str
    value: object
    source: FactSource
    evidence: str
    impact: Literal["low", "medium", "high"]
    confirmed: bool = False


class _ExtractedTaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    facts: list[_ExtractedFact] = Field(default_factory=list)
    assumptions: list[TaskAssumption] = Field(default_factory=list)
    unresolved_questions: list[TaskQuestion] = Field(default_factory=list)


_SYSTEM_PROMPT = """你只负责把一段公开 CFD 请求提取为结构化 TaskDraft 事实。
不得编写 OpenFOAM case，不得调用工具，不得读取 tutorial、golden、私有 evaluator 或宿主机路径。
不得虚构物性、单位、边界数值、初始条件、终止时间、工程容差或 solver 能力。
用户文本中明确出现的事实标为 user_text，并保留最短证据；附件 metadata 中的事实标为
public_asset。任何解释性推断必须标为 model_inference 且 confirmed=false。缺失的高影响信息放入
unresolved_questions；不要用 assumption 补齐。只返回请求 schema。"""


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
                        item.model_dump(mode="json") for item in assets
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
                value=extracted.value,
                source=source,
                evidence=extracted.evidence,
                impact=extracted.impact,
                confirmed=confirmed,
            )
        )
    has_blocking = any(
        item.kind == "blocking" for item in response.unresolved_questions
    )
    has_confirmable = any(
        item.kind == "confirmable" for item in response.unresolved_questions
    )
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
            (
                item.model_copy(update={"source": "model_inference"})
                if item.source == "system_default"
                else item
            )
            for item in response.assumptions
        ],
        unresolved_questions=response.unresolved_questions,
        assets=assets,
        protected_paths=list(protected_paths),
        status=status,
    )
