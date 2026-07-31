from __future__ import annotations

from pathlib import Path

import foampilot
import pytest

from foampilot.agent.context import load_agent_context
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec


def _task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "task_id": "two-fluid-column",
            "title": "Two-fluid column",
            "prompt": (
                "Solve a transient free-surface collapse of two incompressible "
                "isothermal immiscible fluids using a regional phase fraction."
            ),
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 2,
                "max_wall_seconds": 420,
                "max_mpi_ranks": 1,
                "memory_mib": 3072,
            },
            "required_outputs": [
                "bounded phase-fraction evidence",
                "phase-volume conservation evidence",
            ],
            "acceptance_requirements": ["phase volume is conserved"],
            "public_checks": [
                {
                    "name": "conservation",
                    "kind": "conservation",
                    "parameters": {
                        "field": "alpha.water",
                        "maximum_normalized_error": 0.02,
                    },
                }
            ],
            "public_assets": [],
            "protected_paths": ["/private/tutorial/damBreak"],
        }
    )


def _capability() -> CapabilityProfile:
    return CapabilityProfile.model_validate(
        {
            "schema_version": 1,
            "physics_family": "fluid",
            "regime": "transient",
            "compressibility": "incompressible",
            "phase_family": "vof",
            "energy": "disabled",
            "turbulence": "laminar",
            "solver_family": "incompressible-vof",
            "solver_executable": "interFoam",
            "mesh_family": "blockMesh",
            "parallel_expected": False,
            "confidence": "high",
            "evidence": [
                {
                    "source": "task.prompt",
                    "fact": "explicit solver interFoam",
                }
            ],
            "unresolved_questions": [],
        }
    )


def test_context_dynamically_retrieves_public_vof_knowledge() -> None:
    context = load_agent_context(_task(), _capability())

    assert len(context.selected_knowledge_ids) <= 5
    assert "of10.solver.interfoam-vof-contract" in (
        context.selected_knowledge_ids
    )
    assert "of10.numerics.interfoam-alpha-boundedness" in (
        context.selected_knowledge_ids
    )
    assert "interFoam incompressible two-fluid VOF contract" in (
        context.knowledge_text
    )
    assert "constant/physicalProperties.<phase>" in context.knowledge_text
    assert "pcorrFinal" in context.knowledge_text
    assert "all-time extrema" in context.knowledge_text
    assert "maxAlphaCo" in context.knowledge_text
    assert "1.000001" not in context.knowledge_text
    assert "openfoam-author-native-case" in context.skills_text
    assert "VOF boundedness" in context.skills_text
    assert "add a safe generated case file" in context.skills_text
    assert "reviewed plan" not in context.skills_text
    assert "Generate files sequentially" not in context.skills_text
    assert "/private/tutorial/damBreak" not in (
        context.knowledge_text + context.skills_text
    )


def test_context_excludes_development_only_entries() -> None:
    context = load_agent_context(_task(), _capability())

    assert "development_only" not in context.knowledge_text


def test_context_can_load_from_an_explicit_package_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        load_agent_context(
            _task(),
            _capability(),
            package_root=tmp_path,
        )


def test_native_agent_resources_are_installed_inside_the_python_package() -> None:
    package = Path(foampilot.__file__).resolve().parent

    assert (
        package
        / "knowledge/openfoam10/numerics/interfoam-alpha-boundedness.yaml"
    ).is_file()
    assert (
        package / "skills/openfoam-author-native-case/SKILL.md"
    ).is_file()
