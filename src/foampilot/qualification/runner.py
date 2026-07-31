"""Run installable qualification suites through NativeAgent."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.resources import files
from pathlib import Path

import yaml

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore, NativeAgentOutcome
from foampilot.models import (
    CodexOAuthProviderClient,
    ModelGateway,
    SharedCircuitBreaker,
    load_codex_access_token,
)
from foampilot.runtime import RuntimeConfig
from foampilot.tasks import load_task_spec

from .models import (
    PrivateValidation,
    QualificationMetric,
    QualificationReport,
)
from .reporting import (
    CASE_ORDER,
    build_qualification_report,
    markdown_report,
    native_case_dir,
)
from .suites import (
    QualificationSuite,
    load_qualification_suite,
    qualification_suite_path,
)
from .validators import extract_observations, validate_observations


def qualification_data_path(kind: str, case_id: str) -> Path:
    """Resolve one packaged qualification asset."""

    suffix = "json" if kind == "references" else "yaml"
    resource = (
        files("foampilot.qualification")
        .joinpath("data", kind, f"{case_id}.{suffix}")
    )
    return Path(str(resource))


def load_private_validation(case_id: str) -> PrivateValidation:
    """Load one evaluator-owned validation contract."""

    source = qualification_data_path("validation", case_id)
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"validation root must be a mapping: {source}")
    return PrivateValidation.model_validate(payload)


def load_reference(case_id: str) -> dict[str, object]:
    """Load one compact derived reference document."""

    source = qualification_data_path("references", case_id)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"reference root must be a mapping: {source}")
    return payload


def validate_qualification_inputs(
    case_ids: list[str],
) -> list[str]:
    """Validate every selected task and evaluator asset before execution."""

    issues: list[str] = []
    for case_id in case_ids:
        try:
            task = load_task_spec(
                qualification_data_path("tasks", case_id)
            )
            if task.task_id != case_id:
                raise ValueError(
                    f"task_id {task.task_id!r} does not match {case_id!r}"
                )
            validation = load_private_validation(case_id)
            reference = load_reference(case_id)
            if validation.case_id != case_id:
                raise ValueError(
                    f"validation case_id does not match {case_id!r}"
                )
            if reference.get("case_id") != case_id:
                raise ValueError(
                    f"reference case_id does not match {case_id!r}"
                )
        except Exception as error:
            issues.append(f"{case_id}: {type(error).__name__}: {error}")
    return issues


def evaluate_case_copy(
    case_id: str,
    source_case: Path,
) -> list[QualificationMetric]:
    """Evaluate a copy so VTK markers cannot alter immutable artifacts."""

    with tempfile.TemporaryDirectory(
        prefix=f"foampilot-qualification-{case_id}-"
    ) as temporary:
        case_copy = Path(temporary) / "case"
        shutil.copytree(source_case, case_copy, symlinks=True)
        validation = load_private_validation(case_id)
        reference = load_reference(case_id)
        observations = extract_observations(
            case_id,
            case_copy,
            validation,
        )
        return validate_observations(observations, reference)


def qualify_outcome(
    case_id: str,
    outcome: NativeAgentOutcome,
    *,
    artifact_store: ArtifactStore,
    duration_seconds: float,
) -> dict[str, object]:
    """Create one raw report record from native and evaluator evidence."""

    manifest_issues = artifact_store.verify(outcome.run_dir)
    metrics: list[QualificationMetric] = []
    message = outcome.summary.message
    if (
        outcome.status == "PUBLIC_VALIDATION_PASS"
        and not manifest_issues
    ):
        case_dir = native_case_dir(outcome)
        if case_dir is None or not case_dir.is_dir():
            metrics = [
                QualificationMetric(
                    name="generated_case",
                    passed=False,
                    detail="final native attempt has no generated case",
                )
            ]
        else:
            try:
                metrics = evaluate_case_copy(case_id, case_dir)
            except Exception as error:
                metrics = [
                    QualificationMetric(
                        name="physics_evaluation",
                        passed=False,
                        detail=f"{type(error).__name__}: {error}",
                    )
                ]
                message = (
                    "The generated result could not satisfy the "
                    "qualification evaluator."
                )
    return {
        "case_id": case_id,
        "outcome": outcome,
        "manifest_issues": manifest_issues,
        "metrics": metrics,
        "duration_seconds": duration_seconds,
        "message": message,
        "expected_application": load_private_validation(
            case_id
        ).expected_application,
    }


def _run_one(
    case_id: str,
    *,
    run_root: Path,
    gateway: ModelGateway,
) -> dict[str, object]:
    task = load_task_spec(qualification_data_path("tasks", case_id))
    config = RuntimeConfig.local_foundation_v10().model_copy(
        update={"max_mpi_ranks": task.resource_budget.max_mpi_ranks}
    )
    store = ArtifactStore(run_root / case_id)
    started = time.monotonic()
    outcome = NativeAgent(
        gateway=gateway,
        runtime_config=config,
        artifact_store=store,
    ).solve(task)
    return qualify_outcome(
        case_id,
        outcome,
        artifact_store=store,
        duration_seconds=time.monotonic() - started,
    )


def write_qualification_report(
    report: QualificationReport,
    run_root: Path,
) -> tuple[Path, Path]:
    """Write JSON and Markdown beside the per-case run directories."""

    run_root.mkdir(parents=True, exist_ok=True)
    json_path = run_root / f"{report.protocol_id}-report.json"
    markdown_path = run_root / f"{report.protocol_id}-report.md"
    json_path.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        markdown_report(report),
        encoding="utf-8",
    )
    return json_path, markdown_path


def run_qualification_suite(
    *,
    suite: QualificationSuite,
    run_root: Path,
    workers: int,
    model_name: str,
    auth: Path | None,
    gateway: ModelGateway | None = None,
) -> QualificationReport:
    """Run one strict suite through the existing native qualification path."""

    if workers not in {1, 2}:
        raise ValueError("workers must be 1 or 2")
    selected = [item.case_id for item in suite.cases]
    issues = validate_qualification_inputs(selected)
    if issues:
        raise ValueError(
            "qualification inputs are invalid: " + "; ".join(issues)
        )

    active_gateway = gateway
    if active_gateway is None:
        if auth is None:
            raise ValueError(
                "auth is required when gateway is not injected"
            )
        access_token = load_codex_access_token(auth)
        provider = CodexOAuthProviderClient(
            model=model_name,
            access_token=access_token,
        )
        active_gateway = ModelGateway(
            provider=provider,
            circuit_breaker=SharedCircuitBreaker(),
        )
    raw_results: list[dict[str, object]] = []
    parallel = [
        item.case_id
        for item in suite.cases
        if not item.exclusive
    ]
    with ThreadPoolExecutor(
        max_workers=min(workers, suite.max_workers)
    ) as executor:
        futures = {
            executor.submit(
                _run_one,
                case_id,
                run_root=run_root,
                gateway=active_gateway,
            ): case_id
            for case_id in parallel
        }
        for future in as_completed(futures):
            raw_results.append(future.result())
    for item in suite.cases:
        if not item.exclusive:
            continue
        raw_results.append(
            _run_one(
                item.case_id,
                run_root=run_root,
                gateway=active_gateway,
            )
        )

    report = build_qualification_report(
        raw_results,
        model_name=model_name,
        protocol_id=suite.protocol_id,
        case_order=tuple(selected),
    )
    write_qualification_report(report, run_root)
    return report


def run_official_six(
    *,
    run_root: Path,
    workers: int,
    model_name: str,
    auth: Path,
    case_ids: list[str] | None = None,
) -> QualificationReport:
    """Run selected official-six cases through the generic suite runner."""

    suite = load_qualification_suite(
        qualification_suite_path("official-six-v1")
    )
    if case_ids:
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case IDs must be unique")
        if any(case_id not in CASE_ORDER for case_id in case_ids):
            raise ValueError("unknown official-six case ID")
        selected = set(case_ids)
        suite = suite.model_copy(
            update={
                "cases": [
                    item for item in suite.cases if item.case_id in selected
                ]
            }
        )
    return run_qualification_suite(
        suite=suite,
        run_root=run_root,
        workers=workers,
        model_name=model_name,
        auth=auth,
    )
