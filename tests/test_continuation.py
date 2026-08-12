from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from foampilot.agent import NativeAgent
from foampilot.agent.repair_patch import RepairPatch
from foampilot.artifacts import ArtifactStore
from foampilot.environment import CommandFact
from foampilot.plans import GeneratedFile
from foampilot.runtime import RuntimeConfig
from foampilot.workflow import ResumeCompatibilityError
from foampilot.workflow.lineage import LineageRecord
from foampilot.assets import BundleMember, compute_bundle_manifest_sha256
from foampilot.workflow.lineage import build_resume_fingerprint

from tests.test_native_agent_state_machine import (
    SequencePlanRunner,
    _control_dict,
    _environment,
    _runtime_config,
    _task,
    _transport_failure,
)
from tests.test_native_case_generation import RecordingModel, _plan


POLY_MESH_FIXTURE = Path(__file__).parent / "fixtures/poly_mesh/minimal"


def _agent(
    *,
    root: Path,
    replies: list,
    runner: SequencePlanRunner,
    knowledge_text: str | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> NativeAgent:
    return NativeAgent(
        gateway=RecordingModel(replies),
        runtime_config=runtime_config or _runtime_config(),
        artifact_store=ArtifactStore(root),
        environment_snapshot=_environment("blockMesh", "checkMesh", "icoFoam"),
        runner=runner,
        knowledge_text=knowledge_text,
    )


def _repair() -> RepairPatch:
    return RepairPatch(
        because="The solver log identifies a missing stable time step.",
        evidence=["FOAM FATAL ERROR: missing keyword"],
        file_operations=[
            {
                "operation": "replace",
                "path": "system/controlDict",
                "content": _control_dict(delta_t=0.001),
            }
        ],
        command_operations=[],
        expected_check="The solver reaches End.",
        stable_control="Mesh and boundary conditions are unchanged.",
    )


def _deferred_parent(root: Path):
    return _agent(
        root=root,
        replies=[_plan(), _transport_failure()],
        runner=SequencePlanRunner(
            [(1, "", "Courant number 10\nfloating point exception")]
        ),
    ).solve(_task())


def test_resume_repair_creates_child_and_preserves_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    parent_manifest = (
        parent.run_dir / "artifact-manifest.json"
    ).read_bytes()

    child = _agent(
        root=root,
        replies=[_repair()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).resume(parent.run_dir)

    assert child.run_dir != parent.run_dir
    assert (
        parent.run_dir / "artifact-manifest.json"
    ).read_bytes() == parent_manifest
    assert ArtifactStore(root).verify(parent.run_dir) == []
    assert child.summary.parent_run is not None
    assert child.summary.parent_run.run_id == parent.run_dir.name
    assert child.summary.workflow_state == "COMPLETED"
    assert child.summary.native_status == "PUBLIC_VALIDATION_PASS"


def test_strict_resume_supports_an_external_job_artifact_root(
    tmp_path: Path,
) -> None:
    parent_root = tmp_path / "job-parent"
    child_root = tmp_path / "job-child"
    parent = _deferred_parent(parent_root)
    parent_manifest = (parent.run_dir / "artifact-manifest.json").read_bytes()

    child = _agent(
        root=child_root,
        replies=[_repair()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).resume(parent.run_dir)

    assert child.run_dir.parent == child_root.resolve()
    assert ArtifactStore(child_root).verify(child.run_dir) == []
    assert (parent.run_dir / "artifact-manifest.json").read_bytes() == parent_manifest
    lineage = json.loads(
        (child.run_dir / "lineage.json").read_text(encoding="utf-8")
    )
    assert lineage["relation"] == "strict_resume"
    assert lineage["parent_run_id"] == parent.run_dir.name
    assert lineage["input_hash_before"] == lineage["input_hash_after"]
    assert "continuation-evidence/execution-plan.json" in lineage[
        "reused_evidence_paths"
    ]


def test_cross_job_resume_keeps_cumulative_continuation_budget(
    tmp_path: Path,
) -> None:
    parent = _deferred_parent(tmp_path / "job-parent")
    child_one = _agent(
        root=tmp_path / "job-child-one",
        replies=[_transport_failure()],
        runner=SequencePlanRunner([]),
    ).resume(parent.run_dir)
    child_two = _agent(
        root=tmp_path / "job-child-two",
        replies=[_transport_failure()],
        runner=SequencePlanRunner([]),
    ).resume(child_one.run_dir)

    first = json.loads(
        (child_one.run_dir / "continuation.json").read_text(encoding="utf-8")
    )
    second = json.loads(
        (child_two.run_dir / "continuation.json").read_text(encoding="utf-8")
    )
    assert first["continuation_counts"]["MODEL_REPAIR_STARTED"] == 1
    assert second["continuation_counts"]["MODEL_REPAIR_STARTED"] == 2
    assert second["transport_attempts_used_before_child"] >= first[
        "transport_attempts_used_before_child"
    ]
    assert child_two.summary.resume.allowed is False


def test_cross_job_resume_passes_prior_execution_time_to_runner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    run_result_path = parent.run_dir / "attempt-01/run-result.json"
    run_result = json.loads(run_result_path.read_text(encoding="utf-8"))
    started = datetime.now(timezone.utc)
    run_result["steps"][0]["started_at"] = started.isoformat()
    run_result["steps"][0]["finished_at"] = (
        started - timedelta(seconds=100)
    ).isoformat()
    run_result["steps"][0]["elapsed_seconds"] = 12.5
    run_result_path.write_text(
        json.dumps(run_result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (parent.run_dir / "artifact-manifest.json").unlink()
    ArtifactStore(root).finalize(parent.run_dir)
    runner = SequencePlanRunner([(0, "Time = 1\nEnd\n", "")])

    child = _agent(
        root=root,
        replies=[_repair()],
        runner=runner,
    ).resume(parent.run_dir)

    assert child.summary.workflow_state == "COMPLETED"
    assert runner.execution_seconds_used_values == [pytest.approx(12.5)]


def test_rerun_same_input_is_a_cold_child_with_lineage(tmp_path: Path) -> None:
    parent = _agent(
        root=tmp_path / "job-parent",
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).solve(_task())
    parent_manifest = (parent.run_dir / "artifact-manifest.json").read_bytes()

    child = _agent(
        root=tmp_path / "job-rerun",
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).rerun(parent.run_dir)

    lineage = json.loads(
        (child.run_dir / "lineage.json").read_text(encoding="utf-8")
    )
    assert lineage["relation"] == "rerun_same_input"
    assert lineage["change_categories"] == []
    assert lineage["reused_evidence_paths"] == []
    assert lineage["input_hash_before"] == lineage["input_hash_after"]
    assert child.summary.parent_run is None
    assert (parent.run_dir / "artifact-manifest.json").read_bytes() == parent_manifest


def test_lineage_contract_does_not_advertise_openfoam_continuation() -> None:
    relation_schema = LineageRecord.model_json_schema()["properties"][
        "relation"
    ]

    assert "openfoam_continuation" not in relation_schema["enum"]
    assert "design_confirmation" in relation_schema["enum"]


def test_rerun_changed_task_is_explicitly_classified(tmp_path: Path) -> None:
    parent = _agent(
        root=tmp_path / "job-parent",
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).solve(_task())
    changed = _task().model_copy(update={"task_id": "changed-task"})

    child = _agent(
        root=tmp_path / "job-rerun",
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).rerun(parent.run_dir, task=changed)

    lineage = json.loads(
        (child.run_dir / "lineage.json").read_text(encoding="utf-8")
    )
    assert lineage["relation"] == "rerun_with_changes"
    assert lineage["change_categories"] == ["task"]
    assert lineage["input_hash_before"] != lineage["input_hash_after"]


def test_resume_rejects_changed_knowledge(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)

    with pytest.raises(
        ResumeCompatibilityError,
        match="knowledge_hash changed",
    ):
        _agent(
            root=root,
            replies=[_repair()],
            runner=SequencePlanRunner([]),
            knowledge_text="changed knowledge",
        ).resume(parent.run_dir)


def test_resume_rejects_changed_runtime_policy(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    changed_runtime = _runtime_config().model_copy(
        update={"isolation": "trusted_host"}
    )

    with pytest.raises(
        ResumeCompatibilityError,
        match="runtime_policy_sha256 changed",
    ):
        _agent(
            root=root,
            replies=[_repair()],
            runner=SequencePlanRunner([]),
            runtime_config=changed_runtime,
        ).resume(parent.run_dir)


def test_resume_fingerprint_rejects_changed_poly_mesh_zone_member(
    tmp_path: Path,
) -> None:
    public_root = tmp_path / "public"
    source = public_root / "mesh/native"
    shutil.copytree(POLY_MESH_FIXTURE, source)
    members = tuple(
        BundleMember(
            relative_path=path.relative_to(source).as_posix(),
            logical_name=path.relative_to(source).as_posix(),
            sha256=sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )
        for path in sorted(source.rglob("*"))
        if path.is_file()
    )
    values = {
        "adapter_id": "foampilot.asset.openfoam-poly-mesh",
        "kind": "openfoam_poly_mesh",
        "source_path": "mesh/native",
        "install_path": "constant/polyMesh",
        "region": None,
        "members": members,
    }
    manifest = compute_bundle_manifest_sha256(**values)
    payload = _task().model_dump(mode="json")
    payload["public_assets"] = [
        {
            "path": "mesh/native",
            "sha256": manifest,
            "purpose": "provided native mesh",
            "kind": "directory",
            "install_path": "constant/polyMesh",
            "bundle_manifest_sha256": manifest,
        }
    ]
    task = _task().model_validate(payload)
    environment = _environment("checkMesh", "icoFoam")
    common = {
        "task": task,
        "environment": environment,
        "runtime_config": _runtime_config(),
        "model": "test-model",
        "backend_id": "test-backend",
        "backend_policy_sha256": "a" * 64,
        "knowledge_ids": (),
        "knowledge_text": "knowledge",
        "skill_ids": (),
        "skills_text": "skills",
        "public_asset_root": public_root,
    }

    fingerprint = build_resume_fingerprint(**common)
    assert fingerprint.public_assets_sha256 is not None
    (source / "cellZones").write_text("changed\n", encoding="utf-8")

    with pytest.raises(
        ResumeCompatibilityError,
        match="public_assets_sha256 changed",
    ):
        build_resume_fingerprint(**common)


def test_rerun_changed_runtime_policy_is_explicitly_classified(
    tmp_path: Path,
) -> None:
    parent = _agent(
        root=tmp_path / "job-parent",
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).solve(_task())
    changed_runtime = _runtime_config().model_copy(
        update={"isolation": "trusted_host"}
    )

    child = _agent(
        root=tmp_path / "job-rerun",
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
        runtime_config=changed_runtime,
    ).rerun(parent.run_dir)

    lineage = json.loads(
        (child.run_dir / "lineage.json").read_text(encoding="utf-8")
    )
    assert lineage["relation"] == "rerun_with_changes"
    assert "runtime_policy" in lineage["change_categories"]


def test_resume_generation_reissues_generation_and_enters_solver(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    parent = _agent(
        root=root,
        replies=[_transport_failure()],
        runner=SequencePlanRunner([]),
    ).solve(_task())

    child = _agent(
        root=root,
        replies=[_plan()],
        runner=SequencePlanRunner([(0, "Time = 1\nEnd\n", "")]),
    ).resume(parent.run_dir)

    assert parent.summary.native_status is None
    assert child.summary.native_status == "PUBLIC_VALIDATION_PASS"
    assert child.summary.parent_run is not None
    assert (
        json.loads(
            (child.run_dir / "continuation.json").read_text(
                encoding="utf-8"
            )
        )["from_stage"]
        == "MODEL_GENERATION_STARTED"
    )


def test_resume_rejects_parent_manifest_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    (parent.run_dir / "summary.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ResumeCompatibilityError,
        match="parent_manifest changed",
    ):
        _agent(
            root=root,
            replies=[_repair()],
            runner=SequencePlanRunner([]),
        ).resume(parent.run_dir)


def test_resume_rejects_changed_model(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    agent = _agent(
        root=root,
        replies=[_repair()],
        runner=SequencePlanRunner([]),
    )
    agent.gateway.primary_model = "different-model"

    with pytest.raises(
        ResumeCompatibilityError,
        match="model changed",
    ):
        agent.resume(parent.run_dir)


def test_resume_rejects_missing_runtime_executable(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    agent = _agent(
        root=root,
        replies=[_repair()],
        runner=SequencePlanRunner([]),
    )
    agent.environment_snapshot = _environment("blockMesh")

    with pytest.raises(
        ResumeCompatibilityError,
        match="executable_names changed",
    ):
        agent.resume(parent.run_dir)


def test_resume_rejects_changed_runtime_executable_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    agent = _agent(
        root=root,
        replies=[_repair()],
        runner=SequencePlanRunner([]),
    )
    environment = _environment("blockMesh", "checkMesh", "icoFoam")
    agent.environment_snapshot = environment.model_copy(
        update={
            "commands": [
                CommandFact(
                    name=item.name,
                    path=(
                        Path("/opt/alternate/bin/icoFoam")
                        if item.name == "icoFoam"
                        else item.path
                    ),
                )
                for item in environment.commands
            ]
        }
    )

    with pytest.raises(
        ResumeCompatibilityError,
        match="executable_identity changed",
    ):
        agent.resume(parent.run_dir)


def test_resume_rejects_same_path_executable_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    executable = tmp_path / "icoFoam"
    executable.write_text("first build\n", encoding="utf-8")
    environment = _environment("blockMesh", "checkMesh", "icoFoam")
    environment = environment.model_copy(
        update={
            "commands": [
                CommandFact(
                    name=item.name,
                    path=executable if item.name == "icoFoam" else item.path,
                )
                for item in environment.commands
            ]
        }
    )
    parent_agent = _agent(
        root=root,
        replies=[_plan(), _transport_failure()],
        runner=SequencePlanRunner(
            [(1, "", "Courant number 10\nfloating point exception")]
        ),
    )
    parent_agent.environment_snapshot = environment
    parent = parent_agent.solve(_task())
    executable.write_text("second rebuilt executable\n", encoding="utf-8")
    child_agent = _agent(
        root=root,
        replies=[_repair()],
        runner=SequencePlanRunner([]),
    )
    child_agent.environment_snapshot = environment

    with pytest.raises(
        ResumeCompatibilityError,
        match="executable_identity changed",
    ):
        child_agent.resume(parent.run_dir)


def test_third_repair_continuation_is_not_allowed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    child_one = _agent(
        root=root,
        replies=[_transport_failure()],
        runner=SequencePlanRunner([]),
    ).resume(parent.run_dir)
    child_two = _agent(
        root=root,
        replies=[_transport_failure()],
        runner=SequencePlanRunner([]),
    ).resume(child_one.run_dir)

    assert child_one.summary.resume.allowed
    assert not child_two.summary.resume.allowed
    with pytest.raises(
        ResumeCompatibilityError,
        match="resume_eligibility changed",
    ):
        _agent(
            root=root,
            replies=[_repair()],
            runner=SequencePlanRunner([]),
        ).resume(child_two.run_dir)


def test_lineage_transport_limit_rejects_resume(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    model_path = parent.run_dir / "model-configuration.json"
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    payload["transport_attempts"] = 7
    model_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = parent.run_dir / "artifact-manifest.json"
    manifest.unlink()
    ArtifactStore(root).finalize(parent.run_dir)

    with pytest.raises(
        ResumeCompatibilityError,
        match="lineage_transport_attempt_limit changed",
    ):
        _agent(
            root=root,
            replies=[_repair()],
            runner=SequencePlanRunner([]),
        ).resume(parent.run_dir)


def test_resume_rejects_broken_parent_hash_link(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    parent = _deferred_parent(root)
    child = _agent(
        root=root,
        replies=[_transport_failure()],
        runner=SequencePlanRunner([]),
    ).resume(parent.run_dir)
    summary_path = child.run_dir / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["parent_run"]["manifest_sha256"] = "0" * 64
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (child.run_dir / "artifact-manifest.json").unlink()
    ArtifactStore(root).finalize(child.run_dir)

    with pytest.raises(
        ResumeCompatibilityError,
        match="parent_run changed",
    ):
        _agent(
            root=root,
            replies=[_repair()],
            runner=SequencePlanRunner([]),
        ).resume(child.run_dir)
