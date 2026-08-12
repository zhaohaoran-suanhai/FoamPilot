"""Strict qualification for explicitly selected ExecutionPlan sources."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

import yaml

from foampilot.artifacts import ArtifactStore
from foampilot.environment import EnvironmentSnapshot
from foampilot.plans import ExecutionPlan
from foampilot.routing import CapabilityProfile
from foampilot.runtime import PlanRunResult, parse_openfoam_log
from foampilot.tasks import TaskSpec


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class PlanReuseError(ValueError):
    """Stable explicit-plan rejection that must not trigger generation."""

    code = "PLAN_REUSE_REJECTED"

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class VerifiedPlanSource:
    source_run: Path
    source_manifest_sha256: str
    source_attempt: int
    task_sha256: str
    plan_sha256: str
    plan: ExecutionPlan
    capability: CapabilityProfile
    public_validation_pass: bool

    def record(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "hit",
            "source_run_id": self.source_run.name,
            "source_manifest_sha256": self.source_manifest_sha256,
            "source_attempt": self.source_attempt,
            "task_sha256": self.task_sha256,
            "plan_sha256": self.plan_sha256,
            "source_public_validation_pass": self.public_validation_pass,
        }


def _covered_path(
    path: Path,
    *,
    source_run: Path,
    manifest_files: set[str],
) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(source_run).as_posix()
    except ValueError as error:
        raise PlanReuseError(
            "SOURCE_EVIDENCE_ESCAPES_RUN",
            f"evidence path is outside source run: {path}",
        ) from error
    if relative not in manifest_files:
        raise PlanReuseError(
            "SOURCE_EVIDENCE_NOT_MANIFESTED",
            f"source manifest does not cover {relative}",
        )
    return relative


def _step_log(
    step,
    *,
    source_run: Path,
    manifest_files: set[str],
) -> str:
    parts: list[str] = []
    for path in (step.stdout_path, step.stderr_path):
        _covered_path(
            path,
            source_run=source_run,
            manifest_files=manifest_files,
        )
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _validate_assets(task: TaskSpec, asset_root: Path) -> None:
    root = asset_root.resolve()
    for asset in task.public_assets:
        path = (root / asset.path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise PlanReuseError(
                "PUBLIC_ASSET_MISSING",
                f"public asset is missing: {asset.path}",
            )
        if _file_sha256(path) != asset.sha256:
            raise PlanReuseError(
                "PUBLIC_ASSET_SHA256_MISMATCH",
                f"public asset bytes changed: {asset.path}",
            )


def load_verified_plan_source(
    source_run: str | Path,
    *,
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    public_asset_root: str | Path | None,
) -> VerifiedPlanSource:
    """Load one exact source or raise a stable, non-fallback rejection."""

    source = Path(source_run).resolve()
    if not source.is_dir():
        raise PlanReuseError(
            "SOURCE_RUN_MISSING",
            f"source run is missing: {source}",
        )
    store = ArtifactStore(source.parent)
    try:
        manifest_issues = store.verify(source)
    except (OSError, ValueError) as error:
        raise PlanReuseError(
            "SOURCE_MANIFEST_INVALID",
            "source artifact manifest cannot be parsed",
        ) from error
    if manifest_issues:
        raise PlanReuseError(
            "SOURCE_MANIFEST_INVALID",
            "; ".join(manifest_issues[:5]),
        )
    manifest_path = source / store.manifest_name
    try:
        manifest_payload = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise PlanReuseError(
            "SOURCE_MANIFEST_INVALID",
            "source artifact manifest cannot be parsed",
        ) from error
    manifest_files = set(manifest_payload.get("files", {}))

    task_path = source / "task.yaml"
    capability_path = source / "capability-profile.json"
    environment_path = source / "environment.json"
    for required in (task_path, capability_path, environment_path):
        _covered_path(
            required,
            source_run=source,
            manifest_files=manifest_files,
        )
    try:
        source_task = TaskSpec.model_validate(
            yaml.safe_load(task_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise PlanReuseError(
            "SOURCE_TASK_INVALID",
            "source TaskSpec cannot be parsed",
        ) from error
    expected_task_sha = _canonical_sha256(task.model_dump(mode="json"))
    source_task_sha = _canonical_sha256(
        source_task.model_dump(mode="json")
    )
    if source_task_sha != expected_task_sha:
        raise PlanReuseError(
            "TASK_SHA256_MISMATCH",
            "current TaskSpec does not exactly match the source run",
        )
    if (
        task.openfoam_target.distribution != environment.distribution
        or task.openfoam_target.version != environment.version
    ):
        raise PlanReuseError(
            "OPENFOAM_TARGET_MISMATCH",
            "current OpenFOAM runtime does not match TaskSpec",
        )
    try:
        source_environment = EnvironmentSnapshot.model_validate_json(
            environment_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise PlanReuseError(
            "SOURCE_ENVIRONMENT_INVALID",
            "source environment evidence cannot be parsed",
        ) from error
    if (
        source_environment.distribution != environment.distribution
        or source_environment.version != environment.version
    ):
        raise PlanReuseError(
            "SOURCE_OPENFOAM_MISMATCH",
            "source and current OpenFOAM distributions or versions differ",
        )
    if task.public_assets:
        if public_asset_root is None:
            raise PlanReuseError(
                "PUBLIC_ASSET_ROOT_REQUIRED",
                "current public assets were not staged",
            )
        _validate_assets(task, Path(public_asset_root))
        _validate_assets(task, source / "public-assets")

    try:
        capability = CapabilityProfile.model_validate_json(
            capability_path.read_text(encoding="utf-8")
        )
        summary = store.read_summary(source)
    except (OSError, ValueError) as error:
        raise PlanReuseError(
            "SOURCE_EVIDENCE_INVALID",
            "source capability or summary evidence cannot be parsed",
        ) from error
    attempts = sorted(
        {item.attempt for item in summary.attempts},
        reverse=True,
    )
    for attempt_number in attempts:
        attempt_root = source / f"attempt-{attempt_number:02d}"
        plan_path = attempt_root / "execution-plan.json"
        result_path = attempt_root / "run-result.json"
        if not plan_path.is_file() or not result_path.is_file():
            continue
        _covered_path(
            plan_path,
            source_run=source,
            manifest_files=manifest_files,
        )
        _covered_path(
            result_path,
            source_run=source,
            manifest_files=manifest_files,
        )
        try:
            plan = ExecutionPlan.model_validate_json(
                plan_path.read_text(encoding="utf-8")
            )
            run_result = PlanRunResult.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
        except ValueError:
            continue
        if plan.manifest.solver_executable not in (
            environment.available_executable_names
        ):
            raise PlanReuseError(
                "SOLVER_EXECUTABLE_UNAVAILABLE",
                f"solver is unavailable: {plan.manifest.solver_executable}",
            )
        if any(
            command.mpi_ranks
            > min(task.resource_budget.max_mpi_ranks, environment.max_mpi_ranks)
            for command in plan.commands
        ):
            raise PlanReuseError(
                "MPI_RESOURCE_MISMATCH",
                "source plan exceeds the current MPI resource limit",
            )
        steps = {item.step_id: item for item in run_result.steps}
        mesh_commands = [
            item for item in plan.commands if item.stage == "mesh"
        ]
        check_commands = [
            item
            for item in plan.commands
            if item.stage == "check" and item.executable == "checkMesh"
        ]
        solver_commands = [
            item
            for item in plan.commands
            if item.stage == "solve"
            and item.executable == plan.manifest.solver_executable
        ]
        provided_mesh = (
            task.mesh is not None and task.mesh.strategy == "provided"
        )
        if (
            (not provided_mesh and not mesh_commands)
            or not check_commands
            or not solver_commands
        ):
            continue
        if any(
            command.step_id not in steps
            or steps[command.step_id].return_code != 0
            or steps[command.step_id].timed_out
            for command in [*mesh_commands, *check_commands]
        ):
            continue
        if not any(
            "Mesh OK" in _step_log(
                steps[command.step_id],
                source_run=source,
                manifest_files=manifest_files,
            )
            for command in check_commands
        ):
            continue
        solver_ok = False
        for command in solver_commands:
            step = steps.get(command.step_id)
            if (
                step is None
                or step.return_code != 0
                or step.timed_out
            ):
                continue
            log = parse_openfoam_log(
                _step_log(
                    step,
                    source_run=source,
                    manifest_files=manifest_files,
                )
            )
            if log.completed and log.latest_time is not None:
                solver_ok = True
                break
        if not solver_ok:
            continue
        return VerifiedPlanSource(
            source_run=source,
            source_manifest_sha256=store.manifest_sha256(source),
            source_attempt=attempt_number,
            task_sha256=expected_task_sha,
            plan_sha256=_canonical_sha256(plan.model_dump(mode="json")),
            plan=plan,
            capability=capability,
            public_validation_pass=(
                summary.native_status == "PUBLIC_VALIDATION_PASS"
            ),
        )
    raise PlanReuseError(
        "SOURCE_RUN_NOT_ELIGIBLE",
        "no manifested attempt has mesh, Mesh OK, and normal solver completion",
    )
