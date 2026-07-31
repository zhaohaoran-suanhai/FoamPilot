"""Strict parent-to-child continuation contracts."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Literal

from pydantic import Field
import yaml

from foampilot import __version__
from foampilot.artifacts import ArtifactStore, RunSummary
from foampilot.environment import EnvironmentSnapshot
from foampilot.plans import ExecutionPlan
from foampilot.tasks import TaskSpec

from .models import (
    ResumeCompatibility,
    ResumeCompatibilityError,
    StrictModel,
    WorkflowStage,
)


_PROVIDER_POLICY = {
    "schema_version": 1,
    "request_timeout_seconds": 300,
    "routing_deadline_seconds": 60,
    "generation_deadline_seconds": 360,
    "repair_deadline_seconds": 240,
    "total_model_deadline_seconds": 600,
    "max_transport_attempts_per_request": 3,
    "lineage_transport_attempt_limit": 7,
    "max_continuations_per_stage": 2,
    "retry_delays_seconds": [5, 15],
    "stream_retry_delays_seconds": [5],
}
_STRICT_FIELDS = (
    "task_sha256",
    "public_assets_sha256",
    "model",
    "provider",
    "provider_policy_sha256",
    "package_version",
    "package_artifact_sha256",
    "git_revision",
    "execution_plan_schema",
    "knowledge_ids",
    "knowledge_hash",
    "skill_ids",
    "skill_hash",
    "openfoam_target",
)
_RESUMABLE_STAGES = {
    WorkflowStage.MODEL_GENERATION_STARTED,
    WorkflowStage.MODEL_REPAIR_STARTED,
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _package_artifact_sha256() -> str:
    root = _package_root()
    digest = sha256()
    suffixes = {".py", ".yaml", ".yml", ".json", ".md"}
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and item.suffix.lower() in suffixes
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _git_revision() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_package_root(),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision or None


def _public_assets_sha256(
    task: TaskSpec,
    public_asset_root: str | Path | None,
) -> str | None:
    if not task.public_assets:
        return None
    root = (
        Path(public_asset_root).resolve()
        if public_asset_root is not None
        else None
    )
    entries: list[dict[str, str]] = []
    for asset in sorted(task.public_assets, key=lambda item: item.path):
        if root is not None:
            path = (root / asset.path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ResumeCompatibilityError("public_assets_sha256")
            if _file_sha256(path) != asset.sha256:
                raise ResumeCompatibilityError("public_assets_sha256")
        entries.append(
            {
                "path": asset.path,
                "sha256": asset.sha256,
                "purpose": asset.purpose,
            }
        )
    return _hash_json(entries)


def build_resume_fingerprint(
    *,
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    model: str,
    provider: str,
    knowledge_ids: list[str] | tuple[str, ...],
    knowledge_text: str,
    skill_ids: list[str] | tuple[str, ...],
    skills_text: str,
    public_asset_root: str | Path | None = None,
) -> ResumeCompatibility:
    """Describe every input whose change would make resume a new experiment."""

    return ResumeCompatibility(
        task_sha256=_hash_json(task.model_dump(mode="json")),
        public_assets_sha256=_public_assets_sha256(
            task,
            public_asset_root,
        ),
        model=model,
        provider=provider,
        provider_policy_sha256=_hash_json(_PROVIDER_POLICY),
        package_version=__version__,
        package_artifact_sha256=_package_artifact_sha256(),
        git_revision=_git_revision(),
        execution_plan_schema=3,
        knowledge_ids=list(knowledge_ids),
        knowledge_hash=_hash_text(knowledge_text),
        skill_ids=list(skill_ids),
        skill_hash=_hash_text(skills_text),
        openfoam_target=task.openfoam_target.model_dump(mode="json"),
        executable_names=sorted(environment.executable_names),
    )


class ContinuationInput(StrictModel):
    parent_run: Path
    parent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_stage: Literal[
        WorkflowStage.MODEL_GENERATION_STARTED,
        WorkflowStage.MODEL_REPAIR_STARTED,
    ]
    parent_summary: RunSummary
    active_plan_path: Path | None = None
    public_validation_path: Path | None = None
    failed_log_paths: list[Path] = Field(default_factory=list)
    transport_attempts_used: int = Field(ge=0, le=7)
    continuation_index_for_stage: int = Field(ge=1, le=2)
    environment_warnings: list[str] = Field(default_factory=list)


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ResumeCompatibilityError(path.name)
    return payload


def _checkpoint_payload(path: Path) -> dict[str, object]:
    envelope = _read_json(path)
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ResumeCompatibilityError(path.name)
    return payload


def _model_transport_attempts(run_dir: Path) -> int:
    path = run_dir / "model-configuration.json"
    if not path.is_file():
        return 0
    return int(_read_json(path).get("transport_attempts", 0))


def _parent_path(
    summary: RunSummary,
    *,
    store: ArtifactStore,
) -> Path | None:
    if summary.parent_run is None:
        return None
    candidate = (store.root / summary.parent_run.run_id).resolve()
    if (
        not candidate.is_relative_to(store.root)
        or store.verify(candidate)
        or store.manifest_sha256(candidate)
        != summary.parent_run.manifest_sha256
    ):
        raise ResumeCompatibilityError("parent_run")
    return candidate


def _lineage_usage(
    *,
    parent_run: Path,
    from_stage: WorkflowStage,
    store: ArtifactStore,
) -> tuple[int, int]:
    stage_continuations = 0
    transport_attempts = 0
    seen: set[Path] = set()
    current: Path | None = parent_run
    while current is not None:
        if current in seen:
            raise ResumeCompatibilityError("parent_run_cycle")
        seen.add(current)
        if store.verify(current):
            raise ResumeCompatibilityError("parent_manifest")
        summary = store.read_summary(current)
        transport_attempts += _model_transport_attempts(current)
        continuation_path = current / "continuation.json"
        if continuation_path.is_file():
            continuation = _read_json(continuation_path)
            if continuation.get("from_stage") == from_stage.value:
                stage_continuations += 1
        current = _parent_path(
            summary,
            store=store,
        )
    next_index = stage_continuations + 1
    if next_index > 2:
        raise ResumeCompatibilityError("continuation_budget")
    if transport_attempts >= 7:
        raise ResumeCompatibilityError("lineage_transport_attempt_limit")
    return transport_attempts, next_index


def prepare_continuation(
    *,
    parent_run: str | Path,
    artifact_store: ArtifactStore,
    current: ResumeCompatibility,
) -> ContinuationInput:
    """Validate immutable evidence and build a bounded child-run input."""

    parent = Path(parent_run).resolve()
    if not parent.is_relative_to(artifact_store.root):
        raise ResumeCompatibilityError("parent_run")
    if artifact_store.verify(parent):
        raise ResumeCompatibilityError("parent_manifest")
    summary = artifact_store.read_summary(parent)
    if (
        not summary.resume.allowed
        or summary.resume.from_stage not in _RESUMABLE_STAGES
        or summary.terminal_blocker is None
        or not summary.terminal_blocker.retryable
    ):
        raise ResumeCompatibilityError("resume_eligibility")
    from_stage = summary.resume.from_stage
    assert from_stage is not None

    manifest_sha256 = artifact_store.manifest_sha256(parent)
    fingerprint_path = parent / "resume-compatibility.json"
    if not fingerprint_path.is_file():
        raise ResumeCompatibilityError("resume_compatibility")
    previous = ResumeCompatibility.model_validate(
        _read_json(fingerprint_path)
    )
    for field in _STRICT_FIELDS:
        if getattr(previous, field) != getattr(current, field):
            raise ResumeCompatibilityError(field)

    missing_executables = sorted(
        set(previous.executable_names) - set(current.executable_names)
    )
    if missing_executables:
        raise ResumeCompatibilityError("executable_names")
    warnings: list[str] = []
    extra_executables = sorted(
        set(current.executable_names) - set(previous.executable_names)
    )
    if extra_executables:
        warnings.append(
            "runtime exposes additional compatible executables: "
            + ", ".join(extra_executables)
        )

    transport_attempts, continuation_index = _lineage_usage(
        parent_run=parent,
        from_stage=from_stage,
        store=artifact_store,
    )

    active_plan_path: Path | None = None
    validation_path: Path | None = None
    log_paths: list[Path] = []
    if from_stage == WorkflowStage.MODEL_REPAIR_STARTED:
        if not summary.attempts:
            raise ResumeCompatibilityError("repair_evidence")
        attempt = summary.attempts[-1].attempt
        evidence_run = parent
        evidence_summary = summary
        visited: set[Path] = set()
        while not (
            evidence_run / f"attempt-{attempt:02d}"
        ).is_dir():
            if evidence_run in visited:
                raise ResumeCompatibilityError("parent_run_cycle")
            visited.add(evidence_run)
            ancestor = _parent_path(
                evidence_summary,
                store=artifact_store,
            )
            if ancestor is None:
                raise ResumeCompatibilityError("repair_evidence")
            evidence_run = ancestor
            evidence_summary = artifact_store.read_summary(evidence_run)
        attempt_root = evidence_run / f"attempt-{attempt:02d}"
        active_plan_path = attempt_root / "execution-plan.json"
        validation_path = attempt_root / "public-validation.json"
        evidence_path = (
            evidence_run
            / "checkpoints"
            / f"repair-evidence-attempt-{attempt:02d}.json"
        )
        if not active_plan_path.is_file() or not validation_path.is_file():
            raise ResumeCompatibilityError("repair_evidence")
        if evidence_path.is_file():
            evidence = _checkpoint_payload(evidence_path)
            raw_paths = evidence.get("log_paths", [])
            if isinstance(raw_paths, list):
                for raw_path in raw_paths:
                    candidate = (evidence_run / str(raw_path)).resolve()
                    if (
                        candidate.is_relative_to(evidence_run)
                        and candidate.is_file()
                    ):
                        log_paths.append(candidate)

    return ContinuationInput(
        parent_run=parent,
        parent_manifest_sha256=manifest_sha256,
        from_stage=from_stage,
        parent_summary=summary,
        active_plan_path=active_plan_path,
        public_validation_path=validation_path,
        failed_log_paths=log_paths,
        transport_attempts_used=transport_attempts,
        continuation_index_for_stage=continuation_index,
        environment_warnings=warnings,
    )


def load_parent_task(parent_run: str | Path) -> TaskSpec:
    payload = yaml.safe_load(
        (Path(parent_run) / "task.yaml").read_text(encoding="utf-8")
    )
    return TaskSpec.model_validate(payload)


def load_parent_plan(continuation: ContinuationInput) -> ExecutionPlan:
    if continuation.active_plan_path is None:
        raise ResumeCompatibilityError("active_plan")
    return ExecutionPlan.model_validate_json(
        continuation.active_plan_path.read_text(encoding="utf-8")
    )
