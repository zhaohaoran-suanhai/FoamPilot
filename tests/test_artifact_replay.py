from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

import yaml

from foampilot.artifacts import ArtifactStore
from foampilot.inspection import InspectionReport, inspect_native_case
from foampilot.plans import (
    ExecutionPlan,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.runtime import PlanRunResult
from foampilot.tasks import TaskSpec
from foampilot.validation import PublicValidationReport


PROJECT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT / "tests/fixtures/artifact-replay"
INDEX = FIXTURE_ROOT / "index.yaml"
GENERATOR = PROJECT / "tools/generate_synthetic_replay.py"
EXPECTED_KINDS = {
    "single_region_success",
    "mpi_success",
    "include_success",
    "buoyant_success",
    "multi_region_success",
    "known_failure",
}


def _index() -> dict:
    return yaml.safe_load(INDEX.read_text(encoding="utf-8"))


def test_replay_index_has_six_owned_fixture_kinds() -> None:
    payload = _index()
    fixtures = payload["fixtures"]

    assert payload["schema_version"] == 2
    assert {item["kind"] for item in fixtures} == EXPECTED_KINDS
    assert len({item["fixture_id"] for item in fixtures}) == 6
    assert {item["source_kind"] for item in fixtures} == {
        "synthetic_foampilot"
    }


def test_replay_fixture_and_generator_hashes_match_index() -> None:
    generator_hash = sha256(GENERATOR.read_bytes()).hexdigest()
    for fixture in _index()["fixtures"]:
        assert fixture["generator_sha256"] == generator_hash
        for item in fixture["files"]:
            path = FIXTURE_ROOT / fixture["fixture_id"] / item["path"]
            payload = path.read_bytes()
            assert len(payload) == item["bytes"]
            assert sha256(payload).hexdigest() == item["sha256"]


def test_synthetic_artifacts_replay_current_typed_readers() -> None:
    for fixture in _index()["fixtures"]:
        root = FIXTURE_ROOT / fixture["fixture_id"]
        plan = ExecutionPlan.model_validate_json(
            (root / "execution-plan.json").read_text(encoding="utf-8")
        )
        task = TaskSpec.model_validate_json(
            (root / "task.json").read_text(encoding="utf-8")
        )
        assert plan.schema_version == 3
        assert task.task_id == fixture["fixture_id"]
        InspectionReport.model_validate_json(
            (root / "static-inspection.json").read_text(encoding="utf-8")
        )
        PublicValidationReport.model_validate_json(
            (root / "public-validation.json").read_text(encoding="utf-8")
        )
        PlanRunResult.model_validate_json(
            (root / "run-result.json").read_text(encoding="utf-8")
        )


def test_synthetic_cases_replay_policy_and_semantic_inspection(
    tmp_path: Path,
) -> None:
    for fixture in _index()["fixtures"]:
        root = FIXTURE_ROOT / fixture["fixture_id"]
        copied_case = tmp_path / fixture["fixture_id"]
        shutil.copytree(root / "case", copied_case)
        plan = ExecutionPlan.model_validate_json(
            (root / "execution-plan.json").read_text(encoding="utf-8")
        )
        task = TaskSpec.model_validate_json(
            (root / "task.json").read_text(encoding="utf-8")
        )
        available = {command.executable for command in plan.commands}
        normalized = normalize_execution_plan(plan, task, available)

        assert validate_execution_plan(
            normalized.plan,
            task,
            available,
        ) == []
        inspection = inspect_native_case(
            case_root=copied_case,
            task=task,
            plan=normalized.plan,
            available_executables=available,
        )
        codes = {issue.code for issue in inspection.issues}
        if fixture["kind"] == "known_failure":
            assert codes == {"SEMANTIC_FIELD_DIMENSIONS_MISMATCH"}
        else:
            assert inspection.passed, (fixture["fixture_id"], sorted(codes))

        recorded = InspectionReport.model_validate_json(
            (root / "static-inspection.json").read_text(encoding="utf-8")
        )
        assert recorded == inspection


def test_v1_synthetic_summary_is_readable_but_not_resumable(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    shutil.copy2(
        FIXTURE_ROOT / "fp-single-box/summary.json",
        run_dir / "summary.json",
    )
    store.finalize(run_dir)

    summary = store.read_summary(run_dir)

    assert summary.native_status == "PUBLIC_VALIDATION_PASS"
    assert not summary.resume.allowed
    assert summary.resume.reason == "legacy summaries cannot resume"


def test_fixtures_contain_no_external_source_paths() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    ).lower()

    assert "/tmp/" not in text
    assert "/home/" not in text
    assert "tutorial" not in text
    assert "source_manifest_sha256" not in text
