"""Deterministic four-state release gate for simulation case designs."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from foampilot.extensions import CapabilityRegistry
from foampilot.repair.models import NumericalRepairEnvelope

from .design import CaseDesignProposal
from .intent import SimulationIntent
from .io import canonical_sha256
from .provenance import (
    DesignCandidate,
    FactEvidence,
    ResolvedValue,
    StrictModel,
    Uncertainty,
)
from .requirements import RequirementGap, ResolvedRequirements


RiskState = Literal[
    "READY_TO_AUTHOR",
    "CONFIRMATION_REQUIRED",
    "INFORMATION_REQUIRED",
    "CAPABILITY_UNAVAILABLE",
]


class RiskGateError(ValueError):
    pass


class RiskDecision(StrictModel):
    schema_version: Literal[1] = 1
    state: RiskState
    questions: tuple[Uncertainty, ...]
    reason_codes: tuple[str, ...]
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_extension_ids: tuple[str, ...]
    required_extension_identities: dict[str, str]

    @model_validator(mode="after")
    def validate_extension_identities(self) -> Self:
        if (
            self.state == "READY_TO_AUTHOR"
            and set(self.required_extension_ids)
            != set(self.required_extension_identities)
        ):
            raise ValueError("risk decision extension identities are incomplete")
        return self

    @model_validator(mode="after")
    def validate_state_shape(self) -> Self:
        question_ids = [item.question_id for item in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate risk-gate question IDs")
        if self.state == "READY_TO_AUTHOR" and self.questions:
            raise ValueError("ready decision must not contain questions")
        if (
            self.state == "CONFIRMATION_REQUIRED"
            and not any(item.kind == "confirmable" for item in self.questions)
        ):
            raise ValueError("confirmation state requires a confirmable question")
        if (
            self.state == "INFORMATION_REQUIRED"
            and not any(
                item.kind in {"information_required", "conflict"}
                for item in self.questions
            )
        ):
            raise ValueError("information state requires a blocking question")
        return self


class CaseDesign(StrictModel):
    schema_version: Literal[1] = 1
    proposal: CaseDesignProposal
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_ids: tuple[str, ...]
    extension_identities: dict[str, str]
    numerical_repair_envelope: NumericalRepairEnvelope = Field(
        default_factory=NumericalRepairEnvelope
    )
    design_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if canonical_sha256(self.proposal) != self.proposal_sha256:
            raise ValueError("frozen proposal hash mismatch")
        if len(self.confirmation_ids) != len(set(self.confirmation_ids)):
            raise ValueError("duplicate confirmation IDs")
        if self.recompute_sha256() != self.design_sha256:
            raise ValueError("frozen design hash mismatch")
        return self

    def recompute_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"design_sha256"})
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(canonical).hexdigest()


def _candidate_id(fact: ResolvedValue) -> str:
    payload = json.dumps(
        {
            "field_path": fact.field_path,
            "value": fact.value,
            "evidence": [item.model_dump(mode="json") for item in fact.evidence],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "design-" + sha256(payload).hexdigest()[:16]


def _question_id(prefix: str, field_path: str) -> str:
    normalized = field_path.replace(".", "-").replace("_", "-").lower()
    digest = sha256(field_path.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{normalized[:32]}-{digest}"


def _question_from_fact(fact: ResolvedValue) -> Uncertainty:
    return Uncertainty(
        question_id=_question_id("confirm", fact.field_path),
        field_path=fact.field_path,
        impact=fact.impact,
        kind="confirmable",
        prompt_zh=f"是否确认采用 {fact.field_path} 的这个具体值？",
        reason_zh="该中高影响工程值仅来自模型推断，必须由用户具体确认。",
        candidates=(
            DesignCandidate(
                candidate_id=_candidate_id(fact),
                value=fact.value,
                rationale="Case Designer 提出的具体工程候选。",
                evidence=fact.evidence,
            ),
        ),
    )


def _question_from_gap(gap: RequirementGap) -> Uncertainty:
    if gap.kind == "confirmable":
        return Uncertainty(
            question_id=_question_id("confirm", gap.field_path),
            field_path=gap.field_path,
            impact=gap.impact,
            kind="confirmable",
            prompt_zh=f"请确认 {gap.field_path} 的具体候选值。",
            reason_zh=gap.description,
            candidates=gap.candidates,
        )
    return Uncertainty(
        question_id=_question_id("provide", gap.field_path),
        field_path=gap.field_path,
        impact=gap.impact,
        kind="information_required",
        prompt_zh=f"请补充 {gap.field_path}。",
        reason_zh=gap.description,
    )


def _conflict_questions(requirements: ResolvedRequirements) -> list[Uncertainty]:
    return [
        Uncertainty(
            question_id=_question_id("resolve", conflict.field_path),
            field_path=conflict.field_path,
            impact="high",
            kind="conflict",
            prompt_zh=f"请解决 {conflict.field_path} 的权威事实冲突。",
            reason_zh=conflict.detail,
            conflicting_evidence=conflict.evidence,
        )
        for conflict in requirements.conflicts
    ]


def _merge_questions(*groups: tuple[Uncertainty, ...]) -> tuple[Uncertainty, ...]:
    merged: dict[str, Uncertainty] = {}
    kind_rank = {"confirmable": 0, "information_required": 1, "conflict": 2}
    for question in (item for group in groups for item in group):
        previous = merged.get(question.field_path)
        if previous is None or kind_rank[question.kind] > kind_rank[previous.kind]:
            merged[question.field_path] = question
    return tuple(
        sorted(merged.values(), key=lambda item: (item.field_path, item.question_id))
    )


def evaluate_design_risk(
    *,
    intent: SimulationIntent,
    requirements: ResolvedRequirements,
    proposal: CaseDesignProposal,
    registry: CapabilityRegistry | None = None,
    bound_extension_identities: dict[str, str] | None = None,
) -> RiskDecision:
    """Classify one proposal without consulting model confidence."""

    del intent
    extension_ids = tuple(
        sorted(item.extension_id for item in proposal.extension_decisions)
    )
    capability_reasons = list(proposal.capability_conflicts)
    extension_identities: dict[str, str] = {}
    if registry is None:
        bound = bound_extension_identities or {}
        if set(extension_ids) != set(bound):
            capability_reasons.append(
                "selected extensions differ from the bound parent decision"
            )
        else:
            extension_identities = dict(sorted(bound.items()))
    else:
        for extension_id in extension_ids:
            try:
                descriptor = registry.descriptor(extension_id)
            except LookupError:
                capability_reasons.append(
                    f"extension is not registered: {extension_id}"
                )
                continue
            extension_identities[extension_id] = (
                f"{descriptor.extension_version}/protocol-{descriptor.protocol_version}"
            )

    requirement_questions = tuple(
        _question_from_gap(item) for item in requirements.gaps
    )
    conflict_questions = tuple(_conflict_questions(requirements))
    proposal_questions = proposal.uncertainties
    inferred_questions = tuple(
        _question_from_fact(fact)
        for fact in proposal.iter_values()
        if fact.source == "model_inference"
        and not fact.confirmed
        and fact.impact in {"medium", "high"}
    )
    questions = _merge_questions(
        requirement_questions,
        conflict_questions,
        proposal_questions,
        inferred_questions,
    )

    blocking_information = any(
        item.kind in {"information_required", "conflict"}
        for item in questions
    )
    pending_confirmation = any(
        item.kind == "confirmable" for item in questions
    )
    if capability_reasons:
        state: RiskState = "CAPABILITY_UNAVAILABLE"
        reasons = ("CAPABILITY_CONFLICT",)
        questions = ()
    elif blocking_information:
        state = "INFORMATION_REQUIRED"
        reasons = ("REQUIRED_INFORMATION_MISSING_OR_CONFLICTING",)
    elif pending_confirmation:
        state = "CONFIRMATION_REQUIRED"
        reasons = ("CONCRETE_CONFIRMATION_REQUIRED",)
    else:
        state = "READY_TO_AUTHOR"
        reasons = ("DESIGN_FACTS_RESOLVED",)
        questions = ()

    return RiskDecision(
        state=state,
        questions=questions,
        reason_codes=tuple(dict.fromkeys((*reasons, *capability_reasons))),
        proposal_sha256=canonical_sha256(proposal),
        required_extension_ids=extension_ids,
        required_extension_identities=dict(sorted(extension_identities.items())),
    )


def freeze_case_design(
    *,
    proposal: CaseDesignProposal,
    decision: RiskDecision,
    intent: SimulationIntent,
    confirmation_ids: tuple[str, ...] = (),
    numerical_repair_envelope: NumericalRepairEnvelope | None = None,
) -> CaseDesign:
    """Freeze a proposal only when the exact gated content is ready."""

    if decision.state != "READY_TO_AUTHOR":
        raise RiskGateError("case design is not ready to author")
    proposal_sha256 = canonical_sha256(proposal)
    if proposal_sha256 != decision.proposal_sha256:
        raise RiskGateError("proposal hash does not match the risk decision")

    payload = {
        "schema_version": 1,
        "proposal": proposal,
        "intent_sha256": canonical_sha256(intent),
        "proposal_sha256": proposal_sha256,
        "confirmation_ids": tuple(sorted(confirmation_ids)),
        "extension_identities": decision.required_extension_identities,
        "numerical_repair_envelope": (
            numerical_repair_envelope or NumericalRepairEnvelope()
        ),
    }
    canonical = json.dumps(
        {
            key: (
                value.model_dump(mode="json")
                if isinstance(value, (CaseDesignProposal, NumericalRepairEnvelope))
                else value
            )
            for key, value in payload.items()
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return CaseDesign(
        **payload,
        design_sha256=sha256(canonical).hexdigest(),
    )


__all__ = [
    "CaseDesign",
    "RiskDecision",
    "RiskGateError",
    "RiskState",
    "evaluate_design_risk",
    "freeze_case_design",
]
