from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

from pydantic import ValidationError
import pytest
import yaml

from foampilot.artifacts import ArtifactStore
from foampilot.inspection import (
    InspectionReport,
    inspect_native_case,
)
from foampilot.plans import (
    ExecutionPlan,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.plans.legacy import load_frozen_v2_plan
from foampilot.runtime import PlanRunResult
from foampilot.tasks import TaskSpec
from foampilot.validation import PublicValidationReport


FIXTURE_ROOT = Path(__file__).parent / "fixtures/artifact-replay"
INDEX = FIXTURE_ROOT / "index.yaml"
EXPECTED_KINDS = {
    "single_region_success",
    "mpi_success",
    "include_success",
    "buoyant_success",
    "multi_region_success",
    "known_failure",
}


def _index():
    return yaml.safe_load(INDEX.read_text(encoding="utf-8"))


def test_replay_index_has_six_distinct_public_fixture_kinds() -> None:
    payload = _index()
    fixtures = payload["fixtures"]

    assert payload["schema_version"] == 1
    assert {item["kind"] for item in fixtures} == EXPECTED_KINDS
    assert len({item["fixture_id"] for item in fixtures}) == 6
    assert all(
        not Path(item["fixture_id"]).is_absolute()
        for item in fixtures
    )


def test_replay_fixture_hashes_match_index() -> None:
    for fixture in _index()["fixtures"]:
        for item in fixture["files"]:
            path = FIXTURE_ROOT / fixture["fixture_id"] / item["path"]
            payload = path.read_bytes()
            assert len(payload) == item["bytes"]
            assert sha256(payload).hexdigest() == item["sha256"]


def _converted_plan(root: Path) -> ExecutionPlan:
    return load_frozen_v2_plan(
        (root / "execution-plan.json").read_text(encoding="utf-8"),
        (root / "case-manifest-overlay.json").read_text(
            encoding="utf-8"
        ),
    )


def _task(plan: ExecutionPlan) -> TaskSpec:
    total_timeout = sum(
        command.timeout_seconds for command in plan.commands
    )
    return TaskSpec.model_validate(
        {
            "schema_version": 1,
            "task_id": "frozen-replay",
            "title": "Frozen public artifact replay",
            "prompt": (
                f"Replay a reviewed {plan.manifest.regime} "
                f"{plan.manifest.physics_family} case with "
                f"{plan.manifest.solver_executable}."
            ),
            "openfoam_target": {
                "distribution": "foundation",
                "version": "10",
            },
            "resource_budget": {
                "max_attempts": 1,
                "max_wall_seconds": max(total_timeout, 1),
                "max_mpi_ranks": max(
                    command.mpi_ranks for command in plan.commands
                ),
                "memory_mib": 4096,
            },
            "required_outputs": ["reviewed replay artifacts"],
            "acceptance_requirements": ["preserve recorded result"],
            "public_checks": [
                {
                    "name": "recorded-result",
                    "kind": "completion",
                    "parameters": {},
                }
            ],
        }
    )


def test_direct_v2_validation_is_rejected() -> None:
    root = FIXTURE_ROOT / "single-region-success"
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate_json(
            (root / "execution-plan.json").read_text(encoding="utf-8")
        )


def test_every_frozen_v2_fixture_has_a_hashed_reviewed_overlay() -> None:
    for fixture in _index()["fixtures"]:
        paths = {item["path"] for item in fixture["files"]}
        assert "case-manifest-overlay.json" in paths
        assert (
            FIXTURE_ROOT
            / fixture["fixture_id"]
            / "case-manifest-overlay.json"
        ).is_file()


def test_frozen_artifacts_replay_current_typed_readers() -> None:
    for fixture in _index()["fixtures"]:
        root = FIXTURE_ROOT / fixture["fixture_id"]
        plan = _converted_plan(root)
        assert plan.schema_version == 3
        InspectionReport.model_validate_json(
            (root / "static-inspection.json").read_text(
                encoding="utf-8"
            )
        )
        PublicValidationReport.model_validate_json(
            (root / "public-validation.json").read_text(
                encoding="utf-8"
            )
        )
        run_result = root / "run-result.json"
        if run_result.is_file():
            PlanRunResult.model_validate_json(
                run_result.read_text(encoding="utf-8")
            )


def test_frozen_cases_replay_policy_and_semantic_inspection(
    tmp_path: Path,
) -> None:
    for fixture in _index()["fixtures"]:
        root = FIXTURE_ROOT / fixture["fixture_id"]
        copied_case = tmp_path / fixture["fixture_id"]
        shutil.copytree(root / "case", copied_case)
        plan = _converted_plan(root)
        task = _task(plan)
        available = {
            command.executable for command in plan.commands
        }
        normalized = normalize_execution_plan(
            plan,
            task,
            available,
        )

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
        if fixture["kind"] != "known_failure":
            assert inspection.passed, (
                fixture["fixture_id"],
                [issue.code for issue in inspection.issues],
            )

        public = PublicValidationReport.model_validate_json(
            (root / "public-validation.json").read_text(
                encoding="utf-8"
            )
        )
        if fixture["kind"] == "known_failure":
            assert not public.passed
            assert public.failure_layer == "PUBLIC_VALIDATION_FAILED"


def test_v1_summary_is_readable_but_not_resumable(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    run_dir = store.create_run()
    shutil.copy2(
        FIXTURE_ROOT / "single-region-success/summary.json",
        run_dir / "summary.json",
    )
    store.finalize(run_dir)

    summary = store.read_summary(run_dir)

    assert summary.native_status == "PUBLIC_VALIDATION_PASS"
    assert not summary.resume.allowed
    assert summary.resume.reason == "legacy summaries cannot resume"


def test_fixtures_contain_no_source_machine_paths() -> None:
    for path in FIXTURE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "/tmp/" not in text
        assert "/home/" not in text
        assert "tutorials" not in text.lower()
