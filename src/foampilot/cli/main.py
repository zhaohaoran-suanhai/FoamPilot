"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from foampilot.agent import (
    NativeAgent,
    author_case_bundle,
    load_agent_context,
)
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
    CodexOAuthProviderClient,
    JsonlModelTraceSink,
    ModelBudgetLedger,
    ModelGateway,
    ModelStage,
    load_codex_access_token,
)
from foampilot.plans import (
    ExecutionPlan,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.qualification import (
    QualificationReport,
    load_qualification_suite,
    run_official_six,
    run_qualification_suite,
)
from foampilot.runtime import (
    RuntimeConfig,
    preflight_passed,
    run_preflight,
)
from foampilot.routing import route_capability
from foampilot.skills import (
    load_skill_scenarios,
    validate_skill,
)
from foampilot.tasks import load_task_spec


COMMANDS = (
    "validate",
    "plan",
    "solve",
    "resume",
    "inspect",
    "report",
    "preflight",
    "knowledge",
    "skill",
    "audit",
    "qualify",
    "improve",
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
    native_plan.add_argument(
        "--auth",
        type=Path,
        default=Path.home() / ".codex/auth.json",
    )
    native_plan.add_argument("--model-name", default="gpt-5.2-codex")
    native_plan.add_argument("--json", action="store_true")

    native_solve = subparsers.add_parser("solve")
    native_solve.add_argument("path", type=Path)
    native_solve.add_argument("--run-root", required=True, type=Path)
    native_solve.add_argument("--public-asset-root", type=Path)
    native_solve.add_argument(
        "--auth",
        type=Path,
        default=Path.home() / ".codex/auth.json",
    )
    native_solve.add_argument("--model-name", default="gpt-5.2-codex")
    native_solve.add_argument("--max-mpi-ranks", type=int, default=1)
    native_solve.add_argument("--json", action="store_true")

    native_resume = subparsers.add_parser("resume")
    native_resume.add_argument("parent_run", type=Path)
    native_resume.add_argument("--run-root", required=True, type=Path)
    native_resume.add_argument(
        "--auth",
        type=Path,
        default=Path.home() / ".codex/auth.json",
    )
    native_resume.add_argument("--model-name", default="gpt-5.2-codex")
    native_resume.add_argument("--max-mpi-ranks", type=int, default=1)
    native_resume.add_argument("--json", action="store_true")

    native_inspect = subparsers.add_parser("inspect")
    native_inspect.add_argument("task", type=Path)
    native_inspect.add_argument("plan", type=Path)
    native_inspect.add_argument("case_dir", type=Path)
    native_inspect.add_argument("--json", action="store_true")

    report = subparsers.add_parser("report")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--json", action="store_true")

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--json", action="store_true")

    knowledge = subparsers.add_parser("knowledge")
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command")
    knowledge_validate = knowledge_commands.add_parser("validate")
    knowledge_validate.add_argument("root", type=Path)
    knowledge_validate.add_argument("--json", action="store_true")
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
    wall_heat.add_argument(
        "--openfoam-root",
        type=Path,
        default=RuntimeConfig.local_foundation_v10().openfoam_root,
    )
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
    qualify.add_argument("--model-name", default="gpt-5.6-sol")
    qualify.add_argument(
        "--auth",
        type=Path,
        default=Path.home() / ".codex/auth.json",
    )
    qualify.add_argument("--json", action="store_true")
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Return the public CLI parser for documentation and tests."""

    return _parser()


def _emit(payload: object, *, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human)


def _native_gateway(arguments: argparse.Namespace) -> ModelGateway:
    return ModelGateway(
        provider=CodexOAuthProviderClient(
            model=arguments.model_name,
            access_token=load_codex_access_token(arguments.auth),
        )
    )


def _native_validate(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.path)
    payload = {"status": "PASS", "task_id": task.task_id}
    _emit(
        payload,
        as_json=arguments.json,
        human=f"PASS: TaskSpec {task.task_id!r} is valid.",
    )
    return 0


def _native_plan(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.path)
    config = RuntimeConfig.local_foundation_v10().model_copy(
        update={"max_mpi_ranks": task.resource_budget.max_mpi_ranks}
    )
    environment = discover_environment(config, arguments.output.parent)
    gateway = _native_gateway(arguments)
    ledger = ModelBudgetLedger.start()
    trace = JsonlModelTraceSink(
        arguments.output.with_suffix(
            arguments.output.suffix + ".model-attempts.jsonl"
        )
    )
    corpus = load_knowledge_corpus(
        Path(__file__).resolve().parents[1] / "knowledge/openfoam10"
    )
    capability = route_capability(
        task,
        environment,
        corpus,
        gateway=gateway,
        budget=ledger.open_stage(
            ModelStage.ROUTING,
            request_timeout_seconds=60,
            stage_deadline_seconds=60,
            max_transport_attempts=1,
        ),
        trace=trace,
    )
    context = load_agent_context(task, capability)
    plan = author_case_bundle(
        task,
        environment,
        capability,
        gateway,
        context.knowledge_text,
        context.skills_text,
        budget=ledger.open_stage(
            ModelStage.GENERATION,
            stage_deadline_seconds=360,
        ),
        trace=trace,
    )
    plan = normalize_execution_plan(
        plan,
        task,
        environment.executable_names,
    ).plan
    issues = validate_execution_plan(
        plan,
        task,
        environment.executable_names,
    )
    if issues:
        raise ValueError(
            "authored plan is invalid: "
            + ", ".join(item.code for item in issues)
        )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        "status": "PASS",
        "task_id": task.task_id,
        "plan": str(arguments.output),
    }
    _emit(
        payload,
        as_json=arguments.json,
        human=f"PASS: case bundle written to {arguments.output}.",
    )
    return 0


def _native_solve(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.path)
    config = RuntimeConfig.local_foundation_v10().model_copy(
        update={"max_mpi_ranks": arguments.max_mpi_ranks}
    )
    outcome = NativeAgent(
        gateway=_native_gateway(arguments),
        runtime_config=config,
        artifact_store=ArtifactStore(arguments.run_root),
    ).solve(task, public_asset_root=arguments.public_asset_root)
    payload = outcome.model_dump(mode="json")
    _emit(
        payload,
        as_json=arguments.json,
        human=f"{outcome.status}: artifacts at {outcome.run_dir}.",
    )
    return _native_outcome_exit_code(outcome)


def _native_outcome_exit_code(outcome) -> int:
    if outcome.summary.native_status == "PUBLIC_VALIDATION_PASS":
        return 0
    if (
        outcome.summary.workflow_state == "DEFERRED"
        or outcome.status == "BLOCKED_ENVIRONMENT"
    ):
        return 3
    return 4


def _native_resume(arguments: argparse.Namespace) -> int:
    config = RuntimeConfig.local_foundation_v10().model_copy(
        update={"max_mpi_ranks": arguments.max_mpi_ranks}
    )
    outcome = NativeAgent(
        gateway=_native_gateway(arguments),
        runtime_config=config,
        artifact_store=ArtifactStore(arguments.run_root),
    ).resume(arguments.parent_run)
    payload = outcome.model_dump(mode="json")
    _emit(
        payload,
        as_json=arguments.json,
        human=f"{outcome.status}: artifacts at {outcome.run_dir}.",
    )
    return _native_outcome_exit_code(outcome)


def _native_inspect(arguments: argparse.Namespace) -> int:
    task = load_task_spec(arguments.task)
    plan = ExecutionPlan.model_validate_json(
        arguments.plan.read_text(encoding="utf-8")
    )
    config = RuntimeConfig.local_foundation_v10().model_copy(
        update={"max_mpi_ranks": task.resource_budget.max_mpi_ranks}
    )
    environment = discover_environment(config, arguments.case_dir)
    report = inspect_native_case(
        case_root=arguments.case_dir,
        task=task,
        plan=plan,
        available_executables=environment.executable_names,
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
    checks = run_preflight(RuntimeConfig.local_foundation_v10())
    ok = preflight_passed(checks)
    payload = {
        "status": "PASS" if ok else "BLOCKED_ENVIRONMENT",
        "checks": [check.model_dump(mode="json") for check in checks],
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
        balance = audit_wall_heat_flux(
            arguments.case_dir,
            openfoam_root=arguments.openfoam_root,
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
    if arguments.suite == "official-six":
        if arguments.suite_file is not None:
            raise ValueError(
                "--suite-file is only valid with 'qualify suite'"
            )
        report = run_official_six(
            run_root=arguments.run_root,
            workers=arguments.workers,
            model_name=arguments.model_name,
            auth=arguments.auth,
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
            model_name=arguments.model_name,
            auth=arguments.auth,
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


def main(argv: list[str] | None = None) -> int:
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
            "inspect": _native_inspect,
            "preflight": _preflight,
            "report": _report,
            "knowledge": _knowledge,
            "skill": _skill,
            "audit": _audit,
            "qualify": _qualify,
            "improve": _improve,
        }
        return handlers[arguments.command](arguments)
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


def entrypoint() -> None:
    """Console-script wrapper."""

    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
