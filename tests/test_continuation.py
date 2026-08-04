from __future__ import annotations

import json
from pathlib import Path

import pytest

from foampilot.agent import NativeAgent
from foampilot.agent.repair import RepairDecision
from foampilot.artifacts import ArtifactStore
from foampilot.plans import GeneratedFile
from foampilot.workflow import ResumeCompatibilityError

from tests.test_native_agent_state_machine import (
    SequencePlanRunner,
    _control_dict,
    _environment,
    _runtime_config,
    _task,
    _transport_failure,
)
from tests.test_native_case_generation import RecordingModel, _plan


def _agent(
    *,
    root: Path,
    replies: list,
    runner: SequencePlanRunner,
    knowledge_text: str | None = None,
) -> NativeAgent:
    return NativeAgent(
        gateway=RecordingModel(replies),
        runtime_config=_runtime_config(),
        artifact_store=ArtifactStore(root),
        environment_snapshot=_environment("blockMesh", "icoFoam"),
        runner=runner,
        knowledge_text=knowledge_text,
    )


def _repair() -> RepairDecision:
    return RepairDecision(
        because="The solver log identifies a missing stable time step.",
        evidence=["FOAM FATAL ERROR: missing keyword"],
        cause="The initial time step is too large.",
        changed_files=[
            GeneratedFile(
                path="system/controlDict",
                content=_control_dict(delta_t=0.001),
            )
        ],
        changed_commands=[],
        expected_check="The solver reaches End.",
        stable_control="Mesh and boundary conditions are unchanged.",
    )


def _deferred_parent(root: Path):
    return _agent(
        root=root,
        replies=[_plan(), _transport_failure()],
        runner=SequencePlanRunner(
            [(1, "", "FOAM FATAL ERROR: missing keyword")]
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
