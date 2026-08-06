"""Create reviewable candidates from immutable public run evidence."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from foampilot.artifacts import ArtifactStore
from foampilot.qualification.models import QualificationReport

from .models import (
    ImprovementTarget,
    LearningCandidate,
    OfficialExampleEvidence,
    PublicEvidence,
    RootCause,
    SourceRun,
)


_STATUS_CAUSES = {
    "REQUEST_INCOMPLETE": RootCause.TASK_SPEC,
    "BLOCKED_ENVIRONMENT": RootCause.ENVIRONMENT,
    "PLAN_INVALID": RootCause.CASE_GENERATION,
    "GENERATION_INVALID": RootCause.CASE_GENERATION,
    "CASE_GENERATION_FAILED": RootCause.CASE_GENERATION,
    "STATIC_INSPECTION_FAILED": RootCause.VERSION_CONTRACT,
    "MESH_FAILED": RootCause.MESH,
    "MESH_QUALITY_FAILED": RootCause.MESH,
    "INITIALIZATION_FAILED": RootCause.INITIALIZATION,
    "SOLVER_FAILED": RootCause.NUMERICS,
    "POSTPROCESS_FAILED": RootCause.VALIDATION,
    "PUBLIC_VALIDATION_FAILED": RootCause.VALIDATION,
}

_DEFAULT_PROMOTION_CRITERIA = [
    "source_improves",
    "regression_no_regression",
    "holdout_non_decreasing",
    "physics_pass_increases",
    "cost_within_budget",
]


def infer_root_cause(status: str) -> RootCause:
    try:
        return _STATUS_CAUSES[status]
    except KeyError as error:
        raise ValueError(
            f"run status does not support an improvement candidate: {status}"
        ) from error


def directory_sha256(path: str | Path) -> str:
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"official example is not a directory: {root}")
    digest = sha256()
    sources = sorted(root.rglob("*"))
    for source in sources:
        if source.is_symlink():
            raise ValueError(f"official example contains a symlink: {source}")
    for source in (item for item in sources if item.is_file()):
        digest.update(source.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _present_unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def create_learning_candidate(
    *,
    run_dir: str | Path,
    qualification_report: QualificationReport,
    candidate_id: str,
    generalized_lesson: str,
    proposed_target: ImprovementTarget,
    root_cause: RootCause | None = None,
    official_example: str | Path | None = None,
    extracted_principles: list[str] | None = None,
    leakage_families: list[str] | None = None,
    development_cases: list[str] | None = None,
    regression_cases: list[str] | None = None,
    holdout_cases: list[str] | None = None,
    promotion_criteria: list[str] | None = None,
) -> LearningCandidate:
    directory = Path(run_dir).resolve()
    store = ArtifactStore(directory.parent)
    manifest_issues = store.verify(directory)
    if manifest_issues:
        details = "; ".join(manifest_issues)
        raise ValueError(f"artifact manifest verification failed: {details}")

    summary = store.read_summary(directory)
    matching_results = [
        result
        for result in qualification_report.results
        if result.run_dir.resolve() == directory
    ]
    if len(matching_results) != 1:
        raise ValueError(
            "qualification report must contain exactly one result for "
            f"{directory}; found {len(matching_results)}"
        )
    qualification_result = matching_results[0]

    manifest = directory / ArtifactStore.manifest_name
    source_run = SourceRun(
        path=directory,
        manifest_sha256=_file_sha256(manifest),
    )
    failure_fingerprints = _present_unique(
        [attempt.failure_fingerprint for attempt in summary.attempts]
    )
    failed_steps = _present_unique(
        [attempt.failed_step_id for attempt in summary.attempts]
    )
    observations = _present_unique(
        [summary.message, qualification_result.message]
    )

    if official_example is None:
        official_evidence = OfficialExampleEvidence()
    else:
        official_evidence = OfficialExampleEvidence(
            used=True,
            source_sha256=directory_sha256(official_example),
            extracted_principles=extracted_principles or [],
        )

    return LearningCandidate(
        candidate_id=candidate_id,
        source_runs=[source_run],
        root_cause=root_cause or infer_root_cause(summary.status),
        public_evidence=PublicEvidence(
            failure_fingerprints=failure_fingerprints,
            failed_steps=failed_steps,
            observations=observations,
        ),
        official_example=official_evidence,
        generalized_lesson=generalized_lesson,
        proposed_target=proposed_target,
        leakage_families=leakage_families or [],
        development_cases=development_cases or [],
        regression_cases=regression_cases or [],
        holdout_cases=holdout_cases or [],
        promotion_criteria=promotion_criteria
        or list(_DEFAULT_PROMOTION_CRITERIA),
    )
