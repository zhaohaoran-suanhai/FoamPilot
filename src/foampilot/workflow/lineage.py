"""Strict parent-to-child continuation contracts."""

from __future__ import annotations

from datetime import datetime, timezone
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
from foampilot.models import NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT
from foampilot.plans import ExecutionPlan
from foampilot.runtime.models import RuntimeConfig
from foampilot.tasks import TaskSpec

from .models import (
    ResumeCompatibility,
    ResumeCompatibilityError,
    StrictModel,
    WorkflowStage,
)


_STRICT_FIELDS = (
    "task_sha256",
    "public_assets_sha256",
    "model",
    "backend_id",
    "backend_policy_sha256",
    "runtime_policy_sha256",
    "package_version",
    "package_artifact_sha256",
    "git_revision",
    "execution_plan_schema",
    "knowledge_ids",
    "knowledge_hash",
    "skill_ids",
    "skill_hash",
    "acceptance_plan_sha256",
    "observation_plan_sha256",
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
    if root is not None and any(
        asset.kind == "directory" for asset in task.public_assets
    ):
        from foampilot.tasks.io import inspect_public_assets

        try:
            inspect_public_assets(task, root)
        except (OSError, TypeError, ValueError) as error:
            raise ResumeCompatibilityError("public_assets_sha256") from error
    entries: list[dict[str, object]] = []
    for asset in sorted(task.public_assets, key=lambda item: item.path):
        if root is not None:
            path = (root / asset.path).resolve()
            if not path.is_relative_to(root):
                raise ResumeCompatibilityError("public_assets_sha256")
            if asset.kind == "file":
                if not path.is_file() or _file_sha256(path) != asset.sha256:
                    raise ResumeCompatibilityError("public_assets_sha256")
            elif not path.is_dir():
                raise ResumeCompatibilityError("public_assets_sha256")
        entries.append(
            {
                "path": asset.path,
                "sha256": asset.sha256,
                "purpose": asset.purpose,
                "kind": asset.kind,
                "install_path": asset.install_path,
                "bundle_manifest_sha256": asset.bundle_manifest_sha256,
                "inspector": (
                    {
                        "id": "foampilot.mesh.poly-mesh",
                        "version": "1.0.0",
                    }
                    if asset.kind == "directory"
                    else None
                ),
            }
        )
    return _hash_json(entries)


def _runtime_policy_sha256(
    config: RuntimeConfig,
    environment: EnvironmentSnapshot,
) -> str:
    """Hash the local execution contract, including sandbox identity."""

    bubblewrap_sha256: str | None = None
    if config.bubblewrap is not None and config.bubblewrap.is_file():
        bubblewrap_sha256 = _file_sha256(config.bubblewrap)
    return _hash_json(
        {
            "config": config.model_dump(mode="json"),
            "bubblewrap_sha256": bubblewrap_sha256,
            "environment": {
                "distribution": environment.distribution,
                "version": environment.version,
                "openfoam_root": str(environment.openfoam_root),
                "mpi_launcher": (
                    str(environment.mpi_launcher)
                    if environment.mpi_launcher is not None
                    else None
                ),
                "mpi_launcher_identity": (
                    _executable_identity(environment.mpi_launcher)
                    if environment.mpi_launcher is not None
                    else None
                ),
                "max_mpi_ranks": environment.max_mpi_ranks,
            },
        }
    )


def _executable_identity(path: Path) -> str:
    """Return a cheap rebuild-sensitive identity for a local executable."""

    try:
        stat = path.stat()
        facts: dict[str, object] = {
            "path": str(path),
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "mode": stat.st_mode,
        }
    except OSError:
        facts = {"path": str(path), "unavailable": True}
    return _hash_json(facts)


def _environment_executable_paths(
    environment: EnvironmentSnapshot,
) -> dict[str, Path]:
    paths = {item.name: item.path for item in environment.commands}
    if environment.gmsh is not None:
        paths["gmsh"] = environment.gmsh
    return paths


def build_resume_fingerprint(
    *,
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    runtime_config: RuntimeConfig,
    model: str,
    backend_id: str,
    backend_policy_sha256: str,
    knowledge_ids: list[str] | tuple[str, ...],
    knowledge_text: str,
    skill_ids: list[str] | tuple[str, ...],
    skills_text: str,
    public_asset_root: str | Path | None = None,
    acceptance_plan_sha256: str | None = None,
    observation_plan_sha256: str | None = None,
) -> ResumeCompatibility:
    """Describe every input whose change would make resume a new experiment."""

    executable_paths = _environment_executable_paths(environment)
    return ResumeCompatibility(
        task_sha256=_hash_json(task.model_dump(mode="json")),
        public_assets_sha256=_public_assets_sha256(
            task,
            public_asset_root,
        ),
        model=model,
        backend_id=backend_id,
        backend_policy_sha256=backend_policy_sha256,
        runtime_policy_sha256=_runtime_policy_sha256(
            runtime_config,
            environment,
        ),
        package_version=__version__,
        package_artifact_sha256=_package_artifact_sha256(),
        git_revision=_git_revision(),
        execution_plan_schema=4,
        knowledge_ids=list(knowledge_ids),
        knowledge_hash=_hash_text(knowledge_text),
        skill_ids=list(skill_ids),
        skill_hash=_hash_text(skills_text),
        acceptance_plan_sha256=acceptance_plan_sha256 or "0" * 64,
        observation_plan_sha256=observation_plan_sha256 or "0" * 64,
        openfoam_target=task.openfoam_target.model_dump(mode="json"),
        executable_names=sorted(environment.available_executable_names),
        executable_paths={
            name: str(executable_paths[name])
            for name in sorted(executable_paths)
        },
        executable_identities={
            name: _executable_identity(executable_paths[name])
            for name in sorted(executable_paths)
        },
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
    run_assessment_path: Path | None = None
    run_facts_path: Path | None = None
    transport_attempts_used: int = Field(
        ge=0,
        le=NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT,
    )
    continuation_index_for_stage: int = Field(ge=1, le=2)
    continuation_counts: dict[str, int] = Field(default_factory=dict)
    logical_requests_used_before_child: int = Field(ge=0)
    execution_seconds_used_before_child: float = Field(ge=0)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_warnings: list[str] = Field(default_factory=list)


class LineageRecord(StrictModel):
    schema_version: Literal[1] = 1
    relation: Literal[
        "strict_resume",
        "design_confirmation",
        "rerun_same_input",
        "rerun_with_changes",
    ]
    parent_run_id: str
    parent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    input_hash_before: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    change_categories: list[str] = Field(default_factory=list)
    reused_evidence_paths: list[str] = Field(default_factory=list)
    confirmation_record_hashes: list[str] = Field(default_factory=list)


class RerunInput(StrictModel):
    parent_run: Path
    parent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_fingerprint: ResumeCompatibility | None = None
    declared_change_categories: list[str] = Field(default_factory=list)


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


def _continuation_payload(run_dir: Path) -> dict[str, object]:
    path = run_dir / "continuation.json"
    return _read_json(path) if path.is_file() else {}


def _parent_link_is_consistent(parent: Path, summary: RunSummary) -> None:
    if summary.parent_run is None:
        return
    continuation = _continuation_payload(parent)
    raw_parent = continuation.get("parent_run")
    if not isinstance(raw_parent, dict):
        raise ResumeCompatibilityError("parent_run")
    if (
        raw_parent.get("run_id") != summary.parent_run.run_id
        or raw_parent.get("manifest_sha256")
        != summary.parent_run.manifest_sha256
    ):
        raise ResumeCompatibilityError("parent_run")


def _lineage_usage(
    *,
    parent_run: Path,
    from_stage: WorkflowStage,
    store: ArtifactStore,
) -> tuple[int, int, dict[str, int]]:
    del store
    continuation = _continuation_payload(parent_run)
    raw_counts = continuation.get("continuation_counts", {})
    counts = (
        {str(key): int(value) for key, value in raw_counts.items()}
        if isinstance(raw_counts, dict)
        else {}
    )
    if not counts and continuation.get("from_stage") is not None:
        counts[str(continuation["from_stage"])] = int(
            continuation.get("continuation_index_for_stage", 1)
        )
    next_index = counts.get(from_stage.value, 0) + 1
    if next_index > 2:
        raise ResumeCompatibilityError("continuation_budget")
    counts[from_stage.value] = next_index
    transport_attempts = int(
        continuation.get("transport_attempts_used_before_child", 0)
    ) + _model_transport_attempts(parent_run)
    if transport_attempts >= NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT:
        raise ResumeCompatibilityError("lineage_transport_attempt_limit")
    return transport_attempts, next_index, counts


def fingerprint_sha256(value: ResumeCompatibility) -> str:
    return _hash_json(value.model_dump(mode="json"))


def task_sha256(task: TaskSpec) -> str:
    return _hash_json(task.model_dump(mode="json"))


def _run_result_seconds(path: Path) -> float:
    try:
        payload = _read_json(path)
    except (OSError, ValueError):
        return 0.0
    steps = payload.get("steps", [])
    if not isinstance(steps, list):
        return 0.0
    total = 0.0
    for step in steps:
        if not isinstance(step, dict):
            continue
        elapsed = step.get("elapsed_seconds")
        if isinstance(elapsed, (int, float)) and elapsed >= 0:
            total += float(elapsed)
            continue
        try:
            started = datetime.fromisoformat(str(step["started_at"]))
            finished = datetime.fromisoformat(str(step["finished_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        total += max((finished - started).total_seconds(), 0.0)
    return total


def prepare_continuation(
    *,
    parent_run: str | Path,
    artifact_store: ArtifactStore,
    current: ResumeCompatibility,
) -> ContinuationInput:
    """Validate immutable evidence and build a bounded child-run input."""

    parent = Path(parent_run).resolve()
    parent_store = ArtifactStore(parent.parent)
    if parent_store.verify(parent):
        raise ResumeCompatibilityError("parent_manifest")
    summary = parent_store.read_summary(parent)
    _parent_link_is_consistent(parent, summary)
    if (
        not summary.resume.allowed
        or summary.resume.from_stage not in _RESUMABLE_STAGES
        or summary.terminal_blocker is None
        or not summary.terminal_blocker.retryable
    ):
        raise ResumeCompatibilityError("resume_eligibility")
    from_stage = summary.resume.from_stage
    assert from_stage is not None

    manifest_sha256 = parent_store.manifest_sha256(parent)
    fingerprint_path = parent / "resume-compatibility.json"
    if not fingerprint_path.is_file():
        raise ResumeCompatibilityError("resume_compatibility")
    try:
        previous = ResumeCompatibility.model_validate(
            _read_json(fingerprint_path)
        )
    except ValueError as error:
        raise ResumeCompatibilityError("resume_compatibility") from error
    for name, field in (
        ("acceptance-plan.json", "acceptance_plan_sha256"),
        ("observation-plan.json", "observation_plan_sha256"),
    ):
        contract_path = parent / name
        if not contract_path.is_file():
            raise ResumeCompatibilityError(field)
        actual = _file_sha256(contract_path)
        if actual != getattr(previous, field):
            raise ResumeCompatibilityError(field)
    for field in _STRICT_FIELDS:
        if getattr(previous, field) != getattr(current, field):
            raise ResumeCompatibilityError(field)

    missing_executables = sorted(
        set(previous.executable_names) - set(current.executable_names)
    )
    if missing_executables:
        raise ResumeCompatibilityError("executable_names")
    for name in previous.executable_names:
        if previous.executable_paths.get(
            name
        ) != current.executable_paths.get(name):
            raise ResumeCompatibilityError("executable_identity")
        if previous.executable_identities.get(
            name
        ) != current.executable_identities.get(name):
            raise ResumeCompatibilityError("executable_identity")
    warnings: list[str] = []
    extra_executables = sorted(
        set(current.executable_names) - set(previous.executable_names)
    )
    if extra_executables:
        warnings.append(
            "runtime exposes additional compatible executables: "
            + ", ".join(extra_executables)
        )

    transport_attempts, continuation_index, continuation_counts = _lineage_usage(
        parent_run=parent,
        from_stage=from_stage,
        store=parent_store,
    )
    prior_continuation = _continuation_payload(parent)
    model_configuration = parent / "model-configuration.json"
    logical_requests = int(
        prior_continuation.get("logical_requests_used_before_child", 0)
    )
    if model_configuration.is_file():
        logical_requests += int(
            _read_json(model_configuration).get("logical_model_requests", 0)
        )
    execution_seconds = float(
        prior_continuation.get("execution_seconds_used_before_child", 0.0)
    ) + sum(
        _run_result_seconds(path)
        for path in sorted(parent.glob("attempt-*/run-result.json"))
    )

    active_plan_path: Path | None = None
    validation_path: Path | None = None
    run_facts_path: Path | None = None
    if from_stage == WorkflowStage.MODEL_REPAIR_STARTED:
        if not summary.attempts:
            raise ResumeCompatibilityError("repair_evidence")
        attempt = summary.attempts[-1].attempt
        copied_evidence = parent / "continuation-evidence"
        if copied_evidence.is_dir():
            active_plan_path = copied_evidence / "execution-plan.json"
            validation_path = copied_evidence / "run-assessment.json"
            run_facts_path = copied_evidence / "run-facts.json"
        else:
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
                    store=parent_store,
                )
                if ancestor is None:
                    raise ResumeCompatibilityError("repair_evidence")
                evidence_run = ancestor
                evidence_summary = parent_store.read_summary(evidence_run)
            attempt_root = evidence_run / f"attempt-{attempt:02d}"
            active_plan_path = attempt_root / "execution-plan.json"
            validation_path = attempt_root / "run-assessment.json"
            run_facts_path = attempt_root / "run-facts.json"
        if (
            not active_plan_path.is_file()
            or not validation_path.is_file()
            or run_facts_path is None
            or not run_facts_path.is_file()
        ):
            raise ResumeCompatibilityError("repair_evidence")
    return ContinuationInput(
        parent_run=parent,
        parent_manifest_sha256=manifest_sha256,
        from_stage=from_stage,
        parent_summary=summary,
        active_plan_path=active_plan_path,
        run_assessment_path=validation_path,
        run_facts_path=run_facts_path,
        transport_attempts_used=transport_attempts,
        continuation_index_for_stage=continuation_index,
        continuation_counts=continuation_counts,
        logical_requests_used_before_child=logical_requests,
        execution_seconds_used_before_child=execution_seconds,
        input_sha256=fingerprint_sha256(previous),
        environment_warnings=warnings,
    )


def prepare_rerun(
    parent_run: str | Path,
    *,
    declared_change_categories: list[str] | tuple[str, ...] = (),
) -> RerunInput:
    """Verify one immutable parent and capture its normative input evidence."""

    parent = Path(parent_run).resolve()
    store = ArtifactStore(parent.parent)
    if store.verify(parent):
        raise ResumeCompatibilityError("parent_manifest")
    store.read_summary(parent)
    parent_task = load_parent_task(parent)
    fingerprint_path = parent / "resume-compatibility.json"
    fingerprint: ResumeCompatibility | None = None
    if fingerprint_path.is_file():
        try:
            fingerprint = ResumeCompatibility.model_validate(
                _read_json(fingerprint_path)
            )
        except ValueError:
            # A legacy/unknown fingerprint cannot be reused, but its run can
            # still be the immutable parent of a cold rerun.
            fingerprint = None
    return RerunInput(
        parent_run=parent,
        parent_manifest_sha256=store.manifest_sha256(parent),
        parent_task_sha256=task_sha256(parent_task),
        parent_fingerprint=fingerprint,
        declared_change_categories=sorted(set(declared_change_categories)),
    )


def build_lineage_record(
    *,
    rerun: RerunInput,
    task: TaskSpec,
    current_fingerprint: ResumeCompatibility | None,
) -> LineageRecord:
    """Classify rerun relationship from manifested compatibility evidence."""

    categories = set(rerun.declared_change_categories)
    current_task_hash = task_sha256(task)
    if current_task_hash != rerun.parent_task_sha256:
        categories.add("task")
    before = (
        fingerprint_sha256(rerun.parent_fingerprint)
        if rerun.parent_fingerprint is not None
        else rerun.parent_task_sha256
    )
    if current_fingerprint is None:
        categories.add("compatibility_not_verified")
        after = current_task_hash
    else:
        after = fingerprint_sha256(current_fingerprint)
        if rerun.parent_fingerprint is None:
            categories.add("parent_fingerprint_unavailable")
        else:
            mapping = {
                "task_sha256": "task",
                "public_assets_sha256": "public_assets",
                "model": "model",
                "backend_id": "backend",
                "backend_policy_sha256": "backend_policy",
                "runtime_policy_sha256": "runtime_policy",
                "package_version": "package",
                "package_artifact_sha256": "package",
                "git_revision": "package",
                "knowledge_ids": "knowledge",
                "knowledge_hash": "knowledge",
                "skill_ids": "skills",
                "skill_hash": "skills",
                "acceptance_plan_sha256": "acceptance",
                "observation_plan_sha256": "observations",
                "openfoam_target": "openfoam_target",
                "executable_names": "runtime_executables",
                "executable_paths": "runtime_executables",
                "executable_identities": "runtime_executables",
            }
            for field, category in mapping.items():
                if getattr(rerun.parent_fingerprint, field) != getattr(
                    current_fingerprint,
                    field,
                ):
                    categories.add(category)
    return LineageRecord(
        relation=(
            "rerun_with_changes" if categories else "rerun_same_input"
        ),
        parent_run_id=rerun.parent_run.name,
        parent_manifest_sha256=rerun.parent_manifest_sha256,
        created_at=datetime.now(timezone.utc),
        input_hash_before=before,
        input_hash_after=after,
        change_categories=sorted(categories),
        reused_evidence_paths=[],
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
