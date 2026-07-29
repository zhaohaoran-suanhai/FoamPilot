from __future__ import annotations

from pathlib import Path

from foampilot.knowledge import (
    KnowledgeQuery,
    load_knowledge_corpus,
    select_knowledge,
    verify_knowledge_manifest,
)


PROJECT = Path(__file__).parents[1]
CORPUS = PROJECT / "src/foampilot/knowledge/openfoam10"
MANIFEST = PROJECT / "src/foampilot/knowledge/knowledge-manifest.json"


def test_reviewed_corpus_is_complete_frozen_and_has_no_target_solution() -> None:
    entries = load_knowledge_corpus(CORPUS)
    assert len(entries) == 18
    assert verify_knowledge_manifest(CORPUS, MANIFEST) == []
    boundedness = next(
        entry
        for entry in entries
        if entry.id == "of10.numerics.interfoam-alpha-boundedness"
    )
    assert boundedness.leakage.visibility == "public"
    assert boundedness.leakage.contains_target_case_solution is False
    assert {entry.knowledge_type for entry in entries} == {
        "solver_guide",
        "mesh_pattern",
        "boundary_condition",
        "physics_model",
        "numerics",
        "error_playbook",
        "parallel_execution",
        "validation_pattern",
    }
    serialized = "\n".join(entry.model_dump_json() for entry in entries)
    assert "/tutorials/" not in serialized
    assert "contains_target_case_solution\":true" not in serialized


def test_interfoam_knowledge_covers_v10_scheme_and_stability_margin() -> None:
    entries = load_knowledge_corpus(CORPUS)
    solver = next(
        entry
        for entry in entries
        if entry.id == "of10.solver.interfoam-vof-contract"
    )
    boundedness = next(
        entry
        for entry in entries
        if entry.id == "of10.numerics.interfoam-alpha-boundedness"
    )

    assert (
        "div(((rho*nuEff)*dev2(T(grad(U)))))"
        in solver.model_dump_json()
    )
    assert "strictly below" in boundedness.model_dump_json()


def test_retrieval_prefers_exact_solver_and_topic_deterministically() -> None:
    entries = load_knowledge_corpus(CORPUS)
    query = KnowledgeQuery(
        text="icoFoam PISO closed pressure reference",
        solver="icoFoam",
        knowledge_types=("numerics", "error_playbook"),
        limit=3,
    )
    first = select_knowledge(entries, query)
    second = select_knowledge(entries, query)
    assert first == second
    assert first
    assert first[0].entry_id == "of10.numerics.piso-closed-pressure-reference"
    assert len(first) <= 3


def test_detailed_rules_do_not_change_retrieval_relevance() -> None:
    entries = list(load_knowledge_corpus(CORPUS))
    query = KnowledgeQuery(
        text="incompressible transient icoFoam laminar sentinelword",
        limit=20,
    )
    baseline = {
        match.entry_id: match.score
        for match in select_knowledge(entries, query)
    }
    interfoam_index = next(
        index
        for index, entry in enumerate(entries)
        if entry.id == "of10.solver.interfoam-vof-contract"
    )
    entry = entries[interfoam_index]
    entries[interfoam_index] = entry.model_copy(
        update={
            "content": entry.content.model_copy(
                update={
                    "rules": [
                        *entry.content.rules,
                        "icoFoam laminar sentinelword",
                    ]
                }
            )
        }
    )

    modified = {
        match.entry_id: match.score
        for match in select_knowledge(entries, query)
    }
    assert modified[entry.id] == baseline[entry.id]


def test_shipped_corpus_is_public_and_formal_queries_fail_closed() -> None:
    entries = load_knowledge_corpus(CORPUS)
    formal = KnowledgeQuery(
        text="pilot physics golden validation gate",
        evaluation_family="new-holdout-family",
        formal=True,
    )
    assert all(
        match.visibility == "public"
        for match in select_knowledge(entries, formal)
    )

    unapproved_development = formal.model_copy(
        update={
            "formal": False,
        }
    )
    assert all(
        match.visibility == "public"
        for match in select_knowledge(entries, unapproved_development)
    )

    assert all(entry.leakage.visibility == "public" for entry in entries)


def test_type_and_solver_filters_are_applied_before_scoring() -> None:
    entries = load_knowledge_corpus(CORPUS)
    matches = select_knowledge(
        entries,
        KnowledgeQuery(
            text="pressure reference",
            solver="rhoCentralFoam",
            knowledge_types=("numerics",),
        ),
    )
    assert matches == ()
