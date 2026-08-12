from __future__ import annotations

import json

import pytest

from foampilot.context import assemble_agent_context
from foampilot.routing import CapabilityProfile

from .test_agent_context import _task


def _profile(
    solver: str,
    *,
    family: str,
    parallel: bool = False,
) -> CapabilityProfile:
    return CapabilityProfile.model_validate(
        {
            "schema_version": 1,
            "physics_family": "fluid",
            "regime": "transient",
            "compressibility": (
                "compressible"
                if solver == "rhoCentralFoam"
                else "incompressible"
            ),
            "phase_family": (
                "vof" if solver == "interFoam" else "single_phase"
            ),
            "energy": (
                "enabled"
                if solver in {"rhoCentralFoam", "buoyantFoam"}
                else "disabled"
            ),
            "turbulence": "unknown",
            "solver_family": family,
            "solver_executable": solver,
            "mesh_family": "blockMesh",
            "parallel_expected": parallel,
            "confidence": "high",
            "evidence": [
                {
                    "source": "task.prompt",
                    "fact": f"explicit solver {solver}",
                }
            ],
            "unresolved_questions": [],
        }
    )


def test_context_selects_at_most_one_entry_per_slot_and_records_gaps():
    context = assemble_agent_context(
        _task(),
        _profile("interFoam", family="incompressible-vof"),
    )

    selected = [
        entry_id
        for entry_id in context.knowledge_slots.values()
        if entry_id is not None
    ]
    assert len(selected) == len(set(selected))
    assert len(selected) <= len(context.knowledge_slots)
    assert (
        context.knowledge_slots["solver_family_contract"]
        == "of10.solver.interfoam-vof-contract"
    )
    assert (
        context.knowledge_slots["startup_numerics"]
        == "of10.numerics.interfoam-alpha-boundedness"
    )
    assert "error_playbook" not in context.knowledge_slots
    assert "parallel_execution" not in context.knowledge_slots
    assert set(context.missing_slots) == {
        slot
        for slot, entry_id in context.knowledge_slots.items()
        if entry_id is None
    }
    assert "development_only" not in context.knowledge_text


def test_solver_agnostic_knowledge_requires_explicit_task_activation():
    task = _task().model_copy(
        update={
            "title": "Potential flow around a cylinder",
            "request_text": (
                "Use potentialFoam for incompressible irrotational flow "
                "around a stationary cylinder with a uniform Cartesian inlet."
            ),
            "required_outputs": ["velocity and pressure fields"],
            "acceptance_intent": ["potentialFoam completes"],
        }
    )

    context = assemble_agent_context(
        task,
        _profile("potentialFoam", family="incompressible-potential"),
    )

    assert (
        "of10.boundary.rotating-swirl-inlet-contract"
        not in context.selected_knowledge_ids
    )
    assert (
        "of10.function.scalartransport-contract"
        not in context.selected_knowledge_ids
    )
    assert context.knowledge_slots["boundary_condition_contract"] is None
    assert context.knowledge_slots["physics_transport_model"] is None


def test_activation_terms_admit_relevant_solver_agnostic_knowledge():
    task = _task().model_copy(
        update={
            "title": "Passive scalar in potential flow",
            "request_text": (
                "Use potentialFoam and its flux to transport a passive scalar "
                "tracer with constant diffusion."
            ),
            "required_outputs": ["finite passive scalar field"],
            "acceptance_intent": ["scalar transport completes"],
        }
    )

    context = assemble_agent_context(
        task,
        _profile("potentialFoam", family="incompressible-potential"),
    )

    assert (
        context.knowledge_slots["physics_transport_model"]
        == "of10.function.scalartransport-contract"
    )


def test_parallel_and_repair_slots_are_conditional():
    context = assemble_agent_context(
        _task(),
        _profile(
            "icoFoam",
            family="incompressible-laminar",
            parallel=True,
        ),
        repair=True,
    )

    assert "parallel_execution" in context.knowledge_slots
    assert "error_playbook" in context.knowledge_slots


def test_repair_evidence_routes_the_matching_error_playbook_only_in_repair():
    capability = _profile(
        "icoFoam",
        family="incompressible-laminar",
    )
    evidence = "Unable to set reference cell for field p"

    generation = assemble_agent_context(
        _task(),
        capability,
        repair_evidence=evidence,
    )
    repair = assemble_agent_context(
        _task(),
        capability,
        repair=True,
        repair_evidence=evidence,
    )

    assert "error_playbook" not in generation.knowledge_slots
    assert (
        repair.knowledge_slots["error_playbook"]
        == "of10.error.missing-pressure-reference"
    )


def test_repair_evidence_is_bounded_before_retrieval():
    context = assemble_agent_context(
        _task(),
        _profile("icoFoam", family="incompressible-laminar"),
        repair=True,
        repair_evidence=("irrelevant-prefix " * 10000)
        + "Unable to set reference cell for field p",
    )

    assert (
        context.knowledge_slots["error_playbook"]
        == "of10.error.missing-pressure-reference"
    )


def test_context_loads_general_and_at_most_one_matching_family_skill():
    rho = assemble_agent_context(
        _task(),
        _profile(
            "rhoCentralFoam",
            family="compressible-density-based",
        ),
    )
    generic = assemble_agent_context(
        _task(),
        _profile("interFoam", family="incompressible-vof"),
    )

    assert rho.skill_names == (
        "openfoam-author-native-case",
        "openfoam-compressible-transient",
    )
    assert "将 `deltaT` 视为初始步长" in rho.skills_text
    assert generic.skill_names == (
        "openfoam-author-native-case",
        "openfoam-multiphase-vof",
    )
    assert len(rho.skill_names) <= 2
    assert len(generic.skill_names) <= 2


@pytest.mark.parametrize(
    ("solver", "family", "knowledge_id", "skill_name"),
    [
        (
            "compressibleInterFoam",
            "compressible-vof",
            "of10.solver.compressibleinterfoam-contract",
            "openfoam-multiphase-vof",
        ),
        (
            "driftFluxFoam",
            "drift-flux",
            "of10.solver.driftfluxfoam-contract",
            "openfoam-multiphase-coupled",
        ),
        (
            "multiphaseEulerFoam",
            "multiphase-euler",
            "of10.solver.multiphaseeulerfoam-contract",
            "openfoam-multiphase-coupled",
        ),
        (
            "reactingFoam",
            "compressible-reacting",
            "of10.solver.reactingfoam-contract",
            "openfoam-compressible-transient",
        ),
    ],
)
def test_specialized_solver_context_pairs_guide_and_family_skill(
    solver: str,
    family: str,
    knowledge_id: str,
    skill_name: str,
) -> None:
    context = assemble_agent_context(
        _task(),
        _profile(solver, family=family),
    )

    assert context.knowledge_slots["solver_family_contract"] == knowledge_id
    assert context.skill_names == (
        "openfoam-author-native-case",
        skill_name,
    )
    assert len(context.skill_names) == 2


def test_payload_budget_prunes_whole_optional_entries_without_truncation():
    unrestricted = assemble_agent_context(
        _task(),
        _profile(
            "icoFoam",
            family="incompressible-laminar",
            parallel=True,
        ),
        repair=True,
    )
    skill_bytes = len(unrestricted.skills_text.encode("utf-8"))
    limited = assemble_agent_context(
        _task(),
        _profile(
            "icoFoam",
            family="incompressible-laminar",
            parallel=True,
        ),
        repair=True,
        payload_limit_bytes=skill_bytes + 3000,
    )

    payload = json.loads(limited.knowledge_text)
    assert isinstance(payload, list)
    assert {
        item["entry"]["id"] for item in payload
    } == set(limited.selected_knowledge_ids)
    assert len(limited.selected_knowledge_ids) < len(
        unrestricted.selected_knowledge_ids
    )
    assert (
        len(limited.knowledge_text.encode("utf-8"))
        + len(limited.skills_text.encode("utf-8"))
        <= skill_bytes + 3000
    )
