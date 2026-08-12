from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foampilot.context import assemble_agent_context
from foampilot.environment import discover_environment
from foampilot.extensions import CapabilityDescriptor, CapabilityRegistry, SupportedTarget
from foampilot.knowledge import load_knowledge_corpus
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelGateway,
    ModelStage,
    load_backend_registry,
)
from foampilot.preprocessing import inspect_poly_mesh
from foampilot.routing import route_capability
from foampilot.runtime import run_preflight
from foampilot.simulation import (
    design_case,
    evaluate_design_risk,
    freeze_case_design,
    interpret_intent,
    resolve_requirements,
)
from foampilot.tasks import inspect_public_assets, load_task_spec
from tests.support.runtime import real_runtime_config


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "examples/tasks/provided-poly-mesh.yaml"


@pytest.mark.real_openfoam
@pytest.mark.skipif(
    os.environ.get("OFKIT_RUN_REAL_MODEL") != "1",
    reason="real contract-first model gate is opt-in",
)
def test_real_model_reaches_frozen_design_or_precise_questions(
    tmp_path: Path,
) -> None:
    task = load_task_spec(TASK)
    runtime = real_runtime_config()
    preflight = run_preflight(runtime, workspace_root=tmp_path)
    if not preflight.ok or preflight.environment is None:
        pytest.skip(
            "OPENFOAM10_NOT_AVAILABLE: "
            + str(preflight.failure_code or preflight.failure_message)
        )
    environment = discover_environment(runtime, tmp_path)
    capability = route_capability(
        task,
        environment,
        load_knowledge_corpus(ROOT / "src/foampilot/knowledge/openfoam10"),
    )
    assert capability.solver_executable is not None
    token = capability.solver_executable.casefold()
    registry = CapabilityRegistry()
    descriptor = CapabilityDescriptor(
        extension_id=f"foampilot.bridge.solver.{token}",
        extension_version="1.0.0",
        capability_kinds=(f"solver:{token}",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        required_executables=(capability.solver_executable,),
    )
    registry.register(descriptor, capability)

    public_root = ROOT / "examples"
    bundles = tuple(inspect_public_assets(task, public_root))
    mesh_facts = tuple(
        inspect_poly_mesh(
            public_root / bundle.source_path,
            bundle,
            length_unit=task.geometry.length_unit,
        )
        for bundle in bundles
        if bundle.kind == "openfoam_poly_mesh"
    )
    context = assemble_agent_context(task, capability)
    model_name = os.environ.get("OFKIT_CODEX_MODEL", "gpt-5.6-sol")
    gateway = ModelGateway(
        registry=load_backend_registry(None, default_model=model_name),
    )
    ledger = ModelBudgetLedger.start(total_model_deadline_seconds=420)
    trace = InMemoryModelTraceSink()

    intent = interpret_intent(
        task,
        asset_facts=bundles,
        mesh_facts=mesh_facts,
        capability_kinds=descriptor.capability_kinds,
        gateway=gateway,
        budget=ledger.open_stage(
            ModelStage.INTENT_INTERPRETATION,
            stage_deadline_seconds=180,
            max_transport_attempts=2,
        ),
        trace=trace,
    )
    requirements = resolve_requirements(
        intent=intent,
        mesh_facts=mesh_facts,
        capabilities=(descriptor,),
    )
    proposal = design_case(
        task=task,
        intent=intent,
        requirements=requirements,
        mesh_facts=mesh_facts,
        registry=registry,
        context=context,
        available_executables=tuple(environment.available_executable_names),
        gateway=gateway,
        budget=ledger.open_stage(
            ModelStage.CASE_DESIGN,
            stage_deadline_seconds=240,
            max_transport_attempts=2,
        ),
        trace=trace,
    )
    decision = evaluate_design_risk(
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        registry=registry,
    )

    assert decision.state in {
        "READY_TO_AUTHOR",
        "CONFIRMATION_REQUIRED",
        "INFORMATION_REQUIRED",
        "CAPABILITY_UNAVAILABLE",
    }
    serialized = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
    assert "accept_all" not in serialized
    assert "continue_anyway" not in serialized
    if decision.state == "READY_TO_AUTHOR":
        design = freeze_case_design(
            proposal=proposal,
            decision=decision,
            intent=intent,
        )
        assert len(design.design_sha256) == 64
    elif decision.state in {"CONFIRMATION_REQUIRED", "INFORMATION_REQUIRED"}:
        assert decision.questions
        assert all(question.field_path for question in decision.questions)
        assert all(question.reason_zh for question in decision.questions)
        assert all(
            question.candidates
            for question in decision.questions
            if question.kind == "confirmable"
        )
    purposes = [attempt.purpose for attempt in trace.attempts]
    assert purposes
    assert set(purposes) == {
        "interpret-simulation-intent",
        "design-openfoam-case",
    }
    first_design = purposes.index("design-openfoam-case")
    assert all(
        purpose == "interpret-simulation-intent"
        for purpose in purposes[:first_design]
    )
    assert all(
        purpose == "design-openfoam-case"
        for purpose in purposes[first_design:]
    )
