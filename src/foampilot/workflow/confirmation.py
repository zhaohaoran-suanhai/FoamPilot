"""Concrete per-field confirmation and immutable child continuation."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from foampilot.artifacts import ArtifactStore
from foampilot.simulation import (
    ConfirmationRecord,
    FactEvidence,
    JsonValue,
    ResolvedValue,
    SimulationIntent,
    canonical_sha256,
    write_json_exclusive,
)
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
from foampilot.simulation.requirements import ResolvedRequirements
from foampilot.simulation.risk_gate import (
    CaseDesign,
    RiskDecision,
    evaluate_design_risk,
    freeze_case_design,
)

from .lineage import LineageRecord
from .models import StrictModel


class ConfirmationError(ValueError):
    pass


class ConfirmationAnswer(StrictModel):
    question_id: str
    candidate_id: str
    confirmed_value: JsonValue


class ConfirmationAnswers(StrictModel):
    schema_version: Literal[1] = 1
    answers: tuple[ConfirmationAnswer, ...]

    @model_validator(mode="after")
    def validate_unique_answers(self) -> Self:
        question_ids = [item.question_id for item in self.answers]
        if len(question_ids) != len(set(question_ids)):
            raise ConfirmationError(
                "DUPLICATE_CONFIRMATION_ANSWER: question answered more than once"
            )
        return self


class ConfirmationRecords(StrictModel):
    schema_version: Literal[1] = 1
    records: tuple[ConfirmationRecord, ...]


class ConfirmationParent(StrictModel):
    run_dir: Path
    parent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent: SimulationIntent
    requirements: ResolvedRequirements
    proposal: CaseDesignProposal
    decision: RiskDecision


class ConfirmationContinuation(StrictModel):
    parent: ConfirmationParent
    records: tuple[ConfirmationRecord, ...]
    confirmation_record_hashes: tuple[str, ...]
    intent: SimulationIntent
    requirements: ResolvedRequirements
    proposal: CaseDesignProposal
    decision: RiskDecision
    design: CaseDesign | None


def _load_json_model(path: Path, model_type):
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfirmationError(
            f"CONFIRMATION_PARENT_INVALID: {path.name}: {error}"
        ) from error


def load_confirmation_parent(run_dir: str | Path) -> ConfirmationParent:
    """Verify and load a manifested design-gate parent run."""

    parent = Path(run_dir).resolve()
    store = ArtifactStore(parent.parent)
    try:
        problems = store.verify(parent)
    except (OSError, ValueError) as error:
        raise ConfirmationError(f"PARENT_MANIFEST_INVALID: {error}") from error
    if problems:
        raise ConfirmationError(
            "PARENT_MANIFEST_INVALID: " + "; ".join(problems)
        )
    intent = _load_json_model(parent / "simulation-intent.json", SimulationIntent)
    requirements = _load_json_model(
        parent / "resolved-requirements.json",
        ResolvedRequirements,
    )
    proposal = _load_json_model(
        parent / "case-design-proposal.json",
        CaseDesignProposal,
    )
    decision = _load_json_model(parent / "risk-decision.json", RiskDecision)
    if canonical_sha256(proposal) != decision.proposal_sha256:
        raise ConfirmationError(
            "PROPOSAL_HASH_MISMATCH: parent decision does not bind proposal"
        )
    return ConfirmationParent(
        run_dir=parent,
        parent_manifest_sha256=store.manifest_sha256(parent),
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        decision=decision,
    )


def parse_answers(payload: object) -> ConfirmationAnswers:
    """Parse only the concrete answer-file schema; no generic override."""

    if isinstance(payload, dict) and "action" in payload:
        raise ConfirmationError(
            "CONCRETE_CONFIRMATION_REQUIRED: generic actions are not supported"
        )
    try:
        return ConfirmationAnswers.model_validate(payload)
    except ConfirmationError:
        raise
    except ValueError as error:
        raise ConfirmationError(f"CONFIRMATION_ANSWERS_INVALID: {error}") from error


def _record_id(
    *,
    parent_manifest_sha256: str,
    answer: ConfirmationAnswer,
) -> str:
    canonical = json.dumps(
        {
            "parent_manifest_sha256": parent_manifest_sha256,
            "answer": answer.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "confirm-" + sha256(canonical).hexdigest()[:20]


def _confirmed_fact(
    *,
    question,
    candidate,
    record: ConfirmationRecord,
) -> ResolvedValue:
    return ResolvedValue(
        field_path=question.field_path,
        value=record.confirmed_value,
        source="user_confirmation",
        impact=question.impact,
        evidence=(
            *candidate.evidence,
            FactEvidence(
                kind="user_confirmation",
                detail=f"Confirmed concrete candidate {candidate.candidate_id}",
                reference=record.confirmation_id,
            ),
        ),
        confirmed=True,
    )


def _replace_intent(
    intent: SimulationIntent,
    confirmed: tuple[ResolvedValue, ...],
) -> SimulationIntent:
    facts = {item.field_path: item for item in intent.facts}
    facts.update({item.field_path: item for item in confirmed})
    payload = intent.model_dump(mode="json")
    payload["facts"] = [
        facts[path].model_dump(mode="json") for path in sorted(facts)
    ]
    confirmed_paths = {item.field_path for item in confirmed}
    payload["uncertainties"] = [
        item.model_dump(mode="json")
        for item in intent.uncertainties
        if item.field_path not in confirmed_paths
    ]
    return SimulationIntent.model_validate(payload)


def _proposal_section(path: str) -> str | None:
    return {
        "physics": "physical_models",
        "materials": "materials",
        "boundaries": "boundary_designs",
        "initial": "initial_conditions",
        "time": "time_design",
        "numerics": "numerical_design",
        "regions": "region_models",
    }.get(path.split(".", 1)[0])


def _replace_proposal(
    proposal: CaseDesignProposal,
    confirmed: tuple[ResolvedValue, ...],
) -> CaseDesignProposal:
    replacements = {item.field_path: item for item in confirmed}
    payload = proposal.model_dump(mode="json")
    if "solver.family" in replacements:
        payload["solver_family"] = replacements["solver.family"].model_dump(
            mode="json"
        )
    seen = {str(payload["solver_family"]["field_path"])}
    sections = (
        "physical_models",
        "materials",
        "boundary_designs",
        "initial_conditions",
        "time_design",
        "numerical_design",
        "region_models",
    )
    for section in sections:
        values = []
        for raw in payload[section]:
            path = str(raw["field_path"])
            selected = replacements.get(path)
            values.append(selected.model_dump(mode="json") if selected else raw)
            seen.add(path)
        payload[section] = values

    extension_payloads = []
    for raw_decision in payload["extension_decisions"]:
        values = []
        for raw in raw_decision["values"]:
            path = str(raw["field_path"])
            selected = replacements.get(path)
            values.append(selected.model_dump(mode="json") if selected else raw)
            seen.add(path)
        raw_decision["values"] = values
        extension_payloads.append(raw_decision)
    payload["extension_decisions"] = extension_payloads

    for path, value in sorted(replacements.items()):
        if path in seen:
            continue
        section = _proposal_section(path)
        if section is None:
            raise ConfirmationError(
                f"CONFIRMATION_FIELD_UNMAPPED: {path}"
            )
        payload[section].append(value.model_dump(mode="json"))
    confirmed_paths = set(replacements)
    payload["uncertainties"] = [
        item
        for item in payload["uncertainties"]
        if item["field_path"] not in confirmed_paths
    ]
    return CaseDesignProposal.model_validate(payload)


def apply_confirmation_records(
    parent: ConfirmationParent,
    answers: ConfirmationAnswers,
    *,
    answered_at: datetime | None = None,
) -> ConfirmationContinuation:
    """Apply exact candidates and re-run the deterministic design gate."""

    if parent.decision.state == "INFORMATION_REQUIRED" or any(
        item.kind in {"information_required", "conflict"}
        for item in parent.decision.questions
    ):
        raise ConfirmationError(
            "INFORMATION_REQUIRED: this parent requires new facts, not confirmation"
        )
    if parent.decision.state != "CONFIRMATION_REQUIRED":
        raise ConfirmationError(
            f"CONFIRMATION_NOT_ALLOWED: parent state is {parent.decision.state}"
        )
    expected = {
        item.question_id: item
        for item in parent.decision.questions
        if item.kind == "confirmable"
    }
    supplied = {item.question_id: item for item in answers.answers}
    missing = sorted(set(expected) - set(supplied))
    extra = sorted(set(supplied) - set(expected))
    if missing:
        raise ConfirmationError(
            "CONFIRMATION_ANSWER_MISSING: " + ", ".join(missing)
        )
    if extra:
        raise ConfirmationError(
            "CONFIRMATION_QUESTION_UNKNOWN: " + ", ".join(extra)
        )

    timestamp = answered_at or datetime.now(timezone.utc)
    records: list[ConfirmationRecord] = []
    facts: list[ResolvedValue] = []
    for question_id in sorted(expected):
        question = expected[question_id]
        answer = supplied[question_id]
        candidates = {
            item.candidate_id: item for item in question.candidates
        }
        candidate = candidates.get(answer.candidate_id)
        if candidate is None:
            raise ConfirmationError(
                f"CONFIRMATION_CANDIDATE_UNKNOWN: {answer.candidate_id}"
            )
        if candidate.value != answer.confirmed_value:
            raise ConfirmationError(
                f"CONFIRMATION_VALUE_MISMATCH: {question.field_path}"
            )
        record = ConfirmationRecord(
            confirmation_id=_record_id(
                parent_manifest_sha256=parent.parent_manifest_sha256,
                answer=answer,
            ),
            question_id=question.question_id,
            field_path=question.field_path,
            candidate_id=candidate.candidate_id,
            confirmed_value=answer.confirmed_value,
            answered_at=timestamp,
        )
        records.append(record)
        facts.append(
            _confirmed_fact(
                question=question,
                candidate=candidate,
                record=record,
            )
        )

    confirmed = tuple(facts)
    intent = _replace_intent(parent.intent, confirmed)
    requirements = parent.requirements.with_confirmations(confirmed)
    proposal = _replace_proposal(parent.proposal, confirmed)
    decision = evaluate_design_risk(
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        bound_extension_identities=(
            parent.decision.required_extension_identities
        ),
    )
    record_tuple = tuple(records)
    design = (
        freeze_case_design(
            proposal=proposal,
            decision=decision,
            intent=intent,
            confirmation_ids=tuple(
                item.confirmation_id for item in record_tuple
            ),
        )
        if decision.state == "READY_TO_AUTHOR"
        else None
    )
    return ConfirmationContinuation(
        parent=parent,
        records=record_tuple,
        confirmation_record_hashes=tuple(
            canonical_sha256(item) for item in record_tuple
        ),
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        decision=decision,
        design=design,
    )


def persist_confirmation_continuation(
    continuation: ConfirmationContinuation,
    *,
    run_root: str | Path,
) -> Path:
    """Write a manifested child without modifying the parent run."""

    store = ArtifactStore(run_root)
    child = store.create_run()
    write_json_exclusive(child / "simulation-intent.json", continuation.intent)
    write_json_exclusive(
        child / "resolved-requirements.json",
        continuation.requirements,
    )
    write_json_exclusive(
        child / "case-design-proposal.json",
        continuation.proposal,
    )
    write_json_exclusive(child / "risk-decision.json", continuation.decision)
    write_json_exclusive(
        child / "confirmation-records.json",
        ConfirmationRecords(records=continuation.records),
    )
    if continuation.decision.questions:
        write_json_exclusive(child / "questions.json", continuation.decision)
    if continuation.design is not None:
        write_json_exclusive(child / "case-design.json", continuation.design)
    lineage = LineageRecord(
        relation="design_confirmation",
        parent_run_id=continuation.parent.run_dir.name,
        parent_manifest_sha256=(
            continuation.parent.parent_manifest_sha256
        ),
        created_at=datetime.now(timezone.utc),
        input_hash_before=continuation.parent.decision.proposal_sha256,
        input_hash_after=continuation.decision.proposal_sha256,
        change_categories=["user_confirmation"],
        reused_evidence_paths=[
            "simulation-intent.json",
            "resolved-requirements.json",
            "case-design-proposal.json",
            "risk-decision.json",
        ],
        confirmation_record_hashes=list(
            continuation.confirmation_record_hashes
        ),
    )
    write_json_exclusive(child / "lineage.json", lineage)
    store.finalize(child)
    return child


__all__ = [
    "ConfirmationAnswer",
    "ConfirmationAnswers",
    "ConfirmationContinuation",
    "ConfirmationError",
    "ConfirmationParent",
    "apply_confirmation_records",
    "load_confirmation_parent",
    "parse_answers",
    "persist_confirmation_continuation",
]
