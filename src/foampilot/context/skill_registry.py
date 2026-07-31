"""Select one general Skill and at most one solver-family Skill."""

from __future__ import annotations

from pathlib import Path

from foampilot.routing import CapabilityProfile


GENERAL_SKILL = "openfoam-author-native-case"
FAMILY_SKILLS = {
    "buoyantFoam": "openfoam-buoyant-case",
    "rhoCentralFoam": "openfoam-rhocentral-case",
}


def select_skill_names(
    capability: CapabilityProfile,
) -> tuple[str, ...]:
    names = [GENERAL_SKILL]
    family = FAMILY_SKILLS.get(capability.solver_executable or "")
    if family is not None:
        names.append(family)
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
