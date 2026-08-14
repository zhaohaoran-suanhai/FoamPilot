from __future__ import annotations

import pytest
from pydantic import ValidationError

from foampilot.extensions import (
    CapabilityDescriptor,
    CapabilityRegistry,
    RequiredFact,
    SupportedTarget,
)
from foampilot.simulation import (
    DesignCandidate,
    FactEvidence,
    ResolvedValue,
    SimulationIntent,
    Uncertainty,
)
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
from foampilot.simulation.requirements import resolve_requirements
from foampilot.simulation.risk_gate import (
    RiskGateError,
    _question_id,
    evaluate_design_risk,
    freeze_case_design,
)


def _evidence(detail: str = "explicit fact") -> tuple[FactEvidence, ...]:
    return (FactEvidence(kind="test_fact", detail=detail),)


def _value(
    path: str,
    value: object,
    *,
    source: str = "user_text",
    impact: str = "high",
) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source=source,
        impact=impact,
        evidence=_evidence(),
        confirmed=source != "model_inference",
    )


def _registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityDescriptor(
            extension_id="foampilot.solver.piso",
            extension_version="1.0.0",
            capability_kinds=("solver:pisofoam",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
        ),
        object(),
    )
    return registry


def _proposal(
    *,
    solver: ResolvedValue | None = None,
    materials: tuple[ResolvedValue, ...] = (),
    uncertainties: tuple[Uncertainty, ...] = (),
    conflicts: tuple[str, ...] = (),
) -> CaseDesignProposal:
    return CaseDesignProposal(
        solver_family=solver or _value("solver.family", "pisoFoam"),
        physical_models=(_value("physics.regime", "laminar"),),
        materials=materials,
        boundary_designs=(),
        initial_conditions=(),
        time_design=(),
        numerical_design=(),
        region_models=(),
        extension_decisions=(
            ExtensionDecision(
                extension_id="foampilot.solver.piso",
                schema_version=1,
                values=(),
                provenance=_evidence("selected registered solver"),
            ),
        ),
        uncertainties=uncertainties,
        alternatives=(),
        reasoning_evidence=_evidence("coherent design"),
        capability_conflicts=conflicts,
    )


def _decision(
    proposal: CaseDesignProposal,
    *,
    intent: SimulationIntent | None = None,
):
    actual_intent = intent or SimulationIntent()
    requirements = resolve_requirements(
        intent=actual_intent,
        mesh_facts=(),
        capabilities=(),
    )
    return evaluate_design_risk(
        intent=actual_intent,
        requirements=requirements,
        proposal=proposal,
        registry=_registry(),
    )


@pytest.mark.parametrize(
    ("proposal", "expected"),
    [
        (_proposal(), "READY_TO_AUTHOR"),
        (
            _proposal(
                materials=(
                    _value(
                        "materials.fluid.nu",
                        1e-6,
                        source="model_inference",
                    ),
                )
            ),
            "CONFIRMATION_REQUIRED",
        ),
        (
            _proposal(
                uncertainties=(
                    Uncertainty(
                        question_id="q-fluid-nu",
                        field_path="materials.fluid.nu",
                        impact="high",
                        kind="information_required",
                        prompt_zh="请提供流体运动黏度。",
                        reason_zh="当前信息不足以唯一确定流体物性。",
                    ),
                )
            ),
            "INFORMATION_REQUIRED",
        ),
        (
            _proposal(conflicts=("solver family is not registered: bad",)),
            "CAPABILITY_UNAVAILABLE",
        ),
    ],
)
def test_risk_gate_states(proposal: CaseDesignProposal, expected: str) -> None:
    assert _decision(proposal).state == expected


def test_model_reported_confidence_is_not_a_schema_field() -> None:
    payload = _proposal().model_dump(mode="json")
    with pytest.raises(ValidationError):
        CaseDesignProposal.model_validate({**payload, "confidence": "high"})


def test_unregistered_extension_is_capability_unavailable_not_schema_failure() -> None:
    proposal = _proposal().model_copy(
        update={
            "extension_decisions": (
                ExtensionDecision(
                    extension_id="foampilot.solver.unknown",
                    schema_version=1,
                    values=(),
                    provenance=_evidence("model selected unknown extension"),
                ),
            )
        }
    )

    decision = _decision(proposal)

    assert decision.state == "CAPABILITY_UNAVAILABLE"
    assert decision.required_extension_identities == {}


def test_confirmation_question_contains_one_concrete_candidate() -> None:
    proposal = _proposal(
        materials=(
            _value(
                "materials.fluid.nu",
                {"value": 1e-6, "unit": "m2/s"},
                source="model_inference",
            ),
        )
    )

    decision = _decision(proposal)

    assert decision.state == "CONFIRMATION_REQUIRED"
    assert len(decision.questions) == 1
    question = decision.questions[0]
    assert question.kind == "confirmable"
    assert len(question.candidates) == 1
    assert question.candidates[0].value == {"value": 1e-6, "unit": "m2/s"}


def test_generated_question_id_stays_valid_when_field_prefix_is_truncated() -> None:
    identifier = _question_id(
        "confirm",
        "regions.porousBlockage.porosity_model",
    )

    assert "--" not in identifier
    assert identifier == identifier.strip("-_")
    Uncertainty(
        question_id=identifier,
        field_path="regions.porousBlockage.porosity_model",
        impact="high",
        kind="confirmable",
        prompt_zh="确认模型？",
        reason_zh="需要确认。",
        candidates=(
            DesignCandidate(
                candidate_id="porosity-model",
                value="DarcyForchheimer",
                rationale="Foundation 10 模型。",
                evidence=_evidence(),
            ),
        ),
    )


def test_requirement_conflict_precedes_confirmation_candidate() -> None:
    proposal = _proposal(
        materials=(
            _value("materials.fluid.nu", 1e-6, source="model_inference"),
        ),
        uncertainties=(
            Uncertainty(
                question_id="q-zone-role",
                field_path="regions.zone.role",
                impact="high",
                kind="conflict",
                prompt_zh="请解决区域语义冲突。",
                reason_zh="两个权威来源不一致。",
                conflicting_evidence=(
                    FactEvidence(kind="user_quote", detail="porous fluid"),
                    FactEvidence(kind="user_quote", detail="solid"),
                ),
            ),
        ),
    )

    decision = _decision(proposal)

    assert decision.state == "INFORMATION_REQUIRED"


def test_freeze_rejects_non_ready_state_and_hash_mismatch() -> None:
    pending = _proposal(
        materials=(
            _value("materials.fluid.nu", 1e-6, source="model_inference"),
        )
    )
    decision = _decision(pending)
    with pytest.raises(RiskGateError, match="not ready"):
        freeze_case_design(
            proposal=pending,
            decision=decision,
            intent=SimulationIntent(),
        )

    ready = _proposal()
    ready_decision = _decision(ready)
    changed = ready.model_copy(update={"alternatives": ("changed",)})
    with pytest.raises(RiskGateError, match="hash"):
        freeze_case_design(
            proposal=changed,
            decision=ready_decision,
            intent=SimulationIntent(),
        )


def test_ready_design_freezes_extension_identities_and_hashes() -> None:
    intent = SimulationIntent(
        facts=(_value("physics.regime", "laminar"),)
    )
    proposal = _proposal()
    decision = _decision(proposal, intent=intent)

    design = freeze_case_design(
        proposal=proposal,
        decision=decision,
        intent=intent,
        confirmation_ids=("confirm-nu",),
    )

    assert design.proposal_sha256 == decision.proposal_sha256
    assert design.confirmation_ids == ("confirm-nu",)
    assert design.extension_identities == {
        "foampilot.solver.piso": "1.0.0/protocol-1"
    }
    assert decision.required_extension_identities == design.extension_identities
    assert len(design.intent_sha256) == 64
    assert len(design.design_sha256) == 64
    assert design.design_sha256 == design.recompute_sha256()


def test_user_authored_confirmable_uncertainty_is_not_silently_discarded() -> None:
    proposal = _proposal(
        uncertainties=(
            Uncertainty(
                question_id="q-end-time",
                field_path="time.end",
                impact="medium",
                kind="confirmable",
                prompt_zh="确认结束时间？",
                reason_zh="这是模型提出的具体工程候选。",
                candidates=(
                    DesignCandidate(
                        candidate_id="end-time-40",
                        value={"value": 40, "unit": "s"},
                        rationale="覆盖多个对流时间。",
                        evidence=_evidence("time scale estimate"),
                    ),
                ),
            ),
        )
    )

    decision = _decision(proposal)

    assert decision.state == "CONFIRMATION_REQUIRED"
    assert decision.questions[0].question_id == "q-end-time"


def _designer_required_end_time():
    return (
        CapabilityDescriptor(
            extension_id="foampilot.physics.transient",
            extension_version="1.0.0",
            capability_kinds=("physics:transient",),
            supported_targets=(
                SupportedTarget(distribution="foundation", versions=("10",)),
            ),
            required_facts=(
                RequiredFact(
                    field_path="time.end",
                    impact="high",
                    description="Transient end time",
                    resolution="designer_candidate",
                ),
            ),
        ),
    )


def test_designer_required_fact_becomes_confirmation_after_design() -> None:
    intent = SimulationIntent()
    requirements = resolve_requirements(
        intent=intent,
        mesh_facts=(),
        capabilities=_designer_required_end_time(),
    )
    proposal = _proposal().model_copy(
        update={
            "time_design": (
                _value(
                    "time.end",
                    {"value": 40, "unit": "s"},
                    source="model_inference",
                ),
            )
        }
    )

    decision = evaluate_design_risk(
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        registry=_registry(),
    )

    assert decision.state == "CONFIRMATION_REQUIRED"
    assert [item.field_path for item in decision.questions] == ["time.end"]


def test_designer_required_uncertainty_candidate_becomes_confirmation() -> None:
    intent = SimulationIntent()
    requirements = resolve_requirements(
        intent=intent,
        mesh_facts=(),
        capabilities=_designer_required_end_time(),
    )
    proposal = _proposal(
        uncertainties=(
            Uncertainty(
                question_id="confirm_end_time",
                field_path="time.end",
                impact="high",
                kind="confirmable",
                prompt_zh="确认结束时间？",
                reason_zh="Case Designer 已给出具体候选。",
                candidates=(
                    DesignCandidate(
                        candidate_id="end_time_40",
                        value={"value": 40, "unit": "s"},
                        rationale="覆盖多个对流时间。",
                        evidence=_evidence("convective time estimate"),
                    ),
                ),
            ),
        )
    )

    decision = evaluate_design_risk(
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        registry=_registry(),
    )

    assert decision.state == "CONFIRMATION_REQUIRED"
    assert decision.questions == proposal.uncertainties


def test_missing_designer_candidate_fails_closed_after_design() -> None:
    intent = SimulationIntent()
    requirements = resolve_requirements(
        intent=intent,
        mesh_facts=(),
        capabilities=_designer_required_end_time(),
    )

    decision = evaluate_design_risk(
        intent=intent,
        requirements=requirements,
        proposal=_proposal(),
        registry=_registry(),
    )

    assert decision.state == "INFORMATION_REQUIRED"
    assert decision.questions[0].field_path == "time.end"
    assert decision.questions[0].kind == "information_required"
