from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from foampilot.artifacts import ArtifactStore
from foampilot.cli.main import main
from foampilot.simulation import (
    DesignCandidate,
    FactEvidence,
    ResolvedValue,
    SimulationIntent,
    Uncertainty,
    write_json_exclusive,
)
from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision
from foampilot.simulation.requirements import resolve_requirements
from foampilot.simulation.risk_gate import RiskDecision, evaluate_design_risk
from foampilot.workflow.confirmation import (
    ConfirmationError,
    apply_confirmation_records,
    load_confirmation_parent,
    parse_answers,
    persist_confirmation_continuation,
)


def _evidence(detail: str = "model candidate") -> tuple[FactEvidence, ...]:
    return (FactEvidence(kind="test_fact", detail=detail),)


def _model_value(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="model_inference",
        impact="high",
        evidence=_evidence(),
        confirmed=False,
    )


def _proposal(*, include_information_gap: bool = False) -> CaseDesignProposal:
    uncertainties = ()
    if include_information_gap:
        uncertainties = (
            Uncertainty(
                question_id="provide-region-role",
                field_path="regions.unknown.role",
                impact="high",
                kind="information_required",
                prompt_zh="请提供区域物理语义。",
                reason_zh="没有唯一安全候选。",
            ),
        )
    return CaseDesignProposal(
        solver_family=ResolvedValue(
            field_path="solver.family",
            value="pisoFoam",
            source="user_text",
            impact="high",
            evidence=_evidence("explicit solver"),
            confirmed=True,
        ),
        physical_models=(),
        materials=(
            _model_value(
                "materials.fluid.nu",
                {"value": 1e-6, "unit": "m2/s"},
            ),
        ),
        boundary_designs=(),
        initial_conditions=(),
        time_design=(),
        numerical_design=(),
        region_models=(),
        extension_decisions=(
            ExtensionDecision(
                extension_id="foampilot.solver.piso",
                schema_version=1,
                values=(),
                provenance=_evidence("bound solver extension"),
            ),
        ),
        uncertainties=uncertainties,
        alternatives=(),
        reasoning_evidence=_evidence("design reasoning"),
        capability_conflicts=(),
    )


def _risk(proposal: CaseDesignProposal) -> RiskDecision:
    intent = SimulationIntent()
    requirements = resolve_requirements(
        intent=intent,
        mesh_facts=(),
        capabilities=(),
    )
    return evaluate_design_risk(
        intent=intent,
        requirements=requirements,
        proposal=proposal,
        bound_extension_identities={
            "foampilot.solver.piso": "1.0.0/protocol-1"
        },
    )


def _parent(tmp_path: Path, *, information: bool = False) -> Path:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    intent = SimulationIntent()
    requirements = resolve_requirements(
        intent=intent,
        mesh_facts=(),
        capabilities=(),
    )
    proposal = _proposal(include_information_gap=information)
    decision = _risk(proposal)
    write_json_exclusive(run_dir / "simulation-intent.json", intent)
    write_json_exclusive(run_dir / "resolved-requirements.json", requirements)
    write_json_exclusive(run_dir / "case-design-proposal.json", proposal)
    write_json_exclusive(run_dir / "risk-decision.json", decision)
    write_json_exclusive(run_dir / "questions.json", decision)
    store.finalize(run_dir)
    return run_dir


def _answers(parent) -> dict[str, object]:
    question = parent.decision.questions[0]
    candidate = question.candidates[0]
    return {
        "schema_version": 1,
        "answers": [
            {
                "question_id": question.question_id,
                "candidate_id": candidate.candidate_id,
                "confirmed_value": candidate.value,
            }
        ],
    }


def test_confirmation_requires_exact_candidate_id_and_value(tmp_path: Path) -> None:
    parent = load_confirmation_parent(_parent(tmp_path))
    payload = _answers(parent)
    payload["answers"][0]["confirmed_value"] = 0.5  # type: ignore[index]

    with pytest.raises(ConfirmationError, match="CONFIRMATION_VALUE_MISMATCH"):
        apply_confirmation_records(parent, parse_answers(payload))


@pytest.mark.parametrize("answer", ["continue", "accept_all", "use_model_judgement"])
def test_generic_confirmation_is_not_an_api(answer: str) -> None:
    with pytest.raises(ConfirmationError, match="CONCRETE_CONFIRMATION_REQUIRED"):
        parse_answers({"action": answer})


def test_duplicate_or_missing_answers_are_rejected(tmp_path: Path) -> None:
    parent = load_confirmation_parent(_parent(tmp_path))
    payload = _answers(parent)
    payload["answers"] = [*payload["answers"], *payload["answers"]]  # type: ignore[index]
    with pytest.raises(ConfirmationError, match="DUPLICATE_CONFIRMATION_ANSWER"):
        parse_answers(payload)

    with pytest.raises(ConfirmationError, match="CONFIRMATION_ANSWER_MISSING"):
        apply_confirmation_records(
            parent,
            parse_answers({"schema_version": 1, "answers": []}),
        )


def test_information_required_question_cannot_be_confirmed(tmp_path: Path) -> None:
    parent = load_confirmation_parent(_parent(tmp_path, information=True))

    with pytest.raises(ConfirmationError, match="INFORMATION_REQUIRED"):
        apply_confirmation_records(
            parent,
            parse_answers({"schema_version": 1, "answers": []}),
        )


def test_tampered_parent_and_proposal_hash_are_rejected(tmp_path: Path) -> None:
    run_dir = _parent(tmp_path)
    (run_dir / "case-design-proposal.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ConfirmationError, match="PARENT_MANIFEST_INVALID"):
        load_confirmation_parent(run_dir)

    run_dir = _parent(tmp_path / "second")
    manifest = run_dir / "artifact-manifest.json"
    decision_path = run_dir / "risk-decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["proposal_sha256"] = "0" * 64
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    manifest.unlink()
    ArtifactStore(run_dir.parent).finalize(run_dir)
    with pytest.raises(ConfirmationError, match="PROPOSAL_HASH_MISMATCH"):
        load_confirmation_parent(run_dir)


def test_one_record_per_field_and_child_freezes_ready_design(tmp_path: Path) -> None:
    parent = load_confirmation_parent(_parent(tmp_path))
    continuation = apply_confirmation_records(
        parent,
        parse_answers(_answers(parent)),
        answered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    assert len(continuation.records) == 1
    assert continuation.records[0].field_path == "materials.fluid.nu"
    assert continuation.proposal.materials[0].source == "user_confirmation"
    assert continuation.decision.state == "READY_TO_AUTHOR"
    assert continuation.design is not None
    assert continuation.design.confirmation_ids == (
        continuation.records[0].confirmation_id,
    )

    child = persist_confirmation_continuation(
        continuation,
        run_root=tmp_path / "children",
    )
    assert child != parent.run_dir
    assert ArtifactStore(child.parent).verify(child) == []
    lineage = json.loads((child / "lineage.json").read_text(encoding="utf-8"))
    assert lineage["relation"] == "design_confirmation"
    assert lineage["parent_manifest_sha256"] == parent.parent_manifest_sha256
    assert lineage["confirmation_record_hashes"] == list(
        continuation.confirmation_record_hashes
    )
    assert (child / "case-design.json").is_file()


def test_questions_and_confirm_cli_round_trip(tmp_path: Path, capsys) -> None:
    run_dir = _parent(tmp_path)
    assert main(["questions", str(run_dir), "--json"]) == 0
    questions = json.loads(capsys.readouterr().out)
    assert questions["status"] == "CONFIRMATION_REQUIRED"
    question = questions["questions"][0]

    answers = tmp_path / "answers.yaml"
    answers.write_text(
        "schema_version: 1\nanswers:\n"
        f"  - question_id: {question['question_id']}\n"
        f"    candidate_id: {question['candidates'][0]['candidate_id']}\n"
        "    confirmed_value:\n"
        "      value: 1.0e-6\n"
        "      unit: m2/s\n",
        encoding="utf-8",
    )
    child_root = tmp_path / "confirmed-runs"
    assert main(
        [
            "confirm",
            str(run_dir),
            "--answers",
            str(answers),
            "--run-root",
            str(child_root),
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "READY_TO_AUTHOR"
    assert Path(payload["run_dir"]).is_dir()
