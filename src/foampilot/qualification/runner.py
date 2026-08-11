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
    ModelGateway,
)
from foampilot.runtime import (
    RuntimeConfigError,
    RuntimeResolution,
    run_preflight,
)
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
    *,
    public_validation_only: set[str] | None = None,
) -> list[str]:
    """Validate every selected task and evaluator asset before execution."""

    issues: list[str] = []
    public_only = public_validation_only or set()
    for case_id in case_ids:
        try:
            task = load_task_spec(
                qualification_data_path("tasks", case_id)
            )
            if task.task_id != case_id:
                raise ValueError(
                    f"task_id {task.task_id!r} does not match {case_id!r}"
                )
            if case_id in public_only:
                _expected_application(task)
                continue
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


def _expected_application(task) -> str:
    candidates = [
        check.parameters.get("executable")
        for check in task.public_checks
        if check.kind == "command_executed"
        and isinstance(check.parameters.get("executable"), str)
    ]
    if len(candidates) != 1:
        raise ValueError(
            "public-validation task must declare exactly one "
            "command_executed target solver"
        )
    return str(candidates[0])


def evaluate_case_copy(
    case_id: str,
    source_case: Path,
    *,
    openfoam_root: Path,
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
            openfoam_root=openfoam_root,
        )
        return validate_observations(observations, reference)


def qualify_outcome(
    case_id: str,
    outcome: NativeAgentOutcome,
    *,
    artifact_store: ArtifactStore,
    duration_seconds: float,
    evaluation_level: str = "physics_qualification",
    expected_application: str | None = None,
    openfoam_root: Path,
) -> dict[str, object]:
    """Create one raw report record from native and evaluator evidence."""

    manifest_issues = artifact_store.verify(outcome.run_dir)
    metrics: list[QualificationMetric] = []
    message = outcome.summary.message
    if (
        evaluation_level == "physics_qualification"
        and outcome.status == "PUBLIC_VALIDATION_PASS"
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
                metrics = evaluate_case_copy(
                    case_id,
                    case_dir,
                    openfoam_root=openfoam_root,
                )
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
        "expected_application": (
            expected_application
            if evaluation_level == "public_validation"
            else load_private_validation(case_id).expected_application
        ),
        "evaluation_level": evaluation_level,
    }


def _run_one(
    case_id: str,
    *,
    run_root: Path,
    gateway: ModelGateway,
    runtime_resolution: RuntimeResolution,
    evaluation_level: str = "physics_qualification",
) -> dict[str, object]:
    task = load_task_spec(qualification_data_path("tasks", case_id))
    store = ArtifactStore(run_root / case_id)
    evaluator_root = qualification_data_path("tasks", case_id).parent.parent
    started = time.monotonic()
    outcome = NativeAgent(
        gateway=gateway,
        runtime_config=runtime_resolution.config,
        runtime_provenance=runtime_resolution.provenance,
        protected_runtime_roots=(evaluator_root,),
        artifact_store=store,
    ).solve(task)
    return qualify_outcome(
        case_id,
        outcome,
        artifact_store=store,
        duration_seconds=time.monotonic() - started,
        evaluation_level=evaluation_level,
        openfoam_root=runtime_resolution.config.openfoam_root,
        expected_application=(
            _expected_application(task)
            if evaluation_level == "public_validation"
            else None
        ),
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
    backend_id: str,
    model_name: str,
    gateway: ModelGateway,
    runtime_resolution: RuntimeResolution,
) -> QualificationReport:
    """Run one strict suite through the existing native qualification path."""

    if workers not in {1, 2}:
        raise ValueError("workers must be 1 or 2")
    if runtime_resolution.config.isolation != "sandbox_required":
        raise RuntimeConfigError(
            "RUNTIME_POLICY_CONFLICT",
            "Qualification 必须使用 sandbox_required。",
            "修改 Runtime isolation 后重新运行 qualification。",
        )
    preflight = run_preflight(
        runtime_resolution.config,
        workspace_root=run_root,
    )
    if not preflight.ok or preflight.environment is None:
        probe = preflight.sandbox_probe
        code = (
            preflight.failure_code
            or probe.failure_code
            or "OPENFOAM_DISCOVERY_FAILED"
        )
        if code in {"BWRAP_UNAVAILABLE", "NAMESPACE_UNAVAILABLE"}:
            code = "SANDBOX_REQUIRED_UNAVAILABLE"
        raise RuntimeConfigError(
            code,
            preflight.failure_message or "Qualification Runtime preflight 未通过。",
            preflight.failure_recovery
            or "修复 Foundation v10 和 bubblewrap/namespace 后重试。",
            preflight.failure_message or probe.detail,
        )
    if preflight.environment.tutorial_root is None:
        raise RuntimeConfigError(
            "OPENFOAM_DISCOVERY_FAILED",
            "当前 Foundation v10 安装缺少 FOAM_TUTORIALS；qualification 无法执行官方算例。",
            "安装匹配版本的 tutorials，或修复 etc/bashrc 中的 FOAM_TUTORIALS。",
        )
    selected = [item.case_id for item in suite.cases]
    public_only = {
        item.case_id
        for item in suite.cases
        if item.evaluation_level == "public_validation"
    }
    issues = validate_qualification_inputs(
        selected,
        public_validation_only=public_only,
    )
    if issues:
        raise ValueError(
            "qualification inputs are invalid: " + "; ".join(issues)
        )

    active_gateway = gateway
    if isinstance(active_gateway, ModelGateway):
        if active_gateway.mode.value != "qualification":
            raise ValueError("qualification gateway must use pinned mode")
        if (
            active_gateway.primary_backend_id != backend_id
            or active_gateway.primary_model != model_name
        ):
            raise ValueError(
                "qualification gateway does not match pinned backend/model"
            )
    raw_results: list[dict[str, object]] = []
    parallel = [
        item
        for item in suite.cases
        if not item.exclusive
    ]
    with ThreadPoolExecutor(
        max_workers=min(workers, suite.max_workers)
    ) as executor:
        futures = {}
        for item in parallel:
            arguments = {
                "run_root": run_root,
                "gateway": active_gateway,
                "runtime_resolution": runtime_resolution,
            }
            if item.evaluation_level != "physics_qualification":
                arguments["evaluation_level"] = item.evaluation_level
            future = executor.submit(
                _run_one,
                item.case_id,
                **arguments,
            )
            futures[future] = item.case_id
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
                runtime_resolution=runtime_resolution,
                evaluation_level=item.evaluation_level,
            )
        )

    report = build_qualification_report(
        raw_results,
        backend_id=backend_id,
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
    backend_id: str,
    model_name: str,
    gateway: ModelGateway,
    runtime_resolution: RuntimeResolution,
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
        backend_id=backend_id,
        model_name=model_name,
        gateway=gateway,
        runtime_resolution=runtime_resolution,
    )
