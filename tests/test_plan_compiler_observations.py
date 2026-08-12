from __future__ import annotations

from foampilot.observations import EvidenceStrategy, ObservationItem, ObservationPlan, ObservationScope, TimeSelection
from foampilot.simulation import FactEvidence
from tests.test_plan_compiler import _bundle, _context, _environment, _task
from foampilot.extensions import CapabilityRegistry
from foampilot.plans import compile_execution_plan
from foampilot.environment import CommandFact
from pathlib import Path


def test_postprocess_observation_command_is_system_compiled() -> None:
    item = ObservationItem(
        observation_id="outlet-flow",
        kind="flow_rate",
        quantity="volumetric_flow_rate",
        dimension="L3/T",
        scope=ObservationScope(kind="patch", names=("outlet",)),
        time_selection=TimeSelection(kind="final"),
        evidence_strategy=EvidenceStrategy(kind="postprocess_command", collector_id="foundation10.flow_rate"),
        provenance=(FactEvidence(kind="user_quote", detail="outlet flow"),),
    )
    context = _context()
    environment = _environment().model_copy(
        update={
            "commands": [
                *_environment().commands,
                CommandFact(
                    name="postProcess",
                    path=Path("/opt/openfoam/bin/postProcess"),
                ),
            ]
        }
    )
    plan = compile_execution_plan(
        design=context.design,
        bundle=_bundle(),
        environment=environment,
        task=_task(),
        registry=CapabilityRegistry.planning_first_party(),
        observation_plan=ObservationPlan(items=(item,)),
    )

    command = plan.commands[-1]
    assert command.stage == "postprocess"
    assert command.executable == "postProcess"
    assert command.mpi_ranks == 1
    assert command.args == ["-func", "surfaceFieldValue"]
