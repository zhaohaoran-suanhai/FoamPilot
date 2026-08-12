"""Command-line entry point."""

from __future__ import annotations

import argparse
from contextvars import ContextVar
from hashlib import sha256
import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

import yaml

from foampilot.activity import (
    ActivityReporter,
    JsonlStreamActivitySink,
    OperationCancelled,
    PlainActivitySink,
)
from foampilot.agent import (
    NativeAgent,
)
from foampilot.assets import BundleMember, compute_bundle_manifest_sha256
from foampilot.artifacts import ArtifactStore
from foampilot.environment import discover_environment
from foampilot.inspection import inspect_native_case
from foampilot.improvement import (
    ImprovementTarget,
    RootCause,
    compare_promotion,
    create_learning_candidate,
    load_learning_candidate,
    write_learning_candidate,
)
from foampilot.knowledge import (
    KnowledgeQuery,
    build_knowledge_coverage,
    load_knowledge_corpus,
    select_knowledge,
    verify_knowledge_manifest,
)
from foampilot.physics import (
    RiemannState,
    audit_wall_heat_flux,
    ideal_gas_density,
    solve_ideal_gas_riemann,
)
from foampilot.models import (
    BackendMode,
    BackendRegistry,
    GatewayRequestError,
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelGateway,
    ModelStage,
    backend_error_payload_zh,
    doctor_backends,
    load_backend_registry,
)
from foampilot.plans import ExecutionPlan, validate_execution_plan
from foampilot.performance import build_taskbuilder_performance
from foampilot.qualification import (
    QualificationReport,
    load_qualification_suite,
    run_official_six,
    run_qualification_suite,
)
from foampilot.runtime import (
    IsolationPolicy,
    RuntimeConfigError,
    RuntimeExecutionError,
    RuntimeOverrides,
    RuntimeResolution,
    preflight_passed,
    resolve_runtime_config,
    run_preflight,
)
from foampilot.skills import (
    load_skill_scenarios,
    validate_skill,
)
from foampilot.tasks import load_task_spec
from foampilot.tasks import PublicAsset
from foampilot.taskbuilder import (
    TaskDraft,
    compile_task_draft,
    extract_task_draft,
    validate_task_draft,
)
from foampilot.desktop import DesktopDependencyError


COMMANDS = (
    "validate",
    "plan",
    "solve",
    "resume",
    "rerun",
    "inspect",
    "report",
    "questions",
    "confirm",
    "preflight",
    "desktop",
    "model",
    "knowledge",
    "skill",
    "audit",
    "qualify",
    "improve",
    "task",
    "worker",
    "job",
)

KNOWLEDGE_TYPES = (
    "solver_guide",
    "mesh_pattern",
    "boundary_condition",
    "physics_model",
    "numerics",
    "error_playbook",
    "parallel_execution",
    "validation_pattern",
)

MAX_TASK_ASSET_BYTES = 256 * 1024 * 1024
_CLI_ACTIVITY_REPORTER: ContextVar[ActivityReporter | None] = ContextVar(
    "foampilot_cli_activity_reporter",
    default=None,
)


def _add_progress_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--progress",
        choices=("auto", "plain", "jsonl", "none"),
        default="auto",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foampilot",
        description="FoamPilot",
    )
    subparsers = parser.add_subparsers(dest="command")

    native_validate = subparsers.add_parser("validate")
    native_validate.add_argument("path", type=Path)
    native_validate.add_argument("--json", action="store_true")

    native_plan = subparsers.add_parser("plan")
    native_plan.add_argument("path", type=Path)
    native_plan.add_argument("--output", required=True, type=Path)
    native_plan.add_argument("--run-root", type=Path)
    native_plan.add_argument("--public-asset-root", type=Path)
    native_plan.add_argument("--derived-cache", type=Path)
    _add_backend_options(native_plan)
    _add_runtime_options(native_plan)
    native_plan.add_argument("--json", action="store_true")
    _add_progress_option(native_plan)

    native_solve = subparsers.add_parser("solve")
    native_solve.add_argument("path", type=Path)
    native_solve.add_argument("--run-root", required=True, type=Path)
    native_solve.add_argument("--public-asset-root", type=Path)
    native_solve.add_argument("--reuse-verified-plan", type=Path)
    native_solve.add_argument("--derived-cache", type=Path)
    _add_backend_options(native_solve)
    _add_runtime_options(native_solve)
    native_solve.add_argument("--json", action="store_true")
    _add_progress_option(native_solve)

    native_resume = subparsers.add_parser("resume")
    native_resume.add_argument("parent_run", type=Path)
    native_resume.add_argument("--run-root", required=True, type=Path)
    _add_backend_options(native_resume)
    _add_runtime_options(native_resume)
    native_resume.add_argument("--json", action="store_true")
    _add_progress_option(native_resume)

    native_rerun = subparsers.add_parser("rerun")
    native_rerun.add_argument("parent_run", type=Path)
    native_rerun.add_argument("--run-root", required=True, type=Path)
    native_rerun.add_argument("--task", type=Path)
    native_rerun.add_argument("--public-asset-root", type=Path)
    native_rerun.add_argument("--change-category", action="append", default=[])
    _add_backend_options(native_rerun)
    _add_runtime_options(native_rerun)
    native_rerun.add_argument("--json", action="store_true")
    _add_progress_option(native_rerun)

    native_inspect = subparsers.add_parser("inspect")
    native_inspect.add_argument("task", type=Path)
    native_inspect.add_argument("plan", type=Path)
    native_inspect.add_argument("case_dir", type=Path)
    _add_runtime_options(native_inspect)
    native_inspect.add_argument("--json", action="store_true")

    report = subparsers.add_parser("report")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--json", action="store_true")

    questions = subparsers.add_parser("questions")
    questions.add_argument("run_dir", type=Path)
    questions.add_argument("--json", action="store_true")

    confirm = subparsers.add_parser("confirm")
    confirm.add_argument("run_dir", type=Path)
    confirm.add_argument("--answers", required=True, type=Path)
    confirm.add_argument("--run-root", required=True, type=Path)
    confirm.add_argument("--json", action="store_true")

    preflight = subparsers.add_parser("preflight")
    _add_runtime_options(preflight)
    preflight.add_argument("--json", action="store_true")

    desktop = subparsers.add_parser("desktop")
    desktop.add_argument("--open-run", type=Path)
    _add_runtime_options(desktop)

    model = subparsers.add_parser("model")
    model_commands = model.add_subparsers(dest="model_command")
    model_doctor = model_commands.add_parser("doctor")
    model_doctor.add_argument("--backend-config", type=Path)
    model_doctor.add_argument("--json", action="store_true")

    task = subparsers.add_parser("task")
    task_commands = task.add_subparsers(dest="task_command")
    task_draft = task_commands.add_parser("draft")
    task_draft.add_argument("--request-file", required=True, type=Path)
    task_draft.add_argument("--asset", action="append", type=Path, default=[])
    task_draft.add_argument(
        "--asset-dir",
        action="append",
        type=Path,
        default=[],
    )
    task_draft.add_argument(
        "--asset-install-path",
        action="append",
        type=Path,
        default=[],
    )
    task_draft.add_argument("--asset-root", type=Path)
    task_draft.add_argument(
        "--protected-path",
        action="append",
        type=Path,
        default=[],
    )
    task_draft.add_argument("--output", required=True, type=Path)
    _add_backend_options(task_draft)
    task_draft.add_argument("--json", action="store_true")
    _add_progress_option(task_draft)

    task_validate = task_commands.add_parser("validate-draft")
    task_validate.add_argument("path", type=Path)
    task_validate.add_argument("--json", action="store_true")

    task_compile = task_commands.add_parser("compile")
    task_compile.add_argument("path", type=Path)
    task_compile.add_argument("--output", required=True, type=Path)
    task_compile.add_argument("--json", action="store_true")

    knowledge = subparsers.add_parser("knowledge")
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command")
    knowledge_validate = knowledge_commands.add_parser("validate")
    knowledge_validate.add_argument("root", type=Path)
    knowledge_validate.add_argument("--json", action="store_true")
    knowledge_coverage = knowledge_commands.add_parser("coverage")
    knowledge_coverage.add_argument("root", type=Path)
    knowledge_coverage.add_argument("--json", action="store_true")
    knowledge_search = knowledge_commands.add_parser("search")
    knowledge_search.add_argument("root", type=Path)
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--solver")
    knowledge_search.add_argument(
        "--type",
        dest="knowledge_types",
        action="append",
        choices=KNOWLEDGE_TYPES,
        default=[],
    )
    knowledge_search.add_argument("--family")
    knowledge_search.add_argument("--formal", action="store_true")
    knowledge_search.add_argument(
        "--allow-development-family",
        dest="allowed_development_families",
        action="append",
        default=[],
    )
    knowledge_search.add_argument("--limit", type=int, default=5)
    knowledge_search.add_argument("--json", action="store_true")

    skill = subparsers.add_parser("skill")
    skill_commands = skill.add_subparsers(dest="skill_command")
    skill_validate = skill_commands.add_parser("validate")
    skill_validate.add_argument("skill_dir", type=Path)
    skill_validate.add_argument("--scenarios", type=Path)
    skill_validate.add_argument("--json", action="store_true")

    audit = subparsers.add_parser("audit")
    audit_commands = audit.add_subparsers(dest="audit_command")
    shock = audit_commands.add_parser("shock-tube")
    shock.add_argument("--left-pressure", type=float, required=True)
    shock.add_argument("--left-temperature", type=float, required=True)
    shock.add_argument("--left-velocity", type=float, default=0)
    shock.add_argument("--right-pressure", type=float, required=True)
    shock.add_argument("--right-temperature", type=float, required=True)
    shock.add_argument("--right-velocity", type=float, default=0)
    shock.add_argument("--molecular-weight", type=float, required=True)
    shock.add_argument("--cp", type=float, required=True)
    shock.add_argument("--diaphragm-position", type=float, default=0)
    shock.add_argument("--time", type=float, required=True)
    shock.add_argument("--json", action="store_true")
    wall_heat = audit_commands.add_parser("wall-heat-flux")
    wall_heat.add_argument("case_dir", type=Path)
    _add_runtime_options(wall_heat)
    wall_heat.add_argument("--hot-patch", required=True)
    wall_heat.add_argument("--cold-patch", required=True)
    wall_heat.add_argument("--json", action="store_true")

    improve = subparsers.add_parser("improve")
    improve_commands = improve.add_subparsers(dest="improve_command")
    improve_analyze = improve_commands.add_parser("analyze")
    improve_analyze.add_argument("run_dir", type=Path)
    improve_analyze.add_argument(
        "--qualification-report",
        required=True,
        type=Path,
    )
    improve_analyze.add_argument("--candidate-id", required=True)
    improve_analyze.add_argument("--lesson", required=True)
    improve_analyze.add_argument(
        "--target",
        required=True,
        choices=tuple(item.value for item in ImprovementTarget),
    )
    improve_analyze.add_argument(
        "--root-cause",
        choices=tuple(item.value for item in RootCause),
    )
    improve_analyze.add_argument("--official-example", type=Path)
    improve_analyze.add_argument(
        "--principle",
        action="append",
        default=[],
    )
    improve_analyze.add_argument(
        "--leakage-family",
        action="append",
        default=[],
    )
    improve_analyze.add_argument(
        "--development-case",
        action="append",
        default=[],
    )
    improve_analyze.add_argument(
        "--regression-case",
        action="append",
        default=[],
    )
    improve_analyze.add_argument(
        "--holdout-case",
        action="append",
        default=[],
    )
    improve_analyze.add_argument(
        "--criterion",
        action="append",
        default=[],
    )
    improve_analyze.add_argument("--output", required=True, type=Path)
    improve_analyze.add_argument("--json", action="store_true")

    improve_compare = improve_commands.add_parser("compare")
    improve_compare.add_argument("baseline_report", type=Path)
    improve_compare.add_argument("current_report", type=Path)
    improve_compare.add_argument("--candidate", required=True, type=Path)
    improve_compare.add_argument("--output", required=True, type=Path)
    improve_compare.add_argument("--json", action="store_true")

    qualify = subparsers.add_parser("qualify")
    qualify.add_argument("suite", choices=("official-six", "suite"))
    qualify.add_argument("--suite-file", type=Path)
    qualify.add_argument("--run-root", required=True, type=Path)
    qualify.add_argument("--workers", type=int, choices=(1, 2), default=2)
    _add_backend_options(qualify)
    _add_runtime_options(qualify)
    qualify.add_argument("--json", action="store_true")

    worker = subparsers.add_parser("worker")
    worker_commands = worker.add_subparsers(dest="worker_command")
    worker_run = worker_commands.add_parser("run")
    worker_run.add_argument("job_root", type=Path)

    job = subparsers.add_parser("job")
    job_commands = job.add_subparsers(dest="job_command")
    job_status = job_commands.add_parser("status")
    job_status.add_argument("job_root", type=Path)
    job_status.add_argument("--json", action="store_true")
    job_cancel = job_commands.add_parser("cancel")
    job_cancel.add_argument("job_root", type=Path)
    job_cancel.add_argument("--json", action="store_true")
    job_reconcile = job_commands.add_parser("reconcile")
    job_reconcile.add_argument("job_root", type=Path)
    job_reconcile.add_argument("--heartbeat-stale-seconds", type=float, default=5.0)
    job_reconcile.add_argument("--json", action="store_true")
    job_terminate = job_commands.add_parser("terminate-orphan")
    job_terminate.add_argument("job_root", type=Path)
    job_terminate.add_argument("--grace-seconds", type=float, default=2.0)
    job_terminate.add_argument("--json", action="store_true")
    job_recover = job_commands.add_parser("recover-finalize")
    job_recover.add_argument("job_root", type=Path)
    job_recover.add_argument("--json", action="store_true")
    return parser


def _add_backend_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--backend-config", type=Path)
    parser.add_argument("--model-name")


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--openfoam-root", type=Path)
    parser.add_argument(
        "--execution-isolation",
        choices=("sandbox_required", "sandbox_preferred", "trusted_host"),
    )
    parser.add_argument("--bubblewrap")
    parser.add_argument("--max-mpi-ranks", type=int)
    parser.add_argument(
        "--allow-dynamic-code-on-host",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--trusted-readonly-root",
        action="append",
        type=Path,
        default=None,
    )


def _resolve_runtime(
    arguments: argparse.Namespace,
    *,
    default_isolation: IsolationPolicy = "sandbox_preferred",
) -> RuntimeResolution:
    trusted_roots = arguments.trusted_readonly_root
    return resolve_runtime_config(
        explicit_config=arguments.runtime_config,
        cli_overrides=RuntimeOverrides(
            openfoam_root=arguments.openfoam_root,
            isolation=arguments.execution_isolation,
            bubblewrap=arguments.bubblewrap,
            max_mpi_ranks=arguments.max_mpi_ranks,
            allow_dynamic_code_on_host=(
                arguments.allow_dynamic_code_on_host
            ),
            trusted_readonly_roots=(
                tuple(trusted_roots) if trusted_roots is not None else None
            ),
        ),
        default_isolation=default_isolation,
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the public CLI parser for documentation and tests."""

    return _parser()


def _emit(payload: object, *, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human)


def _activity_reporter(
    arguments: argparse.Namespace,
    *,
    stderr: TextIO | None = None,
) -> ActivityReporter:
    injected = _CLI_ACTIVITY_REPORTER.get()
    if injected is not None:
        return injected
    stream = stderr or sys.stderr
    mode = str(getattr(arguments, "progress", "auto"))
    listeners = []
    if mode == "jsonl":
        listeners.append(JsonlStreamActivitySink(stream))
    elif mode == "plain" or (
        mode == "auto" and bool(getattr(stream, "isatty", lambda: False)())
    ):
        listeners.append(PlainActivitySink(stream))
    return ActivityReporter(
        operation_id=uuid4().hex,
        listeners=listeners,
    )


def _worker(arguments: argparse.Namespace) -> int:
    if arguments.worker_command != "run":
        raise ValueError("a worker subcommand is required")
    from foampilot.jobs import run_local_job

    return run_local_job(arguments.job_root)


def _job(arguments: argparse.Namespace) -> int:
    from foampilot.jobs import (
        LocalJobStore,
        JobState,
        RecoveryAction,
        reconcile_job,
        recover_finalize,
        terminate_orphan,
    )

    store = LocalJobStore(arguments.job_root)
    if arguments.job_command == "status":
        status = store.read_status()
        _emit(
            status.model_dump(mode="json"),
            as_json=arguments.json,
            human=(
                f"{status.state.value}: {status.job_id} "
                f"revision={status.revision}"
            ),
        )
        return 0
    if arguments.job_command == "cancel":
        status = store.read_status()
        decision = reconcile_job(store.root)
        pristine_submission = (
            status.state == JobState.SUBMITTED
            and status.worker is None
            and status.current_child is None
            and not decision.writer_lock_held
        )
        if (
            RecoveryAction.CANCEL not in decision.allowed_actions
            and not pristine_submission
        ):
            raise ValueError(
                f"job cannot be cancelled in recovery state "
                f"{decision.state.value}: {decision.code}"
            )
        request = store.request_cancel(requested_by="cli")
        observed_status = store.read_status()
        if observed_status.state in {
            JobState.CANCELLED,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.INTERRUPTED,
        }:
            observed = reconcile_job(store.root)
            _emit(
                {
                    "status": "ALREADY_TERMINAL",
                    "job_state": observed_status.state.value,
                    "code": observed.code,
                    "request": request.model_dump(mode="json"),
                },
                as_json=arguments.json,
                human=(
                    f"ALREADY_TERMINAL: {observed_status.state.value} "
                    f"{observed.code}"
                ),
            )
            return 0
        _emit(
            {
                "status": "CANCEL_REQUESTED",
                "request": request.model_dump(mode="json"),
            },
            as_json=arguments.json,
            human=f"CANCEL_REQUESTED: {request.job_id}",
        )
        return 0
    if arguments.job_command == "reconcile":
        decision = reconcile_job(
            store.root,
            heartbeat_stale_seconds=arguments.heartbeat_stale_seconds,
        )
        _emit(
            decision.model_dump(mode="json"),
            as_json=arguments.json,
            human=f"{decision.state.value}: {decision.reason_zh}",
        )
        return 0
    if arguments.job_command == "terminate-orphan":
        decision = terminate_orphan(
            store.root,
            grace_seconds=arguments.grace_seconds,
        )
        _emit(
            decision.model_dump(mode="json"),
            as_json=arguments.json,
            human=f"{decision.state.value}: {decision.recovery_zh}",
        )
        return 0
    if arguments.job_command == "recover-finalize":
        decision = recover_finalize(store.root)
        _emit(
            decision.model_dump(mode="json"),
            as_json=arguments.json,
            human=f"{decision.state.value}: interrupted evidence finalized",
        )
        return 0
    raise ValueError("a job subcommand is required")


def _questions(arguments: argparse.Namespace) -> int:
    run_dir = arguments.run_dir.resolve()
    store = ArtifactStore(run_dir.parent)
    problems = store.verify(run_dir)
    if problems:
        raise ValueError("PARENT_MANIFEST_INVALID: " + "; ".join(problems))
    raw = json.loads(
        (run_dir / "questions.json").read_text(encoding="utf-8")
    )
    if not isinstance(raw, dict):
        raise ValueError("questions.json root must be a mapping")
    raw_questions = raw.get("questions", [])
    requirement_gaps = raw.get("requirement_gaps", [])
    requirement_conflicts = raw.get("requirement_conflicts", [])
    if not all(
        isinstance(value, list)
        for value in (
            raw_questions,
            requirement_gaps,
            requirement_conflicts,
        )
    ):
        raise ValueError("questions.json lists are invalid")
    payload = {
        "status": str(raw.get("state", "INFORMATION_REQUIRED")),
        "run_dir": str(run_dir),
        "proposal_sha256": raw.get("proposal_sha256"),
        "questions": raw_questions,
        "requirement_gaps": requirement_gaps,
        "requirement_conflicts": requirement_conflicts,
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            f"{payload['status']}: "
            f"{len(raw_questions) + len(requirement_gaps)} question(s)."
        ),
    )
    return 0


def _confirm(arguments: argparse.Namespace) -> int:
    from foampilot.workflow.confirmation import (
        apply_confirmation_records,
        load_confirmation_parent,
        parse_answers,
        persist_confirmation_continuation,
    )

    raw = yaml.safe_load(arguments.answers.read_text(encoding="utf-8"))
    continuation = apply_confirmation_records(
        load_confirmation_parent(arguments.run_dir),
        parse_answers(raw),
    )
    child = persist_confirmation_continuation(
        continuation,
        run_root=arguments.run_root,
    )
    payload = {
        "status": continuation.decision.state,
        "run_dir": str(child),
        "parent_run": str(continuation.parent.run_dir),
        "confirmation_record_hashes": list(
            continuation.confirmation_record_hashes
        ),
        "design_sha256": (
            continuation.design.design_sha256
            if continuation.design is not None
            else None
        ),
        "questions": [
            item.model_dump(mode="json")
            for item in continuation.decision.questions
        ],
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            f"{continuation.decision.state}: confirmation child at {child}."
        ),
    )
    return 0 if continuation.design is not None else 4


def _native_gateway(
    arguments: argparse.Namespace,
    *,
    qualification: bool = False,
    activity_reporter: ActivityReporter | None = None,
) -> ModelGateway:
    default_model = arguments.model_name or "gpt-5.6-sol"
    registry = load_backend_registry(
        arguments.backend_config,
        default_model=default_model,
    )
    backend_id = arguments.backend
    if qualification and backend_id == "auto":
        raise ValueError(
            "qualification requires an explicit --backend and --model-name"
        )
    if qualification and arguments.model_name is None:
        raise ValueError(
            "qualification requires an explicit --backend and --model-name"
        )
    if backend_id == "auto":
        return ModelGateway(
            registry=registry,
            mode=BackendMode.NORMAL,
            activity_reporter=activity_reporter,
        )

    selected = BackendRegistry()
    for priority, backend in registry.registrations():
        if backend.backend_id != backend_id:
            continue
        if arguments.model_name is not None and (
            backend.model != arguments.model_name
        ):
            continue
        selected.register(backend, priority=priority)
    if not selected.registrations():
        raise ValueError(
            f"backend/model is not configured: {backend_id}/"
            f"{arguments.model_name or '*'}"
        )
    if qualification:
        return ModelGateway(
            registry=selected,
            mode=BackendMode.QUALIFICATION,
            pinned_backend_id=backend_id,
            pinned_model=arguments.model_name,
            activity_reporter=activity_reporter,
        )
    return ModelGateway(
        registry=selected,
        mode=BackendMode.NORMAL,
        activity_reporter=activity_reporter,
    )


def _model(arguments: argparse.Namespace) -> int:
    if arguments.model_command != "doctor":
        raise ValueError("a model subcommand is required")
    registry = load_backend_registry(arguments.backend_config)
    records = doctor_backends(registry)
    payload = {
        "schema_version": 1,
        "status": (
            "PASS"
            if any(item.state == "available" for item in records)
            else "BACKEND_UNAVAILABLE"
        ),
        "backends": [item.model_dump(mode="json") for item in records],
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            "PASS: 至少一个模型后端可用。"
            if payload["status"] == "PASS"
            else "BACKEND_UNAVAILABLE: 没有可用的模型后端。"
        ),
    )
    return 0 if payload["status"] == "PASS" else 3


def _native_validate(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.path)
    payload = {"status": "PASS", "task_id": task.task_id}
    _emit(
        payload,
        as_json=arguments.json,
        human=f"PASS: TaskSpec {task.task_id!r} is valid.",
    )
    return 0


def _load_task_draft(path: Path) -> TaskDraft:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("TaskDraft root must be a mapping")
    return TaskDraft.model_validate(payload)


def _write_yaml_exclusive(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(
            value,
            stream,
            sort_keys=False,
            allow_unicode=True,
        )


def _declared_task_assets(
    request_file: Path,
    paths: list[Path],
    asset_root: Path | None,
    *,
    directory_paths: list[Path] | None = None,
    install_paths: list[Path] | None = None,
) -> list[PublicAsset]:
    root = (asset_root or request_file.parent).resolve()
    result: list[PublicAsset] = []
    for relative in paths:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("--asset must be a safe path relative to --asset-root")
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"declared asset is missing or not a file: {relative}")
        if source.stat().st_size > MAX_TASK_ASSET_BYTES:
            raise ValueError(
                "declared asset exceeds the 256 MiB size limit: "
                f"{relative}"
            )
        digest = sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        result.append(
            PublicAsset(
                path=relative.as_posix(),
                sha256=digest.hexdigest(),
                purpose="user-declared public task asset",
            )
        )
    directories = directory_paths or []
    destinations = install_paths or []
    if len(directories) != len(destinations):
        raise ValueError(
            "--asset-dir and --asset-install-path must be paired one-to-one"
        )
    for relative, install_path in zip(directories, destinations, strict=True):
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                "--asset-dir must be a safe path relative to --asset-root"
            )
        if install_path.is_absolute() or ".." in install_path.parts:
            raise ValueError(
                "--asset-install-path must be a safe case-relative path"
            )
        source = root / relative
        if source.is_symlink() or not source.is_dir():
            raise ValueError(
                f"declared asset directory is missing or unsafe: {relative}"
            )
        members: list[BundleMember] = []
        total_bytes = 0
        for member_path in sorted(source.rglob("*")):
            if member_path.is_symlink():
                raise ValueError(
                    "declared asset directory contains a symlink: "
                    f"{member_path.relative_to(root)}"
                )
            if member_path.is_dir():
                continue
            if not member_path.is_file():
                raise ValueError(
                    "declared asset directory contains a non-regular member: "
                    f"{member_path.relative_to(root)}"
                )
            relative_member = member_path.relative_to(source).as_posix()
            logical_name = (
                relative_member[:-3]
                if relative_member.endswith(".gz")
                else relative_member
            )
            member_bytes = member_path.stat().st_size
            total_bytes += member_bytes
            if total_bytes > MAX_TASK_ASSET_BYTES:
                raise ValueError(
                    "declared asset exceeds the 256 MiB size limit: "
                    f"{relative}"
                )
            digest = sha256()
            with member_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            members.append(
                BundleMember(
                    relative_path=relative_member,
                    logical_name=logical_name,
                    sha256=digest.hexdigest(),
                    bytes=member_bytes,
                )
            )
        if not members:
            raise ValueError(
                f"declared asset directory contains no files: {relative}"
            )
        manifest_sha256 = compute_bundle_manifest_sha256(
            adapter_id="foampilot.asset.openfoam-poly-mesh",
            kind="openfoam_poly_mesh",
            source_path=relative.as_posix(),
            install_path=install_path.as_posix(),
            region=None,
            members=members,
        )
        result.append(
            PublicAsset(
                path=relative.as_posix(),
                sha256=manifest_sha256,
                purpose="user-declared OpenFOAM polyMesh directory asset",
                kind="directory",
                install_path=install_path.as_posix(),
                bundle_manifest_sha256=manifest_sha256,
            )
        )
    return result


def _task_builder(arguments: argparse.Namespace) -> int:
    if arguments.task_command == "draft":
        if arguments.output.exists():
            raise ValueError(f"output already exists: {arguments.output}")
        request = arguments.request_file.read_text(encoding="utf-8")
        assets = _declared_task_assets(
            arguments.request_file,
            arguments.asset,
            arguments.asset_root,
            directory_paths=arguments.asset_dir,
            install_paths=arguments.asset_install_path,
        )
        protected = tuple(
            dict.fromkeys(
                str(path.resolve()) for path in arguments.protected_path
            )
        )
        trace = InMemoryModelTraceSink()
        activity_reporter = _activity_reporter(arguments)
        extraction_started = time.monotonic()
        draft = extract_task_draft(
            request,
            assets,
            _native_gateway(
                arguments,
                activity_reporter=activity_reporter,
            ),
            budget=ModelBudgetLedger.start(
                total_model_deadline_seconds=420,
                lineage_transport_attempt_limit=2,
            ).open_stage(
                ModelStage.TASK_EXTRACTION,
                request_timeout_seconds=180,
                stage_deadline_seconds=390,
                max_transport_attempts=2,
            ),
            trace=trace,
            protected_paths=protected,
        )
        _write_yaml_exclusive(
            arguments.output,
            draft.model_dump(mode="json"),
        )
        performance = build_taskbuilder_performance(
            trace.attempts,
            draft_id=draft.draft_id,
            total_seconds=time.monotonic() - extraction_started,
        )
        performance_path = arguments.output.with_suffix(
            arguments.output.suffix + ".performance.json"
        )
        with performance_path.open("x", encoding="utf-8") as stream:
            json.dump(
                performance.model_dump(mode="json"),
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        passed = draft.status == "confirmed"
        payload = {
            "status": "PASS" if passed else "TASK_REQUEST_INCOMPLETE",
            "draft_status": draft.status.value,
            "draft_id": draft.draft_id,
            "output": str(arguments.output),
            "questions": [
                item.model_dump(mode="json")
                for item in draft.unresolved_questions
            ],
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                f"PASS: 任务草稿已写入 {arguments.output}。"
                if passed
                else f"TASK_REQUEST_INCOMPLETE: 草稿已写入 {arguments.output}，请补充或确认问题。"
            ),
        )
        return 0 if passed else 4

    if arguments.task_command == "validate-draft":
        review = validate_task_draft(_load_task_draft(arguments.path))
        blocking = any(item.severity == "blocking" for item in review.issues)
        status = (
            "PASS"
            if review.can_compile
            else (
                "TASK_REQUEST_INCOMPLETE"
                if blocking
                else "TASK_CONFIRMATION_REQUIRED"
            )
        )
        payload = {
            "status": status,
            "can_compile": review.can_compile,
            "draft_id": review.draft.draft_id,
            "issues": [item.model_dump(mode="json") for item in review.issues],
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                "PASS: TaskDraft 可以编译。"
                if review.can_compile
                else f"{status}: 请先处理草稿问题。"
            ),
        )
        return 0 if review.can_compile else 4

    if arguments.task_command == "compile":
        review = validate_task_draft(_load_task_draft(arguments.path))
        if not review.can_compile:
            payload = {
                "status": "TASK_COMPILATION_FAILED",
                "issues": [
                    item.model_dump(mode="json") for item in review.issues
                ],
            }
            _emit(
                payload,
                as_json=arguments.json,
                human="TASK_COMPILATION_FAILED: 请先解决草稿问题。",
            )
            return 4
        compilation = compile_task_draft(review)
        _write_yaml_exclusive(
            arguments.output,
            compilation.task.model_dump(mode="json"),
        )
        payload = {
            "status": "PASS",
            "task_id": compilation.task.task_id,
            "task_sha256": compilation.task_sha256,
            "output": str(arguments.output),
            "assumptions": [
                item.model_dump(mode="json")
                for item in compilation.assumptions
            ],
            "diagnostics": [
                item.model_dump(mode="json")
                for item in compilation.diagnostics
            ],
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=f"PASS: TaskSpec 已写入 {arguments.output}。",
        )
        return 0
    raise ValueError("a task subcommand is required")


def _native_plan(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.path)
    resolution = _resolve_runtime(arguments)
    activity_reporter = _activity_reporter(arguments)
    run_root = arguments.run_root or (
        arguments.output.parent / ".foampilot-plan-runs"
    )
    outcome = NativeAgent(
        gateway=_native_gateway(
            arguments,
            activity_reporter=activity_reporter,
        ),
        runtime_config=resolution.config,
        runtime_provenance=resolution.provenance,
        artifact_store=ArtifactStore(run_root),
        activity_reporter=activity_reporter,
    ).plan(
        task,
        public_asset_root=arguments.public_asset_root,
        derived_cache=arguments.derived_cache,
    )
    if (
        outcome.summary.workflow_state != "COMPLETED"
        or not (outcome.run_dir / "plan-only.json").is_file()
    ):
        payload = outcome.model_dump(mode="json")
        _emit(
            payload,
            as_json=arguments.json,
            human=f"{outcome.status}: artifacts at {outcome.run_dir}.",
        )
        return _native_outcome_exit_code(outcome)
    source = outcome.run_dir / "execution-plan.json"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(source.read_bytes())
    payload = {
        "status": "PASS",
        "task_id": task.task_id,
        "plan": str(arguments.output),
        "run_dir": str(outcome.run_dir),
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=f"PASS: case bundle written to {arguments.output}.",
    )
    return 0


def _native_solve(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.path)
    resolution = _resolve_runtime(arguments)
    activity_reporter = _activity_reporter(arguments)
    outcome = NativeAgent(
        gateway=(
            None
            if arguments.reuse_verified_plan is not None
            else _native_gateway(
                arguments,
                activity_reporter=activity_reporter,
            )
        ),
        runtime_config=resolution.config,
        runtime_provenance=resolution.provenance,
        artifact_store=ArtifactStore(arguments.run_root),
        activity_reporter=activity_reporter,
    ).solve(
        task,
        public_asset_root=arguments.public_asset_root,
        reuse_verified_plan=arguments.reuse_verified_plan,
        derived_cache=arguments.derived_cache,
    )
    payload = outcome.model_dump(mode="json")
    _emit(
        payload,
        as_json=arguments.json,
        human=f"{outcome.status}: artifacts at {outcome.run_dir}.",
    )
    return _native_outcome_exit_code(outcome)


def _native_outcome_exit_code(outcome) -> int:
    if outcome.summary.workflow_state == "CANCELLED":
        return 130
    if outcome.summary.native_status == "PUBLIC_VALIDATION_PASS":
        return 0
    if (
        outcome.summary.workflow_state == "DEFERRED"
        or outcome.status == "BLOCKED_ENVIRONMENT"
    ):
        return 3
    return 4


def _native_resume(arguments: argparse.Namespace) -> int:
    resolution = _resolve_runtime(arguments)
    activity_reporter = _activity_reporter(arguments)
    outcome = NativeAgent(
        gateway=_native_gateway(
            arguments,
            activity_reporter=activity_reporter,
        ),
        runtime_config=resolution.config,
        runtime_provenance=resolution.provenance,
        artifact_store=ArtifactStore(arguments.run_root),
        activity_reporter=activity_reporter,
    ).resume(arguments.parent_run)
    payload = outcome.model_dump(mode="json")
    _emit(
        payload,
        as_json=arguments.json,
        human=f"{outcome.status}: artifacts at {outcome.run_dir}.",
    )
    return _native_outcome_exit_code(outcome)


def _native_rerun(arguments: argparse.Namespace) -> int:
    resolution = _resolve_runtime(arguments)
    activity_reporter = _activity_reporter(arguments)
    task = load_task_spec(arguments.task) if arguments.task is not None else None
    outcome = NativeAgent(
        gateway=_native_gateway(
            arguments,
            activity_reporter=activity_reporter,
        ),
        runtime_config=resolution.config,
        runtime_provenance=resolution.provenance,
        artifact_store=ArtifactStore(arguments.run_root),
        activity_reporter=activity_reporter,
    ).rerun(
        arguments.parent_run,
        task=task,
        public_asset_root=arguments.public_asset_root,
        change_categories=arguments.change_category,
    )
    payload = outcome.model_dump(mode="json")
    _emit(
        payload,
        as_json=arguments.json,
        human=f"{outcome.status}: rerun artifacts at {outcome.run_dir}.",
    )
    return _native_outcome_exit_code(outcome)


def _native_inspect(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.task)
    plan = ExecutionPlan.model_validate_json(
        arguments.plan.read_text(encoding="utf-8")
    )
    resolution = _resolve_runtime(arguments)
    environment = discover_environment(
        resolution.config,
        arguments.case_dir,
    )
    report = inspect_native_case(
        case_root=arguments.case_dir,
        task=task,
        plan=plan,
        available_executables=environment.available_executable_names,
    )
    payload = {
        "status": "PASS" if report.passed else "STATIC_INSPECTION_FAILED",
        "report": report.model_dump(mode="json"),
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            "PASS: native case inspection passed."
            if report.passed
            else f"STATIC_INSPECTION_FAILED: {len(report.issues)} issue(s)."
        ),
    )
    return 0 if report.passed else 4


def _preflight(arguments: argparse.Namespace) -> int:
    resolution = _resolve_runtime(arguments)
    report = run_preflight(
        resolution.config,
        workspace_root=Path.cwd(),
    )
    ok = preflight_passed(report)
    payload = {
        "status": "PASS" if ok else "BLOCKED_ENVIRONMENT",
        "config": resolution.config.model_dump(mode="json"),
        "provenance": resolution.provenance.model_dump(mode="json"),
        "python_executable": str(report.python_executable),
        "checks": [
            check.model_dump(mode="json") for check in report.checks
        ],
        "environment": (
            report.environment.model_dump(mode="json")
            if report.environment is not None
            else None
        ),
        "sandbox_probe": report.sandbox_probe.model_dump(mode="json"),
        "failure": (
            {
                "code": report.failure_code,
                "message": report.failure_message,
                "recovery": report.failure_recovery,
            }
            if report.failure_code is not None
            else None
        ),
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            "PASS: Foundation OpenFOAM v10 runtime is ready."
            if ok
            else "BLOCKED_ENVIRONMENT: one or more runtime checks failed."
        ),
    )
    return 0 if ok else 3


def _report(arguments: argparse.Namespace) -> int:
    summary = ArtifactStore.read_summary(arguments.run_dir)
    store = ArtifactStore(arguments.run_dir.parent)
    problems = store.verify(arguments.run_dir)
    payload: dict[str, Any] = summary.model_dump(mode="json")
    payload["manifest_issues"] = problems
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            f"{summary.status}: {summary.message} Artifact "
            + ("verified." if not problems else "verification failed.")
        ),
    )
    if problems:
        return 4
    if summary.status in {"PASS", "PUBLIC_VALIDATION_PASS"}:
        return 0
    if (
        summary.workflow_state == "DEFERRED"
        or summary.status == "BLOCKED_ENVIRONMENT"
    ):
        return 3
    return 4


def _desktop_launcher(
    run_dir: Path | None,
    runtime_cli_args: tuple[str, ...],
) -> int:
    try:
        from foampilot.desktop.application import launch
    except ModuleNotFoundError as error:
        if error.name == "PySide6" or (
            error.name is not None and error.name.startswith("PySide6.")
        ):
            raise DesktopDependencyError(
                "PySide6 is not installed"
            ) from error
        raise
    return launch(run_dir, runtime_cli_args)


def _runtime_cli_args(arguments: argparse.Namespace) -> tuple[str, ...]:
    values: list[str] = []
    scalar_options = (
        ("runtime_config", "--runtime-config"),
        ("openfoam_root", "--openfoam-root"),
        ("execution_isolation", "--execution-isolation"),
        ("bubblewrap", "--bubblewrap"),
        ("max_mpi_ranks", "--max-mpi-ranks"),
    )
    for attribute, option in scalar_options:
        value = getattr(arguments, attribute)
        if value is not None:
            values.extend((option, str(value)))
    if arguments.allow_dynamic_code_on_host is True:
        values.append("--allow-dynamic-code-on-host")
    for root in arguments.trusted_readonly_root or ():
        values.extend(("--trusted-readonly-root", str(root)))
    return tuple(values)


def _desktop(arguments: argparse.Namespace) -> int:
    try:
        return _desktop_launcher(
            arguments.open_run,
            _runtime_cli_args(arguments),
        )
    except DesktopDependencyError as error:
        print(
            "DESKTOP_DEPENDENCY_MISSING: "
            "请安装 foampilot[desktop]。"
            f" ({error})",
            file=sys.stderr,
        )
        return 3


def _knowledge_manifest(root: Path) -> Path:
    candidates = (
        root / "knowledge-manifest.json",
        root.parent / "knowledge-manifest.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"knowledge-manifest.json not found at {candidates[0]} "
        f"or {candidates[1]}"
    )


def _knowledge(arguments: argparse.Namespace) -> int:
    if arguments.knowledge_command == "validate":
        entries = load_knowledge_corpus(arguments.root)
        if not entries:
            raise ValueError("knowledge corpus contains no YAML entries")
        manifest = _knowledge_manifest(arguments.root)
        issues = verify_knowledge_manifest(arguments.root, manifest)
        payload = {
            "status": "PASS" if not issues else "FAIL_KNOWLEDGE_VALIDATION",
            "entry_count": len(entries),
            "manifest": str(manifest),
            "issues": issues,
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                f"PASS: validated {len(entries)} knowledge entries."
                if not issues
                else f"FAIL_KNOWLEDGE_VALIDATION: {len(issues)} issue(s)."
            ),
        )
        return 0 if not issues else 4
    if arguments.knowledge_command == "coverage":
        report = build_knowledge_coverage(
            load_knowledge_corpus(arguments.root)
        )
        payload = {
            "status": "PASS",
            **report.model_dump(mode="json"),
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                "PASS: generated knowledge coverage for "
                f"{len(report.families)} solver families."
            ),
        )
        return 0
    if arguments.knowledge_command == "search":
        query = KnowledgeQuery(
            text=arguments.query,
            solver=arguments.solver,
            knowledge_types=tuple(arguments.knowledge_types),
            evaluation_family=arguments.family,
            formal=arguments.formal,
            allowed_development_families=tuple(
                arguments.allowed_development_families
            ),
            limit=arguments.limit,
        )
        matches = select_knowledge(
            load_knowledge_corpus(arguments.root),
            query,
        )
        payload = {
            "status": "PASS",
            "query": query.model_dump(mode="json"),
            "matches": [
                match.model_dump(mode="json") for match in matches
            ],
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=f"PASS: selected {len(matches)} knowledge match(es).",
        )
        return 0
    raise ValueError("a knowledge subcommand is required")


def _skill(arguments: argparse.Namespace) -> int:
    if arguments.skill_command != "validate":
        raise ValueError("a skill subcommand is required")
    scenarios_path = arguments.scenarios
    if scenarios_path is None:
        local_scenarios = arguments.skill_dir / "scenarios.yaml"
        repository_scenarios = (
            arguments.skill_dir.parent / "scenarios.yaml"
        )
        scenarios_path = (
            local_scenarios
            if local_scenarios.is_file()
            else repository_scenarios
        )
    scenarios = load_skill_scenarios(scenarios_path)
    issues = validate_skill(arguments.skill_dir, scenarios)
    payload = {
        "status": "PASS" if not issues else "FAIL_SKILL_VALIDATION",
        "skill_name": arguments.skill_dir.name,
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            f"PASS: Skill {arguments.skill_dir.name!r} is valid."
            if not issues
            else f"FAIL_SKILL_VALIDATION: {len(issues)} issue(s)."
        ),
    )
    return 0 if not issues else 4


def _audit(arguments: argparse.Namespace) -> int:
    if arguments.audit_command == "shock-tube":
        gas_constant = 8314.46261815324 / arguments.molecular_weight
        gamma = arguments.cp / (arguments.cp - gas_constant)
        solution = solve_ideal_gas_riemann(
            left=RiemannState(
                pressure_pa=arguments.left_pressure,
                density_kg_m3=ideal_gas_density(
                    arguments.left_pressure,
                    arguments.left_temperature,
                    arguments.molecular_weight,
                ),
                velocity_m_s=arguments.left_velocity,
            ),
            right=RiemannState(
                pressure_pa=arguments.right_pressure,
                density_kg_m3=ideal_gas_density(
                    arguments.right_pressure,
                    arguments.right_temperature,
                    arguments.molecular_weight,
                ),
                velocity_m_s=arguments.right_velocity,
            ),
            gamma=gamma,
            diaphragm_position_m=arguments.diaphragm_position,
            observation_time_s=arguments.time,
        )
        payload = {
            "status": "PASS",
            "solution": solution.model_dump(mode="json"),
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                "PASS: exact ideal-gas Riemann wave positions calculated."
            ),
        )
        return 0
    if arguments.audit_command == "wall-heat-flux":
        resolution = _resolve_runtime(arguments)
        balance = audit_wall_heat_flux(
            arguments.case_dir,
            openfoam_root=resolution.config.openfoam_root,
            hot_patch=arguments.hot_patch,
            cold_patch=arguments.cold_patch,
        )
        payload = {
            "status": "PASS",
            "balance": balance.model_dump(mode="json"),
        }
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                "PASS: transport-model wall heat-flow balance calculated."
            ),
        )
        return 0
    raise ValueError("an audit subcommand is required")


def _qualify(arguments: argparse.Namespace) -> int:
    runtime_resolution = _resolve_runtime(
        arguments,
        default_isolation="sandbox_required",
    )
    gateway = _native_gateway(arguments, qualification=True)
    if arguments.suite == "official-six":
        if arguments.suite_file is not None:
            raise ValueError(
                "--suite-file is only valid with 'qualify suite'"
            )
        report = run_official_six(
            run_root=arguments.run_root,
            workers=arguments.workers,
            backend_id=arguments.backend,
            model_name=arguments.model_name,
            gateway=gateway,
            runtime_resolution=runtime_resolution,
        )
    elif arguments.suite == "suite":
        if arguments.suite_file is None:
            raise ValueError(
                "--suite-file is required with 'qualify suite'"
            )
        report = run_qualification_suite(
            suite=load_qualification_suite(arguments.suite_file),
            run_root=arguments.run_root,
            workers=arguments.workers,
            backend_id=arguments.backend,
            model_name=arguments.model_name,
            gateway=gateway,
            runtime_resolution=runtime_resolution,
        )
    else:
        raise ValueError("a qualification suite is required")
    payload = report.model_dump(mode="json")
    _emit(
        payload,
        as_json=arguments.json,
        human=(
            f"PASS: {report.protocol_id} qualification passed."
            if all(item.status == "PASS" for item in report.results)
            else (
                f"FAIL_AGENT: one or more {report.protocol_id} "
                "cases failed."
            )
        ),
    )
    return (
        0
        if all(item.status == "PASS" for item in report.results)
        else 4
    )


def _improve(arguments: argparse.Namespace) -> int:
    if arguments.improve_command == "analyze":
        qualification_report = QualificationReport.model_validate_json(
            arguments.qualification_report.read_text(encoding="utf-8")
        )
        candidate = create_learning_candidate(
            run_dir=arguments.run_dir,
            qualification_report=qualification_report,
            candidate_id=arguments.candidate_id,
            generalized_lesson=arguments.lesson,
            proposed_target=ImprovementTarget(arguments.target),
            root_cause=(
                RootCause(arguments.root_cause)
                if arguments.root_cause is not None
                else None
            ),
            official_example=arguments.official_example,
            extracted_principles=arguments.principle,
            leakage_families=arguments.leakage_family,
            development_cases=arguments.development_case,
            regression_cases=arguments.regression_case,
            holdout_cases=arguments.holdout_case,
            promotion_criteria=arguments.criterion,
        )
        destination = write_learning_candidate(
            arguments.output,
            candidate,
        )
        payload = candidate.model_dump(mode="json")
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                f"PASS: learning candidate written to {destination}; "
                "no promotion was performed."
            ),
        )
        return 0
    if arguments.improve_command == "compare":
        candidate = load_learning_candidate(arguments.candidate)
        baseline = QualificationReport.model_validate_json(
            arguments.baseline_report.read_text(encoding="utf-8")
        )
        current = QualificationReport.model_validate_json(
            arguments.current_report.read_text(encoding="utf-8")
        )
        report = compare_promotion(candidate, baseline, current)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with arguments.output.open("x", encoding="utf-8") as stream:
            json.dump(
                report.model_dump(mode="json"),
                stream,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        payload = report.model_dump(mode="json")
        _emit(
            payload,
            as_json=arguments.json,
            human=(
                "ELIGIBLE: all promotion gates passed; explicit approval "
                "is still required."
                if report.eligible
                else "INELIGIBLE: one or more promotion gates failed."
            ),
        )
        return 0 if report.eligible else 4
    raise ValueError("an improve subcommand is required")


def _run_main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a stable process exit code."""

    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        if error.code == 0:
            return 0
        return int(error.code)
    if arguments.command is None:
        parser.print_help()
        return 2
    try:
        handlers = {
            "validate": _native_validate,
            "plan": _native_plan,
            "solve": _native_solve,
            "resume": _native_resume,
            "rerun": _native_rerun,
            "inspect": _native_inspect,
            "preflight": _preflight,
            "desktop": _desktop,
            "model": _model,
            "report": _report,
            "questions": _questions,
            "confirm": _confirm,
            "knowledge": _knowledge,
            "skill": _skill,
            "audit": _audit,
            "qualify": _qualify,
            "improve": _improve,
            "task": _task_builder,
            "worker": _worker,
            "job": _job,
        }
        return handlers[arguments.command](arguments)
    except OperationCancelled:
        as_json = bool(getattr(arguments, "json", False))
        _emit(
            {
                "status": "CANCELLED",
                "code": "USER_CANCELLED",
            },
            as_json=as_json,
            human="CANCELLED: 本机任务已按用户请求取消。",
        )
        return 130
    except GatewayRequestError as error:
        as_json = bool(getattr(arguments, "json", False))
        backend_payload = backend_error_payload_zh(error.failure)
        status_prefix = (
            "TASK_EXTRACTION"
            if arguments.command == "task"
            else "MODEL_REQUEST"
        )
        status = status_prefix + (
            "_DEFERRED" if error.failure.retryable else "_FAILED"
        )
        payload = {
            "status": status,
            **backend_payload,
            "logical_request_id": error.logical_request_id,
            "transport_attempts": error.transport_attempts,
            "backend_switches": error.backend_switches,
            "deadline_reason": error.deadline_reason,
        }
        _emit(
            payload,
            as_json=as_json,
            human=(
                f"{status}: {backend_payload['message']} "
                f"{backend_payload['recovery']}"
            ),
        )
        return 3
    except RuntimeConfigError as error:
        as_json = bool(getattr(arguments, "json", False))
        payload = {
            "status": "BLOCKED_ENVIRONMENT",
            "code": error.code,
            "message": error.message,
            "recovery": error.recovery,
        }
        if error.detail is not None:
            payload["detail"] = error.detail
        _emit(
            payload,
            as_json=as_json,
            human=(
                f"BLOCKED_ENVIRONMENT: {error.message} {error.recovery}"
            ),
        )
        return 3
    except RuntimeExecutionError as error:
        as_json = bool(getattr(arguments, "json", False))
        payload = {
            "status": "BLOCKED_ENVIRONMENT",
            "code": error.code,
            "message": "执行隔离环境不可用。",
            "recovery": (
                "修复 bubblewrap/namespace 后重试；sandbox_preferred "
                "只会对 low-risk case 在首命令前降级。"
            ),
        }
        _emit(
            payload,
            as_json=as_json,
            human=(
                f"BLOCKED_ENVIRONMENT: {payload['message']} "
                f"{payload['recovery']}"
            ),
        )
        return 3
    except (ValueError, OSError, json.JSONDecodeError) as error:
        as_json = bool(getattr(arguments, "json", False))
        _emit(
            {
                "status": "INVALID_INPUT",
                "error": str(error),
            },
            as_json=as_json,
            human=f"INVALID_INPUT: {error}",
        )
        return 2
    except Exception as error:
        as_json = bool(getattr(arguments, "json", False))
        _emit(
            {
                "status": "INTERNAL_ERROR",
                "error": str(error),
            },
            as_json=as_json,
            human=f"INTERNAL_ERROR: {error}",
        )
        return 5


def main(
    argv: list[str] | None = None,
    *,
    activity_reporter: ActivityReporter | None = None,
) -> int:
    """Run one CLI request with an optional worker-owned activity reporter."""

    token = _CLI_ACTIVITY_REPORTER.set(activity_reporter)
    try:
        return _run_main(argv)
    finally:
        _CLI_ACTIVITY_REPORTER.reset(token)


def entrypoint() -> None:
    """Console-script wrapper."""

    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
