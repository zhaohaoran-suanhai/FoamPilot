from __future__ import annotations

import os
from pathlib import Path

import pytest

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.plans import ExecutionPlan
from foampilot.runtime import RuntimeConfig
from foampilot.taskbuilder import (
    compile_task_draft,
    extract_task_draft,
    validate_task_draft,
)
from tests.test_native_case_generation import RecordingModel


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "tests/fixtures/gates/non-tutorial-side-driven-plan.json"


class FrozenExtractionGateway:
    primary_backend_id = "frozen-extraction"
    primary_model = "frozen-extraction"
    policy_sha256 = "a" * 64

    def __init__(self, payload) -> None:
        self.payload = payload

    def generate_structured(self, request, schema, *, budget, trace):
        del request, trace
        assert budget.stage == ModelStage.TASK_EXTRACTION
        return ModelResult(
            value=schema.model_validate(self.payload),
            logical_request_id="task-extraction-gate",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def _extracted_payload(request: str) -> dict:
    def fact(path, value):
        return {
            "path": path,
            "value": value,
            "source": "user_text",
            "evidence": request,
            "impact": "high",
            "confirmed": True,
        }

    return {
        "schema_version": 1,
        "facts": [
            fact("physics.regime", "transient"),
            fact("physics.compressibility", "incompressible"),
            fact("physics.phase_family", "single_phase"),
            fact("physics.energy", "disabled"),
            fact("physics.turbulence", "laminar"),
            fact("physics.solver", "icoFoam"),
            fact(
                "geometry",
                {
                    "mode": "parametric",
                    "dimensionality": "two_d",
                    "description": "0.10 m by 0.06 m enclosure",
                    "length_unit": "m",
                    "parameters": {
                        "width": {"value": 0.10, "unit": "m"},
                        "height": {"value": 0.06, "unit": "m"},
                        "thickness": {"value": 0.001, "unit": "m"},
                    },
                    "patch_roles": [
                        {"name": "movingWall", "role": "wall"},
                        {"name": "fixedWalls", "role": "wall"},
                        {"name": "frontAndBack", "role": "empty"},
                    ],
                },
            ),
            fact(
                "materials.fluid",
                {
                    "kinematic_viscosity": {
                        "value": 1.0e-4,
                        "unit": "m2/s",
                    }
                },
            ),
            fact(
                "boundaries",
                [
                    {
                        "patch": "movingWall",
                        "velocity": {"value": [0, 0.05, 0], "unit": "m/s"},
                    },
                    {"patch": "fixedWalls", "condition": "no-slip"},
                    {"patch": "frontAndBack", "condition": "empty"},
                ],
            ),
            fact("operating.end_time", {"value": 1.0, "unit": "s"}),
            fact("outputs.required", ["velocity field", "pressure field"]),
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


@pytest.mark.skipif(
    os.environ.get("FOAMPILOT_RUN_REAL_TASKBUILDER") != "1",
    reason="real TaskBuilder/OpenFOAM gate is opt-in",
)
def test_natural_language_compiles_and_uses_canonical_real_solver(
    tmp_path: Path,
) -> None:
    request = (
        "Use icoFoam to solve a transient laminar incompressible single-phase "
        "side-driven enclosure 0.10 m wide, 0.06 m high and 0.001 m thick. "
        "The movingWall velocity is (0 0.05 0) m/s, fixedWalls are no-slip, "
        "frontAndBack are empty, kinematic viscosity is 1e-4 m2/s, and the "
        "end time is 1.0 s. Preserve velocity and pressure fields."
    )
    draft = extract_task_draft(
        request,
        [],
        FrozenExtractionGateway(_extracted_payload(request)),
        budget=ModelBudgetLedger.start().open_stage(
            ModelStage.TASK_EXTRACTION,
            stage_deadline_seconds=30,
        ),
        trace=InMemoryModelTraceSink(),
        protected_paths=(
            str(RuntimeConfig.local_foundation_v10().tutorial_root),
        ),
    )
    compilation = compile_task_draft(validate_task_draft(draft))
    plan = ExecutionPlan.model_validate_json(PLAN.read_text(encoding="utf-8"))
    store = ArtifactStore(tmp_path / "runs")

    outcome = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=RuntimeConfig.local_foundation_v10(),
        artifact_store=store,
    ).solve(compilation.task)

    assert outcome.status == "PUBLIC_VALIDATION_PASS", outcome.summary
    assert (outcome.run_dir / "geometry-facts.json").is_file()
    assert (
        outcome.run_dir / "attempt-01/mesh-quality-report.json"
    ).is_file()
    assert store.verify(outcome.run_dir) == []
