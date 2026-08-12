from __future__ import annotations

import pytest

from foampilot.context.skill_registry import (
    GENERAL_SKILL,
    select_skill_names,
)
from foampilot.routing import CapabilityProfile
from foampilot.tasks import MeshIntent
from tests.test_execution_plan import task as task_fixture
from tests.support.tasks import replace_explicit_fact


def _capability(solver: str) -> CapabilityProfile:
    return CapabilityProfile.model_validate(
        {
            "schema_version": 1,
            "physics_family": "fluid",
            "regime": "transient",
            "compressibility": "incompressible",
            "phase_family": "single_phase",
            "energy": "disabled",
            "turbulence": "laminar",
            "solver_family": "unspecified",
            "solver_executable": solver,
            "mesh_family": "blockMesh",
            "parallel_expected": False,
            "confidence": "high",
            "evidence": [],
            "unresolved_questions": [],
        }
    )


@pytest.mark.parametrize(
    ("solver", "family_skill"),
    [
        ("icoFoam", "openfoam-incompressible-pressure-velocity"),
        ("simpleFoam", "openfoam-incompressible-pressure-velocity"),
        ("pisoFoam", "openfoam-incompressible-pressure-velocity"),
        ("pimpleFoam", "openfoam-incompressible-pressure-velocity"),
        ("porousSimpleFoam", "openfoam-incompressible-pressure-velocity"),
        ("SRFPimpleFoam", "openfoam-incompressible-pressure-velocity"),
        ("SRFSimpleFoam", "openfoam-incompressible-pressure-velocity"),
        ("rhoCentralFoam", "openfoam-compressible-transient"),
        ("rhoPimpleFoam", "openfoam-compressible-transient"),
        ("reactingFoam", "openfoam-compressible-transient"),
        ("interFoam", "openfoam-multiphase-vof"),
        ("twoLiquidMixingFoam", "openfoam-multiphase-vof"),
        ("compressibleInterFoam", "openfoam-multiphase-vof"),
        ("driftFluxFoam", "openfoam-multiphase-coupled"),
        ("multiphaseEulerFoam", "openfoam-multiphase-coupled"),
        ("buoyantFoam", "openfoam-buoyant-cht"),
        ("chtMultiRegionFoam", "openfoam-buoyant-cht"),
        ("solidDisplacementFoam", "openfoam-solid-mechanics"),
        ("solidEquilibriumDisplacementFoam", "openfoam-solid-mechanics"),
        ("scalarTransportFoam", "openfoam-scalar-field-transport"),
        ("electrostaticFoam", "openfoam-scalar-field-transport"),
    ],
)
def test_registry_selects_one_family_skill(
    solver: str,
    family_skill: str,
) -> None:
    names = select_skill_names(_capability(solver))

    assert names == (GENERAL_SKILL, family_skill)
    assert len(names) <= 2


@pytest.mark.parametrize(
    "solver",
    ["mhdFoam", "potentialFoam", "shallowWaterFoam"],
)
def test_registry_leaves_narrow_unmapped_solvers_on_general_skill(
    solver: str,
) -> None:
    assert select_skill_names(_capability(solver)) == (GENERAL_SKILL,)


def test_registry_adds_mesh_skill_only_for_mesh_enabled_task() -> None:
    base = task_fixture.__wrapped__()
    payload = base.model_dump(mode="json")
    replace_explicit_fact(
        payload,
        "mesh.intent",
        MeshIntent(strategy="blockMesh").model_dump(mode="json"),
    )
    task = base.model_validate(payload)

    assert select_skill_names(_capability("icoFoam"), task=task) == (
        GENERAL_SKILL,
        "openfoam-incompressible-pressure-velocity",
        "openfoam-mesh-workflow",
    )
