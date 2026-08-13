"""Model-backed extraction without execution authority."""

from __future__ import annotations

from hashlib import sha256
import json

from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.tasks import PublicAsset

from .authority import reconcile_extracted_facts
from .context import TaskIngressContext
from .extraction_protocol import _ExtractedTaskDraft, _SYSTEM_PROMPT
from .models import TaskAssumption, TaskDraft, TaskQuestion
from .provided_mesh import reconcile_provided_mesh
from .public_geometry import reconcile_public_geometry
from .questions import INPUT_QUESTION_PATHS, rebuild_input_questions


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

    facts = reconcile_extracted_facts(response.facts, normalized)
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
        if item.path in INPUT_QUESTION_PATHS
    ]
    active_context = ingress_context or TaskIngressContext()
    facts, questions = reconcile_provided_mesh(
        facts=facts,
        questions=questions,
        assets=assets,
        context=active_context,
        request=normalized,
    )
    facts, questions = reconcile_public_geometry(
        facts=facts,
        questions=questions,
        assets=assets,
        context=active_context,
        request=normalized,
    )
    questions = rebuild_input_questions(facts, questions, assets)
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
