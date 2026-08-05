from pathlib import Path

from foampilot.knowledge import (
    KnowledgeCoverageStatus,
    build_knowledge_coverage,
    load_knowledge_corpus,
)
from foampilot.cli.main import main


KNOWLEDGE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src/foampilot/knowledge/openfoam10"
)


def test_coverage_reports_all_reviewed_physics_families() -> None:
    report = build_knowledge_coverage(
        load_knowledge_corpus(KNOWLEDGE_ROOT)
    )

    assert report.schema_version == 1
    assert report.families == (
        "buoyant-cht",
        "compressible-transient",
        "incompressible-pressure-velocity",
        "multiphase-vof",
        "scalar-field-transport",
        "solid-mechanics",
    )
    assert report.knowledge_types == (
        "solver_guide",
        "mesh_pattern",
        "boundary_condition",
        "physics_model",
        "numerics",
        "error_playbook",
        "validation_pattern",
    )


def test_coverage_distinguishes_covered_partial_and_missing() -> None:
    report = build_knowledge_coverage(
        load_knowledge_corpus(KNOWLEDGE_ROOT)
    )
    cells = {
        (cell.family, cell.knowledge_type): cell
        for cell in report.cells
    }

    solid_solver = cells[("solid-mechanics", "solver_guide")]
    assert solid_solver.status == KnowledgeCoverageStatus.COVERED
    assert {
        "solidDisplacementFoam",
        "solidEquilibriumDisplacementFoam",
    }.issubset(set(solid_solver.covered_solvers))

    solid_errors = cells[("solid-mechanics", "error_playbook")]
    assert solid_errors.status == KnowledgeCoverageStatus.COVERED
    assert "of10.error.missing-fvscheme-operator" in (
        solid_errors.entry_ids
    )

    solid_numerics = cells[("solid-mechanics", "numerics")]
    assert solid_numerics.status == KnowledgeCoverageStatus.MISSING
    assert solid_numerics.entry_ids == ()


def test_activation_scoped_entries_do_not_claim_universal_coverage() -> None:
    report = build_knowledge_coverage(
        load_knowledge_corpus(KNOWLEDGE_ROOT)
    )
    cells = {
        (cell.family, cell.knowledge_type): cell
        for cell in report.cells
    }

    solid_boundary = cells[("solid-mechanics", "boundary_condition")]
    solid_physics = cells[("solid-mechanics", "physics_model")]
    assert "of10.boundary.rotating-swirl-inlet-contract" not in (
        solid_boundary.entry_ids
    )
    assert "of10.function.scalartransport-contract" not in (
        solid_physics.entry_ids
    )


def test_coverage_output_is_stably_sorted() -> None:
    report = build_knowledge_coverage(
        load_knowledge_corpus(KNOWLEDGE_ROOT)
    )

    assert report.cells == tuple(
        sorted(
            report.cells,
            key=lambda item: (
                item.family,
                report.knowledge_types.index(item.knowledge_type),
            ),
        )
    )


def test_knowledge_coverage_cli_emits_machine_report(capsys) -> None:
    code = main(
        [
            "knowledge",
            "coverage",
            str(KNOWLEDGE_ROOT),
            "--json",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert '"status": "PASS"' in output
    assert '"families"' in output
    assert '"cells"' in output
