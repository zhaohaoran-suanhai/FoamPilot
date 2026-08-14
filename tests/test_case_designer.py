from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from foampilot.context import AgentContext
from foampilot.extensions import (
    CapabilityDescriptor,
    CapabilityRegistry,
    SupportedTarget,
)
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.simulation import FactEvidence, ResolvedValue, SimulationIntent
from foampilot.simulation.design import (
    CaseDesignProposal,
    ExtensionDecision,
    design_case,
    normalize_case_design_input,
)
from foampilot.simulation.requirements import resolve_requirements
from foampilot.tasks import TaskSpec
from tests.support.tasks import canonical_task_payload


def _fact(path: str, value: object, *, source: str = "model_inference"):
    return ResolvedValue(
        field_path=path,
        value=value,
        source=source,
        impact="high",
        evidence=(FactEvidence(kind="model_reason", detail="design rationale"),),
        confirmed=source != "model_inference",
    )


def _extension(extension_id: str = "foampilot.solver.piso") -> ExtensionDecision:
    return ExtensionDecision(
        extension_id=extension_id,
        schema_version=1,
        values=(),
        provenance=(
            FactEvidence(kind="model_reason", detail="selected capability"),
        ),
    )


def _proposal(
    *,
    solver: str = "pisoFoam",
    extension_id: str = "foampilot.solver.piso",
) -> CaseDesignProposal:
    return CaseDesignProposal(
        solver_family=_fact("solver.family", solver),
        physical_models=(_fact("physics.regime", "laminar"),),
        materials=(),
        boundary_designs=(),
        initial_conditions=(),
        time_design=(),
        numerical_design=(),
        region_models=(),
        extension_decisions=(_extension(extension_id),),
        uncertainties=(),
        alternatives=(),
        reasoning_evidence=(
            FactEvidence(kind="model_reason", detail="coherent transient design"),
        ),
        capability_conflicts=(),
    )


def _descriptor(
    *,
    extension_id: str = "foampilot.solver.piso",
    versions: tuple[str, ...] = ("10",),
    executables: tuple[str, ...] = ("pisoFoam",),
    incompatible: tuple[str, ...] = (),
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        extension_id=extension_id,
        extension_version="1.0.0",
        capability_kinds=("solver:pisofoam",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=versions),
        ),
        required_executables=executables,
        incompatible_extensions=incompatible,
    )


def _registry(*descriptors: CapabilityDescriptor) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for descriptor in descriptors or (_descriptor(),):
        registry.register(descriptor, object())
    return registry


def _task() -> TaskSpec:
    return TaskSpec.model_validate(
        canonical_task_payload(
            {
                "schema_version": 2,
                "task_id": "case-design-test",
                "title": "Case design test",
                "prompt": "Use pisoFoam with nu = 1e-6 m2/s.",
                "openfoam_target": {
                    "distribution": "foundation",
                    "version": "10",
                },
                "resource_budget": {
                    "max_attempts": 1,
                    "max_wall_seconds": 60,
                    "max_mpi_ranks": 1,
                    "memory_mib": 512,
                },
                "required_outputs": ["velocity"],
                "acceptance_requirements": ["normal completion"],
                "public_checks": [],
                "protected_paths": [],
            }
        )
    )


def _context(*, knowledge_text: str = "[]") -> AgentContext:
    return AgentContext(
        knowledge_text=knowledge_text,
        skills_text="",
        knowledge_slots={},
        missing_slots=(),
        selected_knowledge_ids=(),
        selected_source_hashes={},
        skill_names=(),
    )


class ScriptedDesignGateway:
    primary_backend_id = "scripted"
    primary_model = "scripted-design"
    policy_sha256 = "a" * 64

    def __init__(self, response: CaseDesignProposal) -> None:
        self.response = response
        self.requests = []

    def generate_structured(
        self,
        request,
        schema,
        *,
        budget,
        trace,
        output_normalizer=None,
    ):
        del trace
        assert budget.stage == ModelStage.CASE_DESIGN
        assert schema is CaseDesignProposal
        self.requests.append(request)
        return ModelResult(
            value=self.response,
            logical_request_id="design-1",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def test_design_input_normalizer_rejects_exact_cross_container_duplicates() -> None:
    payload = _proposal().model_dump(mode="json")
    duplicate = _fact("materials.fluid.nu", 1.0e-6).model_dump(mode="json")
    payload["materials"] = [duplicate]
    payload["extension_decisions"][0]["values"] = [duplicate]

    with pytest.raises(
        ValidationError,
        match=r"duplicate case design.*materials\.fluid\.nu",
    ):
        normalize_case_design_input(json.dumps(payload))


def test_design_input_normalizer_rejects_conflicting_duplicate_values() -> None:
    payload = _proposal().model_dump(mode="json")
    payload["materials"] = [
        _fact("materials.fluid.nu", 1.0e-6).model_dump(mode="json")
    ]
    payload["extension_decisions"][0]["values"] = [
        _fact("materials.fluid.nu", 2.0e-6).model_dump(mode="json")
    ]

    with pytest.raises(
        ValidationError,
        match=r"duplicate case design.*materials\.fluid\.nu",
    ):
        normalize_case_design_input(json.dumps(payload))


def test_design_prompt_requires_global_unique_field_ownership() -> None:
    _, gateway = _run(_proposal())

    prompt = gateway.requests[0].system_prompt
    assert "Each field_path must occur exactly once globally" in prompt
    assert "extension_decisions.values" in prompt
    assert "do not repeat it in a section array" in prompt
    assert "Use the exact required fact field_paths" in prompt
    assert "do not invent aliases" in prompt


def test_design_input_normalizer_rejects_empty_candidate_evidence() -> None:
    payload = _proposal().model_dump(mode="json")
    payload["uncertainties"] = [
        {
            "question_id": "confirm_nu",
            "field_path": "materials.fluid.nu",
            "impact": "high",
            "kind": "confirmable",
            "prompt_zh": "请确认运动黏度。",
            "reason_zh": "运动黏度影响雷诺数。",
            "candidates": [
                {
                    "candidate_id": "nu_001",
                    "value": {"value": 0.001, "unit": "m2/s"},
                    "rationale": "使流动保持在层流范围。",
                    "evidence": [],
                }
            ],
            "conflicting_evidence": [],
        }
    ]

    with pytest.raises(
        ValidationError,
        match="uncertainties.0.candidates.0.evidence",
    ):
        normalize_case_design_input(json.dumps(payload))


def test_design_input_normalizer_does_not_repair_empty_fact_evidence() -> None:
    payload = _proposal().model_dump(mode="json")
    payload["physical_models"][0]["evidence"] = []

    with pytest.raises(ValidationError, match="physical_models.0.evidence"):
        normalize_case_design_input(json.dumps(payload))


def _window():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.CASE_DESIGN,
        stage_deadline_seconds=30,
        max_transport_attempts=1,
    )


def _run(
    proposal: CaseDesignProposal,
    *,
    registry: CapabilityRegistry | None = None,
    available_executables: tuple[str, ...] = ("pisoFoam",),
    intent: SimulationIntent | None = None,
    context: AgentContext | None = None,
) -> tuple[CaseDesignProposal, ScriptedDesignGateway]:
    effective_intent = intent or SimulationIntent()
    gateway = ScriptedDesignGateway(proposal)
    resolved = resolve_requirements(
        intent=effective_intent,
        mesh_facts=(),
        capabilities=(),
    )
    result = design_case(
        task=_task(),
        intent=effective_intent,
        requirements=resolved,
        mesh_facts=(),
        registry=registry or _registry(),
        context=context or _context(),
        available_executables=available_executables,
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )
    return result, gateway


def test_case_design_proposal_cannot_contain_files_or_commands() -> None:
    payload = _proposal().model_dump(mode="json")
    with pytest.raises(ValidationError):
        CaseDesignProposal.model_validate({**payload, "commands": []})
    with pytest.raises(ValidationError):
        CaseDesignProposal.model_validate({**payload, "files": {}})


def test_designer_cannot_select_unregistered_solver() -> None:
    proposal, _ = _run(
        _proposal(solver="unregistered", extension_id="foampilot.solver.piso")
    )

    assert proposal.capability_conflicts == (
        "solver family is not registered: unregistered",
    )


def test_target_mismatch_and_missing_executable_are_conflicts() -> None:
    target_mismatch, _ = _run(
        _proposal(),
        registry=_registry(_descriptor(versions=("11",))),
    )
    missing_executable, _ = _run(_proposal(), available_executables=())

    assert any("target is unsupported" in item for item in target_mismatch.capability_conflicts)
    assert missing_executable.capability_conflicts == (
        "required executable is unavailable: pisoFoam",
    )


def test_selected_incompatible_extensions_are_rejected() -> None:
    other = CapabilityDescriptor(
        extension_id="foampilot.physics.other",
        extension_version="1.0.0",
        capability_kinds=("physics:other",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
    )
    base = _proposal().model_copy(
        update={"extension_decisions": (_extension(), _extension(other.extension_id))}
    )
    result, _ = _run(
        base,
        registry=_registry(
            _descriptor(incompatible=(other.extension_id,)),
            other,
        ),
    )

    assert any("extensions are incompatible" in item for item in result.capability_conflicts)


def test_explicit_high_impact_fact_is_preserved_over_model_design() -> None:
    explicit = ResolvedValue(
        field_path="materials.fluid.nu",
        value={"value": 1e-6, "unit": "m2/s"},
        source="user_text",
        impact="high",
        evidence=(FactEvidence(kind="user_quote", detail="nu = 1e-6 m2/s"),),
        confirmed=True,
    )
    intent = SimulationIntent(facts=(explicit,))
    proposal = _proposal().model_copy(
        update={
            "materials": (
                _fact("materials.fluid.nu", {"value": 2e-6, "unit": "m2/s"}),
            )
        }
    )

    result, _ = _run(proposal, intent=intent)

    assert result.materials == (explicit,)
    assert any("contradicts resolved fact" in item for item in result.capability_conflicts)


def test_matching_aggregate_is_confirmed_from_all_authoritative_children() -> None:
    velocity = ResolvedValue(
        field_path="initial_conditions.velocity",
        value={"vector": [0, 0, 0], "unit": "m/s"},
        source="user_text",
        impact="medium",
        evidence=(FactEvidence(kind="user_quote", detail="start from rest"),),
        confirmed=True,
    )
    pressure = ResolvedValue(
        field_path="initial_conditions.kinematic_pressure",
        value={"value": 0, "unit": "m2/s2"},
        source="user_text",
        impact="medium",
        evidence=(FactEvidence(kind="user_quote", detail="zero pressure datum"),),
        confirmed=True,
    )
    aggregate = _fact(
        "initial_conditions",
        {
            "velocity": velocity.value,
            "kinematic_pressure": pressure.value,
        },
    )
    proposal = _proposal().model_copy(
        update={"initial_conditions": (aggregate,)}
    )

    result, _ = _run(
        proposal,
        intent=SimulationIntent(facts=(velocity, pressure)),
    )
    facts = {item.field_path: item for item in result.initial_conditions}

    assert facts["initial_conditions"].confirmed is True
    assert facts["initial_conditions"].source == "deterministic_rule"
    assert facts["initial_conditions"].evidence[-1].kind == "deterministic_composition"


def test_mismatching_aggregate_remains_unconfirmed() -> None:
    velocity = ResolvedValue(
        field_path="initial_conditions.velocity",
        value={"vector": [0, 0, 0], "unit": "m/s"},
        source="user_text",
        impact="medium",
        evidence=(FactEvidence(kind="user_quote", detail="start from rest"),),
        confirmed=True,
    )
    aggregate = _fact(
        "initial_conditions",
        {"velocity": {"vector": [1, 0, 0], "unit": "m/s"}},
    )
    proposal = _proposal().model_copy(
        update={"initial_conditions": (aggregate,)}
    )

    result, _ = _run(
        proposal,
        intent=SimulationIntent(facts=(velocity,)),
    )
    facts = {item.field_path: item for item in result.initial_conditions}

    assert facts["initial_conditions"].confirmed is False
    assert facts["initial_conditions"].source == "model_inference"


def test_explicit_fact_replaces_matching_extension_value_without_duplication() -> None:
    explicit = ResolvedValue(
        field_path="regions.porousBlockage.role",
        value="porous",
        source="user_text",
        impact="high",
        evidence=(
            FactEvidence(
                kind="user_quote",
                detail="porousBlockage is the porous cell zone",
            ),
        ),
        confirmed=True,
    )
    intent = SimulationIntent(facts=(explicit,))
    extension = _extension().model_copy(
        update={
            "values": (
                _fact("regions.porousBlockage.role", "porous"),
            )
        }
    )
    proposal = _proposal().model_copy(
        update={"extension_decisions": (extension,)}
    )

    result, _ = _run(proposal, intent=intent)

    assert result.region_models == ()
    assert result.extension_decisions[0].values == (explicit,)


def test_model_cannot_self_assert_user_or_public_authority() -> None:
    spoofed = ResolvedValue(
        field_path="materials.fluid.nu",
        value=1e-6,
        source="public_asset_fact",
        impact="high",
        evidence=(FactEvidence(kind="asset_fact", detail="invented authority"),),
        confirmed=True,
    )
    proposal = _proposal().model_copy(update={"materials": (spoofed,)})

    result, _ = _run(proposal)

    assert result.materials[0].source == "model_inference"
    assert result.materials[0].confirmed is False
    assert result.materials[0].evidence[-1].kind == "authority_audit"


def test_designer_prompt_is_bounded_and_contains_no_legacy_evaluator_contract() -> None:
    intent = SimulationIntent(
        facts=(
            ResolvedValue(
                field_path="physics.regime",
                value="transient",
                source="user_text",
                impact="high",
                evidence=(
                    FactEvidence(kind="user_quote", detail="transient"),
                ),
                confirmed=True,
            ),
        )
    )
    result, gateway = _run(_proposal(), intent=intent)
    request = gateway.requests[0]

    assert result.solver_family.field_path == "solver.family"
    assert len(request.user_prompt.encode("utf-8")) < 64 * 1024
    assert "\n" not in request.user_prompt
    assert "acceptance.legacy_checks" not in request.user_prompt
    assert "public_checks" not in request.user_prompt
    assert "Never emit an empty evidence or provenance array" in request.system_prompt
    parsed = json.loads(request.user_prompt)
    assert set(parsed["SimulationIntent"]) == {
        "constraints",
        "uncertainties",
    }
    assert any(
        item["field_path"] == "physics.regime"
        for item in parsed["ResolvedRequirements"]["resolved"]
    )
    assert set(parsed["public_context"]) == {
        "knowledge_text",
        "skills_text",
        "selected_knowledge_ids",
        "selected_source_hashes",
        "skill_names",
    }


def test_oversized_design_context_fails_before_model_call() -> None:
    with pytest.raises(ValueError, match="design context"):
        _run(_proposal(), context=_context(knowledge_text="x" * (64 * 1024)))
