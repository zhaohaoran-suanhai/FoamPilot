"""Lean state machine from public task to verified native OpenFOAM run."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel
import yaml

from foampilot.artifacts import (
    ArtifactStore,
    AttemptSummary,
    NativeAgentOutcome,
    NativeAgentStatus,
    RunSummary,
)
from foampilot.environment import (
    EnvironmentSnapshot,
    discover_environment,
)
from foampilot.inspection import inspect_native_case
from foampilot.models import ModelClient, TransportError
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    NativeCommand,
    validate_execution_plan,
)
from foampilot.runtime import (
    PlanRunResult,
    PlanRunner,
    RuntimeConfig,
)
from foampilot.tasks import TaskSpec, stage_public_assets
from foampilot.validation.models import PublicValidationReport
from foampilot.validation.native import validate_native_run

from .context import AgentContext, load_agent_context
from .generation import author_case_bundle, materialize_case
from .repair import (
    RepairDecision,
    failure_fingerprint,
    request_repair,
    should_stop_repair,
    validate_repair_decision,
)


def _json_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _json_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _json_payload(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_declared_files(
    case_root: Path,
    plan: ExecutionPlan,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for generated in plan.files:
        path = case_root / generated.path
        if path.is_file():
            values[generated.path] = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
    return values


def _run_log(run: PlanRunResult) -> str:
    parts: list[str] = []
    for step in run.steps:
        for path in (step.stdout_path, step.stderr_path):
            if path.is_file():
                parts.append(
                    path.read_text(encoding="utf-8", errors="replace")
                )
    return "\n".join(parts)


def _generation_trace(
    case_root: Path,
    plan: ExecutionPlan,
    *,
    attempt: int,
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for generated in plan.files:
        path = case_root / generated.path
        if not path.is_file():
            continue
        payload = path.read_bytes()
        files.append(
            {
                "path": generated.path,
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
            }
        )
    return {
        "schema_version": 2,
        "attempt": attempt,
        "source": (
            "single_model_case_bundle"
            if attempt == 1
            else "evidence_scoped_repair"
        ),
        "files": files,
    }


def _status_for_report(
    report: PublicValidationReport,
) -> NativeAgentStatus:
    if report.passed:
        return "PUBLIC_VALIDATION_PASS"
    if report.failure_layer == "ENVIRONMENT_BLOCKED":
        return "BLOCKED_ENVIRONMENT"
    if report.failure_layer is None:
        return "PUBLIC_VALIDATION_FAILED"
    return report.failure_layer


def _apply_repair(
    plan: ExecutionPlan,
    decision: RepairDecision,
) -> ExecutionPlan:
    files = {item.path: item for item in plan.files}
    added_paths: list[str] = []
    for changed in decision.changed_files:
        if changed.path not in files:
            added_paths.append(changed.path)
        files[changed.path] = changed
    commands = {item.step_id: item for item in plan.commands}
    for changed in decision.changed_commands:
        commands[changed.step_id] = changed
    return plan.model_copy(
        update={
            "files": [files[item.path] for item in plan.files]
            + [files[path] for path in added_paths],
            "commands": [commands[item.step_id] for item in plan.commands],
        }
    )


class NativeAgent:
    """Author once, execute safely, validate independently, and repair once."""

    def __init__(
        self,
        *,
        model: ModelClient,
        runtime_config: RuntimeConfig,
        artifact_store: ArtifactStore,
        environment_snapshot: EnvironmentSnapshot | None = None,
        runner: PlanRunner | Any | None = None,
        knowledge_text: str | None = None,
        skills_text: str | None = None,
    ) -> None:
        self.model = model
        self.runtime_config = runtime_config
        self.artifact_store = artifact_store
        self.environment_snapshot = environment_snapshot
        self.runner = runner
        self.knowledge_text = knowledge_text
        self.skills_text = skills_text

    def _finish(
        self,
        *,
        run_dir: Path,
        task: TaskSpec,
        status: NativeAgentStatus,
        attempts: list[AttemptSummary],
        message: str,
        model_calls: int,
    ) -> NativeAgentOutcome:
        _write_json(
            run_dir / "model-configuration.json",
            {
                "client_type": type(self.model).__name__,
                "model": getattr(self.model, "model", None),
                "case_bundle_calls": 1 if model_calls else 0,
                "repair_calls": max(0, model_calls - 1),
                "total_model_calls": model_calls,
            },
        )
        summary = RunSummary(
            task_id=task.task_id,
            status=status,
            attempts=attempts,
            message=message,
        )
        _write_json(run_dir / "summary.json", summary)
        self.artifact_store.finalize(run_dir)
        return NativeAgentOutcome(
            status=status,
            run_dir=run_dir,
            summary=summary,
        )

    def _environment(self, run_dir: Path) -> EnvironmentSnapshot:
        if self.environment_snapshot is not None:
            return self.environment_snapshot
        return discover_environment(self.runtime_config, run_dir)

    def _context(self, task: TaskSpec) -> AgentContext:
        selected = load_agent_context(task)
        return selected.model_copy(
            update={
                "knowledge_text": (
                    selected.knowledge_text
                    if self.knowledge_text is None
                    else self.knowledge_text
                ),
                "skills_text": (
                    selected.skills_text
                    if self.skills_text is None
                    else self.skills_text
                ),
            }
        )

    def solve(
        self,
        task: TaskSpec,
        *,
        public_asset_root: str | Path | None = None,
    ) -> NativeAgentOutcome:
        run_dir = self.artifact_store.create_run()
        attempts: list[AttemptSummary] = []
        model_calls = 0
        (run_dir / "task.yaml").write_text(
            yaml.safe_dump(
                task.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        try:
            environment = self._environment(run_dir)
        except (OSError, RuntimeError) as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="BLOCKED_ENVIRONMENT",
                attempts=attempts,
                message=f"Environment discovery failed: {error}",
                model_calls=model_calls,
            )
        _write_json(run_dir / "environment.json", environment)
        if (
            environment.distribution != task.openfoam_target.distribution
            or environment.version != task.openfoam_target.version
            or not environment.workspace_writable
        ):
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="BLOCKED_ENVIRONMENT",
                attempts=attempts,
                message="The discovered runtime does not satisfy the task target.",
                model_calls=model_calls,
            )

        try:
            context = self._context(task)
        except (OSError, ValueError) as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="PLAN_INVALID",
                attempts=attempts,
                message=f"Dynamic Agent context is invalid: {error}",
                model_calls=model_calls,
            )
        _write_json(
            run_dir / "agent-context.json",
            {
                "selected_knowledge_ids": context.selected_knowledge_ids,
                "selected_source_hashes": context.selected_source_hashes,
            },
        )

        try:
            model_calls += 1
            plan = author_case_bundle(
                task,
                environment,
                self.model,
                context.knowledge_text,
                context.skills_text,
            )
        except TransportError as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="BLOCKED_ENVIRONMENT",
                attempts=attempts,
                message=f"Model transport is unavailable: {error}",
                model_calls=model_calls,
            )
        except Exception as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="CASE_GENERATION_FAILED",
                attempts=attempts,
                message=f"Case-bundle authoring failed: {error}",
                model_calls=model_calls,
            )
        _write_json(run_dir / "execution-plan.json", plan)

        plan_issues = validate_execution_plan(
            plan,
            task,
            environment.executable_names,
        )
        if plan_issues:
            _write_json(run_dir / "plan-issues.json", plan_issues)
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="PLAN_INVALID",
                attempts=attempts,
                message="The model-authored bundle violates safety policy.",
                model_calls=model_calls,
            )

        active_plan = plan
        fingerprints: list[str] = []
        for attempt_number in range(
            1,
            task.resource_budget.max_attempts + 1,
        ):
            attempt_root = run_dir / f"attempt-{attempt_number:02d}"
            case_root = attempt_root / "case"
            case_root.mkdir(parents=True)
            _write_json(attempt_root / "execution-plan.json", active_plan)

            try:
                if task.public_assets:
                    if public_asset_root is None:
                        raise ValueError(
                            "public_asset_root is required for public assets"
                        )
                    stage_public_assets(task, public_asset_root, case_root)
                materialize_case(active_plan, task, case_root)
            except Exception as error:
                attempts.append(
                    AttemptSummary(
                        attempt=attempt_number,
                        status="CASE_GENERATION_FAILED",
                    )
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=f"Case materialization failed: {error}",
                    model_calls=model_calls,
                )
            _write_json(
                attempt_root / "generation-trace.json",
                _generation_trace(
                    case_root,
                    active_plan,
                    attempt=attempt_number,
                ),
            )

            inspection = inspect_native_case(
                case_root=case_root,
                task=task,
                plan=active_plan,
                available_executables=environment.executable_names,
            )
            _write_json(
                attempt_root / "static-inspection.json",
                inspection,
            )
            if not inspection.passed:
                attempts.append(
                    AttemptSummary(
                        attempt=attempt_number,
                        status="STATIC_INSPECTION_FAILED",
                    )
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="STATIC_INSPECTION_FAILED",
                    attempts=attempts,
                    message="Static safety inspection rejected the case.",
                    model_calls=model_calls,
                )

            runner = self.runner
            if runner is None:
                runner = PlanRunner.from_runtime_config(
                    self.runtime_config,
                    environment.executable_names,
                    workspace_root=run_dir,
                )
            run_result = runner.run(
                case_dir=case_root,
                commands=active_plan.commands,
                budget=task.resource_budget,
            )
            _write_json(attempt_root / "run-result.json", run_result)
            report = validate_native_run(
                task=task,
                run_result=run_result,
                case_root=case_root,
            )
            _write_json(
                attempt_root / "public-validation.json",
                report,
            )
            status = _status_for_report(report)
            if report.passed:
                attempts.append(
                    AttemptSummary(
                        attempt=attempt_number,
                        status=status,
                    )
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message="All evaluator-owned public checks pass.",
                    model_calls=model_calls,
                )

            log_text = _run_log(run_result)
            fingerprint = failure_fingerprint(
                report,
                log_tail=log_text,
            )
            assert isinstance(fingerprint, str)
            fingerprints.append(fingerprint)
            attempt_summary = AttemptSummary(
                attempt=attempt_number,
                status=status,
                failed_step_id=report.failed_step_id,
                failure_fingerprint=fingerprint,
            )
            attempts.append(attempt_summary)

            stop = should_stop_repair(
                fingerprints=fingerprints,
                attempts_used=attempt_number,
                max_attempts=task.resource_budget.max_attempts,
                generated_bytes_changed=True,
                environment_failure=(status == "BLOCKED_ENVIRONMENT"),
            )
            if stop.stop:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair stopped: {stop.reason}.",
                    model_calls=model_calls,
                )

            current_files = _read_declared_files(case_root, active_plan)
            try:
                model_calls += 1
                decision = request_repair(
                    task=task,
                    plan=active_plan,
                    report=report,
                    failed_log=log_text,
                    current_files=current_files,
                    knowledge_text=context.knowledge_text,
                    skills_text=context.skills_text,
                    client=self.model,
                )
                issues = validate_repair_decision(
                    decision,
                    task=task,
                    plan=active_plan,
                    available_executables=environment.executable_names,
                    current_files=current_files,
                )
            except TransportError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="BLOCKED_ENVIRONMENT",
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                )
            except Exception as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair proposal failed: {error}",
                    model_calls=model_calls,
                )
            _write_json(
                attempt_root / "repair-decision.json",
                decision,
            )
            if issues:
                _write_json(
                    attempt_root / "repair-issues.json",
                    issues,
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message="Repair proposal violates safety policy.",
                    model_calls=model_calls,
                )

            changed = bool(
                decision.changed_commands
                or any(
                    current_files.get(item.path) != item.content
                    for item in decision.changed_files
                )
            )
            decision_stop = should_stop_repair(
                fingerprints=fingerprints,
                attempts_used=attempt_number,
                max_attempts=task.resource_budget.max_attempts,
                generated_bytes_changed=changed,
                decision=decision,
            )
            if decision_stop.stop:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair stopped: {decision_stop.reason}.",
                    model_calls=model_calls,
                )

            attempt_summary.changed_files = [
                item.path for item in decision.changed_files
            ]
            active_plan = _apply_repair(active_plan, decision)

        raise AssertionError("attempt loop exhausted without terminal result")
