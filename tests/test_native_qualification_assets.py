from __future__ import annotations

import json
from pathlib import Path

from foampilot.agent import load_agent_context
from foampilot.knowledge import load_knowledge_corpus
from foampilot.tasks import load_task_spec


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = PACKAGE_ROOT / "examples" / "qualification"
EXPECTED_TASK_IDS = {
    "buoyant-cavity",
    "compressible-shock-tube",
    "laminar-cavity",
    "multiphase-dam-break",
    "potential-cylinder",
    "rans-pitzdaily",
}
EXPECTED_SOLVER_GUIDES = {
    "buoyant-cavity": "of10.solver.buoyantfoam-contract",
    "compressible-shock-tube": "of10.solver.rhocentralfoam-contract",
    "laminar-cavity": "of10.solver.icofoam-contract",
    "multiphase-dam-break": "of10.solver.interfoam-vof-contract",
    "potential-cylinder": "of10.solver.potentialfoam-contract",
    "rans-pitzdaily": "of10.solver.simplefoam-rans-contract",
}


def test_native_qualification_has_exactly_six_safe_task_specs() -> None:
    paths = sorted(TASK_ROOT.glob("*.yaml"))
    tasks = [load_task_spec(path) for path in paths]

    assert {task.task_id for task in tasks} == EXPECTED_TASK_IDS
    assert len(tasks) == len(EXPECTED_TASK_IDS)
    for task in tasks:
        assert task.openfoam_target.distribution == "foundation"
        assert task.openfoam_target.version == "10"
        assert task.public_checks
        assert any(
            path == Path("/home/edwin/workplace/OpenFOAM-10/tutorials")
            for path in map(Path, task.protected_paths)
        )
        visible = json.dumps(
            task.agent_payload(),
            ensure_ascii=False,
        ).casefold()
        assert "golden" not in visible
        assert "private validator" not in visible
        assert "/home/edwin/workplace/openfoam-10/tutorials" not in visible


def test_each_qualification_task_retrieves_its_public_solver_contract() -> None:
    for path in sorted(TASK_ROOT.glob("*.yaml")):
        task = load_task_spec(path)
        context = load_agent_context(task)

        assert EXPECTED_SOLVER_GUIDES[task.task_id] in (
            context.selected_knowledge_ids
        )
        assert context.selected_knowledge_ids


def test_native_authoring_skill_uses_only_execution_plan_v2_fields() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "unresolved_inputs" not in skill


def test_native_authoring_skill_keeps_optional_diagnostics_external() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "The Runner owns MPI launchers" in skill
    assert "Keep optional diagnostics outside the required solve plan." in skill
    assert (
        "configure native volume-field-value function objects" not in skill
    )


def test_solver_contracts_cover_observed_foundation_v10_failure_shields() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(
            PACKAGE_ROOT
            / "src"
            / "foampilot"
            / "knowledge"
            / "openfoam10"
        )
    }

    expected_rules = {
        "of10.solver.potentialfoam-contract": (
            "div(div(phi,U))",
        ),
        "of10.solver.simplefoam-rans-contract": (
            "executable simpleFoam",
            "mpi_ranks",
        ),
        "of10.solver.interfoam-vof-contract": (
            "[1 -1 -2 0 0 0 0]",
            "prghTotalPressure",
        ),
        "of10.solver.rhocentralfoam-contract": (
            "timeFormat general",
            "initial directory named 0",
        ),
        "of10.solver.buoyantfoam-contract": (
            "div(phi,K)",
            "single scalar values",
        ),
    }
    for entry_id, fragments in expected_rules.items():
        rules = "\n".join(entries[entry_id].content.rules)
        for fragment in fragments:
            assert fragment in rules
