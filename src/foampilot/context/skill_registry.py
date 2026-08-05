"""Select one general Skill and at most one solver-family Skill."""

from __future__ import annotations

from pathlib import Path

from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec


GENERAL_SKILL = "openfoam-author-native-case"
MESH_SKILL = "openfoam-mesh-workflow"
FAMILY_SKILLS = {
    "icoFoam": "openfoam-incompressible-pressure-velocity",
    "pimpleFoam": "openfoam-incompressible-pressure-velocity",
    "pisoFoam": "openfoam-incompressible-pressure-velocity",
    "porousSimpleFoam": "openfoam-incompressible-pressure-velocity",
    "simpleFoam": "openfoam-incompressible-pressure-velocity",
    "SRFPimpleFoam": "openfoam-incompressible-pressure-velocity",
    "SRFSimpleFoam": "openfoam-incompressible-pressure-velocity",
    "rhoCentralFoam": "openfoam-compressible-transient",
    "rhoPimpleFoam": "openfoam-compressible-transient",
    "rhoSimpleFoam": "openfoam-compressible-transient",
    "interFoam": "openfoam-multiphase-vof",
    "twoLiquidMixingFoam": "openfoam-multiphase-vof",
    "buoyantFoam": "openfoam-buoyant-cht",
    "chtMultiRegionFoam": "openfoam-buoyant-cht",
    "solidDisplacementFoam": "openfoam-solid-mechanics",
    "solidEquilibriumDisplacementFoam": "openfoam-solid-mechanics",
    "electrostaticFoam": "openfoam-scalar-field-transport",
    "scalarTransportFoam": "openfoam-scalar-field-transport",
}


def select_skill_names(
    capability: CapabilityProfile,
    *,
    task: TaskSpec | None = None,
) -> tuple[str, ...]:
    names = [GENERAL_SKILL]
    family = FAMILY_SKILLS.get(capability.solver_executable or "")
    if family is not None:
        names.append(family)
    if task is not None and (task.geometry is not None or task.mesh is not None):
        names.append(MESH_SKILL)
    return tuple(names)


def read_skills(
    root: Path,
    names: tuple[str, ...],
) -> str:
    documents: list[str] = []
    for name in names:
        path = root / name / "SKILL.md"
        if not path.is_file():
            raise FileNotFoundError(f"Agent Skill is missing: {path}")
        documents.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(documents)
