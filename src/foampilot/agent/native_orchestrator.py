"""Lean state machine from public task to verified native OpenFOAM run."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
import yaml

from foampilot.activity import ActivityReporter, OperationCancelled
from foampilot.artifacts import (
    ArtifactStore,
    AttemptSummary,
    NativeAgentOutcome,
    NativeAgentStatus,
    NativeStatus,
    RunSummary,
    is_successful_native_status,
)
from foampilot.environment import (
    EnvironmentSnapshot,
    discover_environment,
)
from foampilot.evidence import (
    EvidenceExtractorRegistry,
    RunAssessment,
    RunFacts,
    assess_native_run,
    assessment_for_inspection,
)
from foampilot.extensions import (
    CapabilityDescriptor,
    CapabilityRegistry,
    SupportedTarget,
)
from foampilot.extensions.physics import (
    FOUNDATION10_POROUS_EXTENSION_ID,
    canonicalize_foundation10_porous_proposal,
    foundation10_porous_descriptor,
)
from foampilot.inspection import (
    InspectionReport,
    inspect_native_case,
    verify_design_conformance,
)
from foampilot.knowledge import load_knowledge_corpus
from foampilot.manifests import family_contract
from foampilot.models import (
    BackendFailureKind,
    GatewayRequestError,
    JsonlModelTraceSink,
    LineageBudgetExhausted,
    ModelBudgetLedger,
    ModelGateway,
    ModelContextArtifact,
    ModelStage,
)
from foampilot.models.messages_zh import backend_error_payload_zh
from foampilot.plans import (
    ExecutionPlan,
    GeneratedFile,
    compile_execution_plan,
    normalize_execution_plan,
    validate_execution_plan,
)
from foampilot.observations import (
    inject_observation_fragments,
)
from foampilot.performance import (
    DerivedCache,
    PlanReuseError,
    PerformanceReuse,
    build_performance_summary,
    geometry_cache_key,
    load_verified_plan_source,
    mesh_cache_key,
    prepare_repair_reuse,
)
from foampilot.preprocessing import (
    ExecutedMeshFacts,
    GeometryFacts,
    GeometryProbeError,
    InputMeshFacts,
    MeshQualityReport,
    PolyMeshInspectionError,
    mesh_quality_from_run_facts,
    inspect_poly_mesh,
    probe_geometry,
    probe_provided_mesh,
)
from foampilot.routing import (
    CapabilityProfile,
    RoutingError,
    route_capability,
)
from foampilot.simulation import (
    CaseDesign,
    ExtensionDecision,
    FactEvidence,
    ResolvedValue,
    ResolvedRequirements,
    SimulationIntent,
    design_case,
    evaluate_design_risk,
    freeze_case_design,
    interpret_intent,
    resolve_requirements,
)
from foampilot.authoring import (
    AuthorTargetFacts,
    CaseBundle,
    CaseAuthoringError,
    author_case,
)
from foampilot.repair import NumericalRepairEnvelope, NumericalRepairRule
from foampilot.repair import (
    RepairChangeSet,
    RepairDecision,
    RepairPolicy,
    apply_authorized_repair,
    authorize_repair,
)
from foampilot.runtime import (
    ExecutionPolicyDecision,
    PlanRunResult,
    PlanRunner,
    ReusedStepResult,
    RuntimeConfig,
    RuntimeConfigProvenance,
    RuntimeExecutionError,
    RuntimeFieldSource,
    RuntimeCheck,
    SandboxProbe,
    run_preflight,
    scan_execution_risk,
)
from foampilot.runtime.preflight import RuntimePreflightReport
from foampilot.runtime.protection import runtime_protected_paths
from foampilot.tasks import (
    TaskSpec,
    snapshot_public_assets,
    stage_public_assets,
)
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ParentRun,
    ResumeMetadata,
    ResumeCompatibilityError,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowState,
    WorkflowStore,
)
from foampilot.workflow.lineage import (
    ContinuationInput,
    LineageRecord,
    RerunInput,
    build_lineage_record,
    build_resume_fingerprint,
    load_parent_plan,
    load_parent_task,
    prepare_continuation,
    prepare_rerun,
)
from foampilot.workflow.confirmation import (
    ConfirmationResumeInput,
    load_confirmation_resume,
)

from .context import AgentContext, load_agent_context
from .contract_stages import ContractStageError, ContractStagePipeline
from .generation import materialize_case
from .model_policy import (
    AUTHOR_MODEL_POLICY,
    DESIGN_MODEL_POLICY,
    INTENT_MODEL_POLICY,
    NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT,
    NATIVE_MODEL_TOTAL_DEADLINE_SECONDS,
    REPAIR_MODEL_POLICY,
    ROUTING_MODEL_POLICY,
)
from .repair import (
    failure_fingerprint,
    request_repair_proposal,
    should_stop_repair,
)
from .failure import (
    FailureClassificationError,
    classify_native_failure,
)
from .repair_scope import RepairScopeError, build_repair_scope
from .status import (
    AgentDecisionStage,
    AgentStatusError,
    build_agent_status_snapshot,
)


def _python_api_runtime_provenance() -> RuntimeConfigProvenance:
    fields = (
        "schema_version",
        "openfoam.distribution",
        "openfoam.version",
        "openfoam.root",
        "execution.isolation",
        "execution.bubblewrap",
        "execution.max_mpi_ranks",
        "execution.allow_dynamic_code_on_host",
        "execution.trusted_readonly_roots",
    )
    return RuntimeConfigProvenance(
        fields={
            field: RuntimeFieldSource(
                source="python_api",
                locator="NativeAgent(runtime_config=...)",
            )
            for field in fields
        }
    )


def _pending_execution_policy(
    config: RuntimeConfig,
) -> ExecutionPolicyDecision:
    return ExecutionPolicyDecision(
        requested_isolation=config.isolation,
        actual_backend=None,
        allowed=False,
        code="POLICY_PENDING",
        dynamic_code_host_opt_in=config.allow_dynamic_code_on_host,
    )


def _synthetic_preflight(
    environment: EnvironmentSnapshot,
) -> RuntimePreflightReport:
    probe = SandboxProbe(
        status="not_requested",
        ok=None,
        return_code=None,
        detail=(
            "sandbox probe not run because the environment snapshot was "
            "injected by a trusted Python caller"
        ),
    )
    return RuntimePreflightReport(
        ok=True,
        python_executable=Path(sys.executable).resolve(),
        checks=(
            RuntimeCheck(
                name="injected_environment_snapshot",
                ok=True,
                detail=(
                    "trusted Python caller supplied the environment; this "
                    "is not a real installation or sandbox readiness gate"
                ),
                blocking=False,
            ),
        ),
        environment=environment,
        sandbox_probe=probe,
    )


def _execution_environment_failure(
    *,
    code: str,
    detail: str,
    evidence_paths: Sequence[str],
    step_id: str | None = None,
    message: str = "执行隔离环境不可用。",
    recovery: str = (
        "修复 bubblewrap/namespace 后重试；sandbox_preferred 只会对 "
        "low-risk case 在首命令前降级。"
    ),
) -> FailureRecord:
    return FailureRecord(
        domain=FailureDomain.ENVIRONMENT,
        code=code,
        step_id=step_id,
        detail=detail,
        message=message,
        recovery=recovery,
        evidence_paths=list(evidence_paths),
    )


def _execution_budget_failure(
    *,
    evidence_paths: Sequence[str],
    step_id: str | None,
) -> FailureRecord:
    return FailureRecord(
        domain=FailureDomain.WORKFLOW,
        code="EXECUTION_WALL_BUDGET_EXHAUSTED",
        step_id=step_id,
        detail=(
            "the cumulative OpenFOAM execution time across this continuation "
            "lineage reached task.resource_budget.max_wall_seconds"
        ),
        message="累计求解时间预算已用尽。",
        recovery=(
            "检查已有日志；如需增加 max_wall_seconds，请以 rerun-with-changes "
            "启动新的完整求解。"
        ),
        evidence_paths=list(evidence_paths),
    )


def _run_result_seconds(run: PlanRunResult) -> float:
    return sum(
        (
            step.elapsed_seconds
            if step.elapsed_seconds is not None
            else max(
                (step.finished_at - step.started_at).total_seconds(),
                0.0,
            )
        )
        for step in run.steps
    )


def _recorded_execution_seconds(run_dir: Path) -> float:
    total = 0.0
    for path in sorted(run_dir.glob("attempt-*/run-result.json")):
        try:
            total += _run_result_seconds(
                PlanRunResult.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError):
            continue
    return total


def _lineage_logical_requests(run_dir: Path, store_root: Path) -> int:
    del store_root
    total = 0
    continuation_path = run_dir / "continuation.json"
    if continuation_path.is_file():
        try:
            total += int(
                _read_json(continuation_path).get(
                    "logical_requests_used_before_child",
                    0,
                )
            )
        except (OSError, ValueError, TypeError):
            pass
    model_path = run_dir / "model-configuration.json"
    if model_path.is_file():
        try:
            total += int(
                _read_json(model_path).get("logical_model_requests", 0)
            )
        except (OSError, ValueError, TypeError):
            pass
    return total


def _write_status_artifact(
    *,
    run_dir: Path,
    name: str,
    snapshot: BaseModel,
) -> ModelContextArtifact:
    path = run_dir / name
    _write_json(path, snapshot)
    return ModelContextArtifact(
        path=name,
        sha256=sha256(path.read_bytes()).hexdigest(),
    )


def _agent_status_failure(error: AgentStatusError) -> FailureRecord:
    return FailureRecord(
        domain=FailureDomain.WORKFLOW,
        code=error.code,
        detail=error.detail,
        message=error.message,
        recovery=error.recovery,
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


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be a mapping: {path}")
    return payload


def _performance_context(
    run_dir: Path,
) -> tuple[str, PerformanceReuse, list[str]]:
    path = run_dir / "performance-context.json"
    if not path.is_file():
        return "cold", PerformanceReuse(), []
    try:
        payload = _read_json(path)
        path_kind = str(payload.get("path_kind", "cold"))
        reuse_payload = payload.get("reuse", {})
        diagnostics_payload = payload.get("diagnostics", [])
        reuse = PerformanceReuse.model_validate(
            reuse_payload if isinstance(reuse_payload, dict) else {}
        )
        diagnostics = (
            [str(item) for item in diagnostics_payload]
            if isinstance(diagnostics_payload, list)
            else []
        )
        return path_kind, reuse, diagnostics
    except (OSError, ValueError):
        return (
            "cold",
            PerformanceReuse(),
            ["PERFORMANCE_EVIDENCE_INCOMPLETE: invalid performance context"],
        )


def _update_performance_context(
    run_dir: Path,
    *,
    path_kind: str | None = None,
    plan: str | None = None,
    geometry: str | None = None,
    mesh: str | None = None,
    repair_start_stage: str | None = None,
    diagnostic: str | None = None,
) -> None:
    current_kind, current_reuse, diagnostics = _performance_context(run_dir)
    updates: dict[str, object] = {}
    if plan is not None:
        updates["plan"] = plan
    if geometry is not None:
        updates["geometry"] = geometry
    if mesh is not None:
        updates["mesh"] = mesh
    if repair_start_stage is not None:
        updates["repair_start_stage"] = repair_start_stage
    reuse = current_reuse.model_copy(update=updates)
    if diagnostic is not None:
        diagnostics = list(dict.fromkeys([*diagnostics, diagnostic]))
    _write_json(
        run_dir / "performance-context.json",
        {
            "schema_version": 1,
            "path_kind": path_kind or current_kind,
            "reuse": reuse,
            "diagnostics": diagnostics,
        },
    )


_NATIVE_STATUSES: set[str] = {
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "MESH_QUALITY_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "ACCEPTANCE_FAILED",
    "ACCEPTANCE_INCOMPLETE",
    "RUN_COMPLETED",
}


def _production_capability_registry(
    capability: CapabilityProfile,
    task: TaskSpec,
) -> CapabilityRegistry:
    """Freeze deterministic mesh/runner capabilities around the routed solver."""

    if not capability.solver_executable:
        raise ValueError("routed capability has no solver executable")
    token = re.sub(
        r"[^a-z0-9]+",
        "-",
        capability.solver_executable.casefold(),
    ).strip("-")
    if not token:
        raise ValueError("routed solver executable has no stable identity")
    planning = CapabilityRegistry.planning_first_party()
    registry = CapabilityRegistry()
    mesh_strategy = task.mesh.strategy if task.mesh is not None else (
        "provided"
        if capability.mesh_family.casefold() in {"provided", "openfoam_mesh"}
        else "blockMesh"
    )
    if mesh_strategy == "auto":
        mesh_strategy = (
            "provided"
            if capability.mesh_family.casefold()
            in {"provided", "openfoam_mesh"}
            else "blockMesh"
        )
    if mesh_strategy not in {"provided", "blockMesh"}:
        raise ValueError(
            "CAPABILITY_UNAVAILABLE: deterministic mesh planning does not "
            f"support {mesh_strategy!r}"
        )
    mesh_extension_id = (
        "foampilot.mesh.openfoam-provided"
        if mesh_strategy == "provided"
        else "foampilot.mesh.block-mesh"
    )
    runner_extension_id = (
        "foampilot.solver.foundation10-parallel"
        if capability.parallel_expected
        else "foampilot.solver.foundation10-serial"
    )
    for extension_id in (mesh_extension_id, runner_extension_id):
        registry.register(
            planning.descriptor(extension_id),
            planning.provider(extension_id),
        )
    registry.register(
        CapabilityDescriptor(
            extension_id=f"foampilot.bridge.solver.{token}",
            extension_version="1.0.0",
            protocol_version=1,
            capability_kinds=(f"solver:{token}",),
            supported_targets=(
                SupportedTarget(
                    distribution="foundation",
                    versions=(task.openfoam_target.version,),
                ),
            ),
            required_executables=(capability.solver_executable,),
            input_contracts=("foampilot.simulation.CaseDesignProposal:1",),
            output_contracts=("foampilot.simulation.CaseDesign:1",),
        ),
        capability,
    )
    geometry_fact = next(
        (
            fact
            for fact in task.explicit_facts
            if fact.field_path == "geometry.input"
            and fact.confirmed
            and fact.source
            in {"user_text", "user_confirmation", "deterministic_rule"}
        ),
        None,
    )
    porous_roles = (
        tuple(
            item.name
            for item in task.geometry.region_roles
            if item.role == "porous"
        )
        if geometry_fact is not None and task.geometry is not None
        else ()
    )
    inlet_roles = (
        tuple(
            item.name
            for item in task.geometry.patch_roles
            if item.role == "inlet"
        )
        if geometry_fact is not None and task.geometry is not None
        else ()
    )
    if porous_roles:
        if capability.solver_executable != "pisoFoam":
            raise ValueError(
                "CAPABILITY_UNAVAILABLE: the bounded Foundation v10 porous "
                "extension requires pisoFoam"
            )
        if len(porous_roles) != 1 or len(inlet_roles) != 1:
            raise ValueError(
                "CAPABILITY_UNAVAILABLE: porous path requires exactly one "
                "confirmed porous cellZone and one inlet patch"
            )
        descriptor = foundation10_porous_descriptor(
            porous_roles[0], inlet_roles[0]
        )
        registry.register(descriptor, descriptor)
    return registry


def _extension_decision(
    registry: CapabilityRegistry,
    extension_id: str,
    *,
    values: tuple[ResolvedValue, ...] = (),
) -> ExtensionDecision:
    descriptor = registry.descriptor(extension_id)
    return ExtensionDecision(
        extension_id=extension_id,
        schema_version=descriptor.protocol_version,
        values=values,
        provenance=(
            FactEvidence(
                kind="deterministic_capability",
                detail="selected from the task and routed execution contract",
            ),
        ),
    )


def _deterministic_fact(path: str, value: object) -> ResolvedValue:
    return ResolvedValue(
        field_path=path,
        value=value,
        source="deterministic_rule",
        impact="high",
        evidence=(
            FactEvidence(
                kind="deterministic_capability",
                detail="resolved by the trusted task and runner contract",
            ),
        ),
        confirmed=True,
    )


def _complete_planning_extensions(
    proposal,
    *,
    registry: CapabilityRegistry,
    task: TaskSpec,
    capability: CapabilityProfile,
):
    selected = {
        item.extension_id: item.model_copy(
            update={
                "values": tuple(
                    fact
                    for fact in item.values
                    if fact.field_path != "execution.run_solver"
                )
            }
        )
        for item in proposal.extension_decisions
    }
    run_solver_fact = next(
        (
            fact
            for fact in task.explicit_facts
            if fact.field_path == "execution.run_solver" and fact.confirmed
        ),
        None,
    )
    mesh_extension_id = next(
        (
            extension_id
            for extension_id in (
                "foampilot.mesh.openfoam-provided",
                "foampilot.mesh.block-mesh",
            )
            if extension_id in registry.extension_ids()
        ),
        None,
    )
    runner_extension_id = next(
        (
            extension_id
            for extension_id in (
                "foampilot.solver.foundation10-serial",
                "foampilot.solver.foundation10-parallel",
            )
            if extension_id in registry.extension_ids()
        ),
        None,
    )
    if mesh_extension_id is not None:
        mesh_value = (
            "provided"
            if mesh_extension_id.endswith("openfoam-provided")
            else "blockMesh"
        )
        selected[mesh_extension_id] = _extension_decision(
            registry,
            mesh_extension_id,
            values=(_deterministic_fact("mesh.strategy", mesh_value),),
        )
    if runner_extension_id is not None:
        ranks = (
            min(2, task.resource_budget.max_mpi_ranks)
            if capability.parallel_expected
            else 1
        )
        selected[runner_extension_id] = _extension_decision(
            registry,
            runner_extension_id,
            values=(
                _deterministic_fact("execution.mpi_ranks", ranks),
                *((run_solver_fact,) if run_solver_fact is not None else ()),
            ),
        )
    for extension_id in tuple(selected):
        if extension_id.startswith("foampilot.bridge.solver."):
            del selected[extension_id]
    bridge_extension_id = (
        "foampilot.bridge.solver."
        + proposal.solver_family.value.strip().lower()
    )
    if bridge_extension_id in registry.extension_ids():
        selected[bridge_extension_id] = _extension_decision(
            registry,
            bridge_extension_id,
        )
    if (
        FOUNDATION10_POROUS_EXTENSION_ID in registry.extension_ids()
        and FOUNDATION10_POROUS_EXTENSION_ID not in selected
    ):
        selected[FOUNDATION10_POROUS_EXTENSION_ID] = _extension_decision(
            registry,
            FOUNDATION10_POROUS_EXTENSION_ID,
        )
    completed = proposal.model_copy(
        update={
            section: tuple(
                fact
                for fact in getattr(proposal, section)
                if fact.field_path != "execution.run_solver"
            )
            for section in (
                "physical_models",
                "materials",
                "boundary_designs",
                "initial_conditions",
                "time_design",
                "numerical_design",
                "region_models",
            )
        }
        | {
            "extension_decisions": tuple(
                selected[key] for key in sorted(selected)
            )
        }
    )
    porous_roles = (
        tuple(
            item.name
            for item in task.geometry.region_roles
            if item.role == "porous"
        )
        if task.geometry is not None
        else ()
    )
    inlet_roles = (
        tuple(
            item.name
            for item in task.geometry.patch_roles
            if item.role == "inlet"
        )
        if task.geometry is not None
        else ()
    )
    if (
        FOUNDATION10_POROUS_EXTENSION_ID in selected
        and len(porous_roles) == 1
        and len(inlet_roles) == 1
    ):
        completed = canonicalize_foundation10_porous_proposal(
            completed,
            cell_zone=porous_roles[0],
            inlet_patch=inlet_roles[0],
        )
        completed = completed.model_copy(
            update={
                "boundary_designs": tuple(
                    item
                    for item in completed.boundary_designs
                    if item.field_path
                    != f"boundaries.{inlet_roles[0]}.startup_profile"
                )
            }
        )
    return completed


def _default_numerical_repair_envelope(proposal) -> NumericalRepairEnvelope:
    rules: list[NumericalRepairRule] = []
    for fact in proposal.numerical_design:
        if fact.field_path in {"numerics.delta_t", "numerics.deltaT"} and isinstance(
            fact.value, (int, float)
        ):
            current = float(fact.value)
            if current > 0:
                rules.append(
                    NumericalRepairRule(
                        field_path=fact.field_path,
                        operators=("replace", "scale"),
                        direction="decrease",
                        minimum=current / 100.0,
                        maximum=current,
                        authored_paths=("system/controlDict",),
                        dictionary_keyword="deltaT",
                    )
                )
        elif fact.field_path in {"numerics.max_co", "numerics.maxCo"} and isinstance(
            fact.value, (int, float)
        ):
            current = float(fact.value)
            if current > 0:
                rules.append(
                    NumericalRepairRule(
                        field_path=fact.field_path,
                        operators=("replace", "scale"),
                        direction="decrease",
                        minimum=max(current / 10.0, 1.0e-6),
                        maximum=current,
                        authored_paths=("system/controlDict",),
                        dictionary_keyword="maxCo",
                    )
                )
    return NumericalRepairEnvelope(rules=tuple(rules))


def _author_target_facts(
    *,
    task: TaskSpec,
    design: CaseDesign,
    capability: CapabilityProfile,
    extensions: CapabilityRegistry,
) -> AuthorTargetFacts:
    mesh_value = next(
        (
            item.value
            for item in design.proposal.iter_values()
            if item.field_path == "mesh.strategy"
        ),
        "provided" if capability.mesh_family == "provided" else "blockMesh",
    )
    required = [
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
    ]
    contract = family_contract(str(design.proposal.solver_family.value))
    if contract is not None:
        required.extend(contract.required_files)
    if any(
        item.field_path.startswith("materials.")
        for item in design.proposal.iter_values()
    ):
        required.append("constant/physicalProperties")
    if str(mesh_value) == "blockMesh":
        required.append("system/blockMeshDict")
    ranks = next(
        (
            item.value
            for item in design.proposal.iter_values()
            if item.field_path == "execution.mpi_ranks"
        ),
        1,
    )
    if int(ranks) > 1:
        required.append("system/decomposeParDict")
    authoring_rules: list[str] = []
    for extension_id in sorted(design.extension_identities):
        descriptor = extensions.descriptor(extension_id)
        required.extend(descriptor.required_authored_paths)
        authoring_rules.extend(descriptor.authoring_rules)
    return AuthorTargetFacts(
        distribution=task.openfoam_target.distribution,
        version=task.openfoam_target.version,
        solver_executable=str(design.proposal.solver_family.value),
        required_outputs=tuple(task.required_outputs),
        required_authored_paths=tuple(dict.fromkeys(required)),
        extension_authoring_rules=tuple(dict.fromkeys(authoring_rules)),
        public_asset_install_paths=tuple(
            item.install_path if item.kind == "directory" else item.path
            for item in task.public_assets
            if (item.install_path if item.kind == "directory" else item.path)
            is not None
        ),
        protected_paths=tuple(task.protected_paths),
    )


def _repair_policy(task: TaskSpec) -> RepairPolicy:
    return RepairPolicy(
        automatic_numerical_repair=(
            task.repair_policy.automatic_numerical_repair
        ),
        model_diagnostic=task.repair_policy.model_diagnostic,
    )


def _automatic_repair_eligible(
    classification,
    design: CaseDesign,
) -> tuple[bool, str]:
    if not design.numerical_repair_envelope.rules:
        return False, "frozen numerical repair envelope is empty"
    if (
        classification.code != "numerical_instability"
        or classification.domain
        not in {FailureDomain.SOLVER, FailureDomain.VALIDATION}
    ):
        return False, (
            "automatic repair is limited to solver-derived numerical "
            "stability evidence; "
            f"observed domain is {classification.domain.value}"
        )
    if any(
        operation not in {"replace_file"}
        for operation in classification.allowed_operations
    ):
        return False, "failure requires a capability or command-plan change"
    return True, "eligible numerical solver repair"


def _bundle_from_plan(plan: ExecutionPlan) -> CaseBundle:
    return CaseBundle(manifest=plan.manifest, files=plan.files)


def _failure_record(
    *,
    status: str,
    message: str,
    attempts: list[AttemptSummary],
) -> FailureRecord | None:
    if is_successful_native_status(status) or status == "PLAN_READY":
        return None
    domains = {
        "REQUEST_INCOMPLETE": FailureDomain.TASK,
        "INFORMATION_REQUIRED": FailureDomain.DESIGN,
        "CONFIRMATION_REQUIRED": FailureDomain.DESIGN,
        "CAPABILITY_UNAVAILABLE": FailureDomain.DESIGN,
        "ROUTING_UNRESOLVED": FailureDomain.TASK,
        "BLOCKED_ENVIRONMENT": FailureDomain.ENVIRONMENT,
        "PLAN_INVALID": FailureDomain.PLAN,
        "GENERATION_INVALID": FailureDomain.PLAN,
        "PLAN_REUSE_REJECTED": FailureDomain.PLAN,
        "CASE_GENERATION_FAILED": FailureDomain.CASE,
        "STATIC_INSPECTION_FAILED": FailureDomain.INSPECTION,
        "MESH_FAILED": FailureDomain.MESH,
        "MESH_QUALITY_FAILED": FailureDomain.MESH,
        "INITIALIZATION_FAILED": FailureDomain.INITIALIZATION,
        "SOLVER_FAILED": FailureDomain.SOLVER,
        "POSTPROCESS_FAILED": FailureDomain.POSTPROCESS,
        "ACCEPTANCE_FAILED": FailureDomain.VALIDATION,
        "ACCEPTANCE_INCOMPLETE": FailureDomain.VALIDATION,
    }
    return FailureRecord(
        domain=domains.get(status, FailureDomain.LEGACY),
        code=status,
        step_id=(
            attempts[-1].failed_step_id if attempts else None
        ),
        detail=message,
    )


def _backend_blocker(error: GatewayRequestError) -> FailureRecord:
    payload = backend_error_payload_zh(error.failure)
    return FailureRecord(
        domain=FailureDomain.BACKEND,
        code=error.failure.kind.value,
        retryable=error.failure.retryable,
        detail=error.failure.detail,
        message=str(payload["message"]),
        recovery=str(payload["recovery"]),
    )


def _record_event(
    workflow: WorkflowStore,
    *,
    stage: WorkflowStage,
    state: WorkflowEventState,
    attempt: int | None = None,
    step_id: str | None = None,
    detail: str = "",
    evidence_paths: list[str] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    workflow.record(
        WorkflowEvent(
            sequence=workflow.next_sequence,
            stage=stage,
            state=state,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            attempt=attempt,
            step_id=step_id,
            detail=detail,
            evidence_paths=evidence_paths or [],
        )
    )


def _last_completed_stage(workflow: WorkflowStore) -> str | None:
    if not workflow.events_path.is_file():
        return None
    result: str | None = None
    for line in workflow.events_path.read_text(
        encoding="utf-8"
    ).splitlines():
        event = WorkflowEvent.model_validate_json(line)
        if event.state == WorkflowEventState.COMPLETED:
            result = event.stage.value
    return result


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


def _status_for_assessment(
    report: RunAssessment,
) -> NativeAgentStatus:
    if report.ok:
        return "RUN_COMPLETED"
    if report.failure_layer == "ENVIRONMENT_BLOCKED":
        return "BLOCKED_ENVIRONMENT"
    if report.failure_layer is None:
        return "SOLVER_FAILED"
    return report.failure_layer


def _inspection_validation_report(
    inspection: InspectionReport,
) -> RunAssessment:
    issue = inspection.issues[0]
    return assessment_for_inspection(issue.code)


class NativeAgent:
    """Author once, execute safely, validate independently, and repair once."""

    def __init__(
        self,
        *,
        gateway: ModelGateway | None,
        runtime_config: RuntimeConfig,
        artifact_store: ArtifactStore,
        runtime_provenance: RuntimeConfigProvenance | None = None,
        protected_runtime_roots: Sequence[Path] = (),
        environment_snapshot: EnvironmentSnapshot | None = None,
        runner: PlanRunner | Any | None = None,
        knowledge_text: str | None = None,
        skills_text: str | None = None,
        workflow_event_listener: Any | None = None,
        activity_reporter: ActivityReporter | None = None,
    ) -> None:
        self.gateway = gateway
        self.runtime_config = runtime_config
        self.runtime_provenance = (
            runtime_provenance or _python_api_runtime_provenance()
        )
        resolved_protected_roots: list[Path] = []
        for root in protected_runtime_roots:
            path = Path(root)
            if not path.is_absolute():
                raise ValueError("protected runtime roots must be absolute")
            resolved_protected_roots.append(path.resolve())
        self.protected_runtime_roots = tuple(
            dict.fromkeys(resolved_protected_roots)
        )
        self.artifact_store = artifact_store
        self.environment_snapshot = environment_snapshot
        self.runner = runner
        self.knowledge_text = knowledge_text
        self.skills_text = skills_text
        self.workflow_event_listener = workflow_event_listener
        self.activity_reporter = activity_reporter

    def _finish(
        self,
        *,
        run_dir: Path,
        task: TaskSpec,
        status: str,
        attempts: list[AttemptSummary],
        message: str,
        model_calls: int,
        workflow_state: WorkflowState | None = None,
        primary_failure: FailureRecord | None = None,
        terminal_blocker: FailureRecord | None = None,
        resume: ResumeMetadata | None = None,
    ) -> NativeAgentOutcome:
        workflow = WorkflowStore(
            run_dir=run_dir,
            event_listener=self.workflow_event_listener,
        )
        active_workflow_state = workflow_state or (
            WorkflowState.COMPLETED
            if is_successful_native_status(status)
            else WorkflowState.FAILED
        )
        if primary_failure is None and (
            active_workflow_state
            not in {WorkflowState.DEFERRED, WorkflowState.CANCELLED}
            or status in _NATIVE_STATUSES
        ):
            primary_failure = _failure_record(
                status=status,
                message=message,
                attempts=attempts,
            )
        native_status: NativeStatus | None = (
            status if status in _NATIVE_STATUSES else None
        )
        last_completed_stage = _last_completed_stage(workflow)
        final_event_state = {
            WorkflowState.COMPLETED: WorkflowEventState.COMPLETED,
            WorkflowState.FAILED: WorkflowEventState.FAILED,
            WorkflowState.DEFERRED: WorkflowEventState.DEFERRED,
            WorkflowState.CANCELLED: WorkflowEventState.CANCELLED,
        }[active_workflow_state]
        _record_event(
            workflow,
            stage=WorkflowStage.RUN_FINALIZED,
            state=final_event_state,
            detail=message,
        )
        if (
            active_workflow_state
            in {WorkflowState.FAILED, WorkflowState.DEFERRED}
            and not (run_dir / "failure-report.json").exists()
        ):
            classifications = sorted(
                run_dir.glob("failure-classification-attempt-*.json")
            )
            run_fact_paths = sorted(
                run_dir.glob("attempt-*/run-facts.json")
            )
            if classifications and run_fact_paths:
                from foampilot.agent.failure import (
                    NativeFailureClassification,
                )
                from foampilot.reporting import build_failure_report

                classification = NativeFailureClassification.model_validate_json(
                    classifications[-1].read_text(encoding="utf-8")
                )
                run_facts = RunFacts.model_validate_json(
                    run_fact_paths[-1].read_text(encoding="utf-8")
                )
                terminal_code = (
                    terminal_blocker.code
                    if terminal_blocker is not None
                    else "TERMINAL_FAILURE"
                )
                reason_codes = (terminal_code,)
                if (
                    classification.code == "numerical_instability"
                    and not task.repair_policy.automatic_numerical_repair
                ):
                    reason_codes = (
                        "AUTOMATIC_NUMERICAL_REPAIR_DISABLED",
                    )
                decision = RepairDecision(
                    state="FINALIZE_FAILED",
                    reason_codes=reason_codes,
                )
                completed_progress = tuple(
                    dict.fromkeys(
                        event.stage.value
                        for event in (
                            WorkflowEvent.model_validate_json(line)
                            for line in workflow.events_path.read_text(
                                encoding="utf-8"
                            ).splitlines()
                            if line.strip()
                        )
                        if event.state == WorkflowEventState.COMPLETED
                    )
                )
                preserved = tuple(
                    sorted(
                        path.relative_to(run_dir).as_posix()
                        for path in run_dir.rglob("*")
                        if path.is_file()
                        and not path.is_symlink()
                        and (
                            path.name
                            in {
                                "run-facts.json",
                                "run-assessment.json",
                                "result-report.json",
                                "static-inspection.json",
                            }
                            or path.name.startswith(
                                "failure-classification-attempt-"
                            )
                            or ".foampilot/logs" in path.as_posix()
                        )
                    )
                )
                failure_report = build_failure_report(
                    run_facts,
                    classification,
                    decision,
                    progress=completed_progress,
                    artifacts=preserved,
                )
                _write_json(run_dir / "failure-report.json", failure_report)
        if self.activity_reporter is not None:
            self.activity_reporter.emit(
                kind="stage",
                state=(
                    "completed"
                    if active_workflow_state == WorkflowState.COMPLETED
                    else (
                        "cancelled"
                        if active_workflow_state == WorkflowState.CANCELLED
                        else "failed"
                    )
                ),
                source="workflow",
                stage="solve",
                detail_code=(
                    None
                    if active_workflow_state == WorkflowState.COMPLETED
                    else active_workflow_state.value
                ),
                message="FoamPilot run finalized",
            )
            _write_json(
                run_dir / "observability.json",
                {
                    "schema_version": 1,
                    "state": (
                        "degraded"
                        if self.activity_reporter.degraded
                        else "ok"
                    ),
                    "diagnostics": list(
                        self.activity_reporter.degradation_messages
                    ),
                },
            )
        if final_event_state == WorkflowEventState.COMPLETED:
            last_completed_stage = WorkflowStage.RUN_FINALIZED.value
        trace_path = run_dir / "model-attempts.jsonl"
        traces: list[dict[str, object]] = []
        if trace_path.is_file():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    traces.append(payload)
        generation_calls = 0
        repair_calls = 0
        if workflow.events_path.is_file():
            for line in workflow.events_path.read_text(
                encoding="utf-8"
            ).splitlines():
                event = WorkflowEvent.model_validate_json(line)
                if event.state != WorkflowEventState.STARTED:
                    continue
                if event.stage == WorkflowStage.MODEL_GENERATION_STARTED:
                    generation_calls += 1
                elif event.stage == WorkflowStage.MODEL_REPAIR_STARTED:
                    repair_calls += 1
        _write_json(
            run_dir / "model-configuration.json",
            {
                "schema_version": 3,
                "client_type": (
                    type(self.gateway).__name__
                    if self.gateway is not None
                    else None
                ),
                "backend_id": getattr(
                    self.gateway,
                    "primary_backend_id",
                    None,
                ),
                "model": getattr(self.gateway, "primary_model", None),
                "backend_policy_sha256": getattr(
                    self.gateway,
                    "policy_sha256",
                    None,
                ),
                "automatic_failover": bool(
                    getattr(self.gateway, "automatic_failover", False)
                ),
                "backend_ids_used": sorted(
                    {
                        str(item["backend_id"])
                        for item in traces
                        if item.get("backend_id") is not None
                    }
                ),
                "case_bundle_calls": generation_calls,
                "repair_calls": repair_calls,
                "total_model_calls": model_calls,
                "logical_model_requests": model_calls,
                "transport_attempts": len(traces),
                "model_time_seconds": sum(
                    float(item.get("elapsed_seconds", 0))
                    for item in traces
                ),
            },
        )
        parent_run: ParentRun | None = None
        continuation_path = run_dir / "continuation.json"
        if continuation_path.is_file():
            continuation_payload = _read_json(continuation_path)
            raw_parent = continuation_payload.get("parent_run")
            if isinstance(raw_parent, dict):
                parent_run = ParentRun.model_validate(raw_parent)
        summary = RunSummary(
            task_id=task.task_id,
            workflow_state=active_workflow_state,
            native_status=native_status,
            last_completed_stage=last_completed_stage,
            attempts=attempts,
            primary_failure=primary_failure,
            terminal_blocker=terminal_blocker,
            resume=resume
            or ResumeMetadata(
                allowed=False,
                reason="run has no retryable interrupted model stage",
            ),
            parent_run=parent_run,
            message=message,
        )
        workflow.finish(summary)
        path_kind, performance_reuse, performance_diagnostics = (
            _performance_context(run_dir)
        )
        performance = build_performance_summary(
            run_dir,
            path_kind=path_kind,
            reuse=performance_reuse,
        )
        if performance_diagnostics:
            performance = performance.model_copy(
                update={
                    "diagnostics": list(
                        dict.fromkeys(
                            [
                                *performance.diagnostics,
                                *performance_diagnostics,
                            ]
                        )
                    )
                }
            )
        _write_json(
            run_dir / "performance-summary.json",
            performance,
        )
        self.artifact_store.finalize(run_dir)
        return NativeAgentOutcome(
            run_dir=run_dir,
            summary=summary,
        )

    def _finish_cancelled(
        self,
        *,
        run_dir: Path,
        task: TaskSpec,
        attempts: list[AttemptSummary],
        model_calls: int,
        stage: str,
    ) -> NativeAgentOutcome:
        _write_json(
            run_dir / "cancellation.json",
            {
                "schema_version": 1,
                "code": "USER_CANCELLED",
                "stage": stage,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return self._finish(
            run_dir=run_dir,
            task=task,
            status="CANCELLED",
            attempts=attempts,
            message="The local job was cancelled by the user.",
            model_calls=model_calls,
            workflow_state=WorkflowState.CANCELLED,
            resume=ResumeMetadata(
                allowed=False,
                reason="cancelled runs require an explicit rerun",
            ),
        )

    def _environment(self, run_dir: Path) -> EnvironmentSnapshot:
        if self.environment_snapshot is not None:
            return self.environment_snapshot
        return discover_environment(self.runtime_config, run_dir)

    def _context(
        self,
        task: TaskSpec,
        capability: CapabilityProfile,
        *,
        repair: bool = False,
        repair_evidence: str = "",
        geometry_facts: GeometryFacts | None = None,
    ) -> AgentContext:
        selected = load_agent_context(
            task,
            capability,
            repair=repair,
            repair_evidence=repair_evidence,
            geometry_facts=geometry_facts,
        )
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

    @staticmethod
    def _parent_capability(parent_run: Path) -> CapabilityProfile:
        path = parent_run / "capability-profile.json"
        if not path.is_file():
            raise ResumeCompatibilityError("capability_profile")
        try:
            return CapabilityProfile.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise ResumeCompatibilityError(
                "capability_profile"
            ) from error

    def resume(
        self,
        parent_run: str | Path,
        *,
        public_asset_root: str | Path | None = None,
    ) -> NativeAgentOutcome:
        """Create an immutable child run from a retryable model stage."""

        if self.gateway is None:
            raise ValueError("strict resume requires a model gateway")

        parent = Path(parent_run).resolve()
        confirmation_resume: ConfirmationResumeInput | None = None
        lineage_path = parent / "lineage.json"
        if lineage_path.is_file():
            try:
                lineage = LineageRecord.model_validate_json(
                    lineage_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                lineage = None
            if lineage is not None and lineage.relation == "design_confirmation":
                confirmation_resume = load_confirmation_resume(parent)
        task = (
            confirmation_resume.task
            if confirmation_resume is not None
            else load_parent_task(parent)
        )
        environment = self._environment(self.artifact_store.root)
        capability = (
            None
            if confirmation_resume is not None
            else self._parent_capability(parent)
        )
        effective_asset_root = public_asset_root
        parent_assets = parent / "public-assets"
        if effective_asset_root is None and parent_assets.is_dir():
            effective_asset_root = parent_assets
        geometry_facts = (
            None
            if task.geometry is not None
            and task.geometry.mode == "openfoam_mesh"
            else probe_geometry(
                task,
                Path(effective_asset_root or parent),
            )
        )
        if confirmation_resume is not None:
            return self.solve(
                task,
                public_asset_root=effective_asset_root,
                _confirmation_resume=confirmation_resume,
            )
        assert capability is not None
        context = self._context(task, capability, geometry_facts=geometry_facts)
        acceptance_path = parent / "acceptance-plan.json"
        observation_path = parent / "observation-plan.json"
        current = build_resume_fingerprint(
            task=task,
            environment=environment,
            runtime_config=self.runtime_config,
            model=self.gateway.primary_model,
            backend_id=self.gateway.primary_backend_id,
            backend_policy_sha256=self.gateway.policy_sha256,
            knowledge_ids=context.selected_knowledge_ids,
            knowledge_text=context.knowledge_text,
            skill_ids=context.skill_names,
            skills_text=context.skills_text,
            public_asset_root=effective_asset_root,
            acceptance_plan_sha256=(
                sha256(acceptance_path.read_bytes()).hexdigest()
                if acceptance_path.is_file()
                else "0" * 64
            ),
            observation_plan_sha256=(
                sha256(observation_path.read_bytes()).hexdigest()
                if observation_path.is_file()
                else "0" * 64
            ),
        )
        continuation = prepare_continuation(
            parent_run=parent,
            artifact_store=self.artifact_store,
            current=current,
        )
        return self.solve(
            task,
            public_asset_root=effective_asset_root,
            _continuation=continuation,
        )

    def rerun(
        self,
        parent_run: str | Path,
        *,
        task: TaskSpec | None = None,
        public_asset_root: str | Path | None = None,
        change_categories: list[str] | tuple[str, ...] = (),
    ) -> NativeAgentOutcome:
        """Start a complete new solve with explicit immutable lineage."""

        parent = Path(parent_run).resolve()
        rerun = prepare_rerun(
            parent,
            declared_change_categories=change_categories,
        )
        selected_task = task or load_parent_task(parent)
        effective_asset_root = public_asset_root
        parent_assets = parent / "public-assets"
        if effective_asset_root is None and parent_assets.is_dir():
            effective_asset_root = parent_assets
        return self.solve(
            selected_task,
            public_asset_root=effective_asset_root,
            _rerun=rerun,
        )

    def plan(
        self,
        task: TaskSpec,
        *,
        public_asset_root: str | Path | None = None,
        derived_cache: str | Path | None = None,
    ) -> NativeAgentOutcome:
        """Compile the canonical plan and stop before case materialization."""

        return self.solve(
            task,
            public_asset_root=public_asset_root,
            derived_cache=derived_cache,
            _plan_only=True,
        )

    def solve(
        self,
        task: TaskSpec,
        *,
        public_asset_root: str | Path | None = None,
        reuse_verified_plan: str | Path | None = None,
        derived_cache: str | Path | None = None,
        _continuation: ContinuationInput | None = None,
        _confirmation_resume: ConfirmationResumeInput | None = None,
        _rerun: RerunInput | None = None,
        _plan_only: bool = False,
    ) -> NativeAgentOutcome:
        if _plan_only and (
            _continuation is not None
            or _confirmation_resume is not None
            or _rerun is not None
            or reuse_verified_plan is not None
        ):
            raise ValueError("plan-only mode does not accept lineage inputs")
        selected_lineage_inputs = sum(
            item is not None
            for item in (
                _continuation,
                _confirmation_resume,
                _rerun,
                reuse_verified_plan,
            )
        )
        if selected_lineage_inputs > 1:
            raise ValueError(
                "verified plan reuse, strict resume, and rerun are mutually exclusive"
            )
        run_dir = self.artifact_store.create_run()
        activity_reporter = self.activity_reporter or ActivityReporter(
            operation_id=uuid4().hex
        )
        activity_reporter.bind_run(
            run_dir.name,
            run_dir / "activity-events.jsonl",
        )
        self.activity_reporter = activity_reporter
        if isinstance(self.gateway, ModelGateway):
            self.gateway.activity_reporter = activity_reporter
        activity_reporter.emit(
            kind="stage",
            state="started",
            source="workflow",
            stage="solve",
            message="FoamPilot run started",
        )
        _write_json(run_dir / "runtime-config.json", self.runtime_config)
        _write_json(
            run_dir / "runtime-config-provenance.json",
            self.runtime_provenance,
        )
        _write_json(
            run_dir / "execution-policy.json",
            _pending_execution_policy(self.runtime_config),
        )
        derived_cache_store = (
            DerivedCache(derived_cache)
            if derived_cache is not None
            else None
        )
        _write_json(
            run_dir / "performance-context.json",
            {
                "schema_version": 1,
                "path_kind": (
                    "warm_plan"
                    if reuse_verified_plan is not None
                    else "cold"
                ),
                "reuse": PerformanceReuse(
                    plan=(
                        "miss"
                        if reuse_verified_plan is not None
                        else "disabled"
                    ),
                    geometry=(
                        "miss"
                        if derived_cache_store is not None
                        and task.geometry is not None
                        and task.geometry.mode != "openfoam_mesh"
                        else "disabled"
                    ),
                    mesh=(
                        "miss"
                        if derived_cache_store is not None
                        else "disabled"
                    ),
                ),
                "diagnostics": [],
            },
        )
        workflow = WorkflowStore(
            run_dir=run_dir,
            event_listener=self.workflow_event_listener,
        )
        attempts: list[AttemptSummary] = (
            list(_continuation.parent_summary.attempts)
            if _continuation is not None
            and _continuation.from_stage
            == WorkflowStage.MODEL_REPAIR_STARTED
            else []
        )
        model_calls = 0
        model_ledger = ModelBudgetLedger.start(
            total_model_deadline_seconds=NATIVE_MODEL_TOTAL_DEADLINE_SECONDS,
            lineage_transport_attempt_limit=NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT,
            transport_attempts_used=(
                _continuation.transport_attempts_used
                if _continuation is not None
                else (
                    _confirmation_resume.transport_attempts_used
                    if _confirmation_resume is not None
                    else 0
                )
            ),
        )
        lineage_logical_requests_before_run = (
            _lineage_logical_requests(
                _continuation.parent_run,
                self.artifact_store.root.resolve(),
            )
            if _continuation is not None
            else (
                _confirmation_resume.logical_requests_used_before_child
                if _confirmation_resume is not None
                else 0
            )
        )
        execution_seconds_used = (
            _continuation.execution_seconds_used_before_child
            if _continuation is not None
            else 0.0
        )
        logical_request_limit = task.resource_budget.max_attempts + 4
        model_trace = JsonlModelTraceSink(
            run_dir / "model-attempts.jsonl"
        )
        (run_dir / "task.yaml").write_text(
            yaml.safe_dump(
                task.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        if _continuation is not None:
            reused_evidence_paths: list[str] = []
            if _continuation.active_plan_path is not None:
                evidence_root = run_dir / "continuation-evidence"
                evidence_root.mkdir()
                plan_copy = evidence_root / "execution-plan.json"
                plan_copy.write_bytes(_continuation.active_plan_path.read_bytes())
                reused_evidence_paths.append(
                    "continuation-evidence/execution-plan.json"
                )
                if _continuation.run_assessment_path is not None:
                    validation_copy = evidence_root / "run-assessment.json"
                    validation_copy.write_bytes(
                        _continuation.run_assessment_path.read_bytes()
                    )
                    reused_evidence_paths.append(
                        "continuation-evidence/run-assessment.json"
                    )
                if _continuation.run_facts_path is not None:
                    facts_copy = evidence_root / "run-facts.json"
                    facts_copy.write_bytes(
                        _continuation.run_facts_path.read_bytes()
                    )
                    reused_evidence_paths.append(
                        "continuation-evidence/run-facts.json"
                    )
            _write_json(
                run_dir / "continuation.json",
                {
                    "schema_version": 1,
                    "parent_run": {
                        "run_id": _continuation.parent_run.name,
                        "manifest_sha256": (
                            _continuation.parent_manifest_sha256
                        ),
                    },
                    "from_stage": _continuation.from_stage.value,
                    "continuation_index_for_stage": (
                        _continuation.continuation_index_for_stage
                    ),
                    "continuation_counts": (
                        _continuation.continuation_counts
                    ),
                    "transport_attempts_used_before_child": (
                        _continuation.transport_attempts_used
                    ),
                    "logical_requests_used_before_child": (
                        _continuation.logical_requests_used_before_child
                    ),
                    "execution_seconds_used_before_child": (
                        _continuation.execution_seconds_used_before_child
                    ),
                    "environment_warnings": (
                        _continuation.environment_warnings
                    ),
                },
            )
            _write_json(
                run_dir / "lineage.json",
                LineageRecord(
                    relation="strict_resume",
                    parent_run_id=_continuation.parent_run.name,
                    parent_manifest_sha256=(
                        _continuation.parent_manifest_sha256
                    ),
                    created_at=datetime.now(timezone.utc),
                    input_hash_before=_continuation.input_sha256,
                    input_hash_after=_continuation.input_sha256,
                    change_categories=[],
                    reused_evidence_paths=reused_evidence_paths,
                ),
            )
        elif _confirmation_resume is not None:
            _write_json(
                run_dir / "continuation.json",
                {
                    "schema_version": 1,
                    "parent_run": {
                        "run_id": _confirmation_resume.checkpoint_run.name,
                        "manifest_sha256": (
                            _confirmation_resume.checkpoint_manifest_sha256
                        ),
                    },
                    "from_stage": WorkflowStage.AUTHORING_CASE.value,
                    "transport_attempts_used_before_child": (
                        _confirmation_resume.transport_attempts_used
                    ),
                    "logical_requests_used_before_child": (
                        _confirmation_resume.logical_requests_used_before_child
                    ),
                    "execution_seconds_used_before_child": 0.0,
                },
            )
            _write_json(
                run_dir / "lineage.json",
                LineageRecord(
                    relation="strict_resume",
                    parent_run_id=_confirmation_resume.checkpoint_run.name,
                    parent_manifest_sha256=(
                        _confirmation_resume.checkpoint_manifest_sha256
                    ),
                    created_at=datetime.now(timezone.utc),
                    input_hash_before=_confirmation_resume.design.design_sha256,
                    input_hash_after=_confirmation_resume.design.design_sha256,
                    change_categories=[],
                    reused_evidence_paths=[
                        "simulation-intent.json",
                        "resolved-requirements.json",
                        "case-design-proposal.json",
                        "risk-decision.json",
                        "case-design.json",
                    ],
                    confirmation_record_hashes=list(
                        _read_json(
                            _confirmation_resume.checkpoint_run / "lineage.json"
                        ).get("confirmation_record_hashes", [])
                    ),
                ),
            )
        elif _rerun is not None:
            _write_json(
                run_dir / "lineage.json",
                build_lineage_record(
                    rerun=_rerun,
                    task=task,
                    current_fingerprint=None,
                ),
            )
        _record_event(
            workflow,
            stage=WorkflowStage.TASK_VALIDATED,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["task.yaml"],
        )

        if self.environment_snapshot is not None:
            preflight = _synthetic_preflight(self.environment_snapshot)
        else:
            preflight = run_preflight(
                self.runtime_config,
                workspace_root=run_dir,
            )
        _write_json(run_dir / "preflight.json", preflight)
        _write_json(
            run_dir / "sandbox-probe.json",
            preflight.sandbox_probe,
        )
        if not preflight.ok or preflight.environment is None:
            probe = preflight.sandbox_probe
            code = (
                preflight.failure_code
                or probe.failure_code
                or "OPENFOAM_DISCOVERY_FAILED"
            )
            if (
                self.runtime_config.isolation == "sandbox_required"
                and code in {"BWRAP_UNAVAILABLE", "NAMESPACE_UNAVAILABLE"}
            ):
                code = "SANDBOX_REQUIRED_UNAVAILABLE"
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="BLOCKED_ENVIRONMENT",
                attempts=attempts,
                message="Runtime preflight failed.",
                model_calls=model_calls,
                primary_failure=_execution_environment_failure(
                    code=code,
                    detail=preflight.failure_message or probe.detail,
                    evidence_paths=["preflight.json", "sandbox-probe.json"],
                    message=(
                        preflight.failure_message or "执行隔离环境不可用。"
                    ),
                    recovery=(
                        preflight.failure_recovery
                        or (
                            "修复 bubblewrap/namespace 后重试；"
                            "sandbox_preferred 只会对 low-risk case "
                            "在首命令前降级。"
                        )
                    ),
                ),
            )
        environment = preflight.environment
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
        _record_event(
            workflow,
            stage=WorkflowStage.ENVIRONMENT_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["environment.json"],
        )
        execution_task = task.model_copy(
            update={
                "protected_paths": [
                    str(path)
                    for path in runtime_protected_paths(
                        task.protected_paths,
                        environment,
                        self.protected_runtime_roots,
                    )
                ]
            }
        )
        visible_task = json.dumps(
            execution_task.agent_payload(),
            ensure_ascii=False,
            sort_keys=True,
        )
        leaked_paths = [
            path
            for path in execution_task.protected_paths
            if path in visible_task
        ]
        if leaked_paths:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="CASE_GENERATION_FAILED",
                attempts=attempts,
                message=(
                    "Agent-visible task content references a protected runtime "
                    "path."
                ),
                model_calls=model_calls,
                primary_failure=FailureRecord(
                    domain=FailureDomain.CASE,
                    code="CASE_GENERATION_FAILED",
                    detail=(
                        "PROTECTED_PATH_IN_PUBLIC_TASK: protected runtime paths "
                        "must not enter model-visible task content"
                    ),
                    message="任务文本引用了运行时受保护路径，未调用模型。",
                    recovery="移除 tutorial、私有 evaluator 或其他受保护绝对路径。",
                ),
            )

        effective_public_asset_root: str | Path | None = public_asset_root
        asset_bundles = []
        if task.public_assets:
            if public_asset_root is None:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message="public_asset_root is required for public assets",
                    model_calls=model_calls,
                )
            public_snapshot = run_dir / "public-assets"
            try:
                asset_bundles = snapshot_public_assets(
                    task,
                    public_asset_root,
                    public_snapshot,
                )
            except (OSError, ValueError) as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=f"Public asset staging failed: {error}",
                    model_calls=model_calls,
                )
            effective_public_asset_root = public_snapshot
        _write_json(run_dir / "asset-bundles.json", asset_bundles)

        input_mesh_facts: tuple[InputMeshFacts, ...] = ()
        executed_mesh_facts: tuple[ExecutedMeshFacts, ...] = ()
        if task.mesh is not None and task.mesh.strategy == "provided":
            try:
                mesh_bundles = tuple(
                    bundle
                    for bundle in asset_bundles
                    if bundle.kind == "openfoam_poly_mesh"
                )
                if not mesh_bundles:
                    raise ValueError("provided polyMesh bundle is missing")
                assert effective_public_asset_root is not None
                length_unit = (
                    task.geometry.length_unit
                    if task.geometry is not None
                    else None
                )
                if length_unit is None:
                    raise ValueError("provided mesh length unit is missing")
                input_mesh_facts = tuple(
                    inspect_poly_mesh(
                        Path(effective_public_asset_root) / bundle.source_path,
                        bundle,
                        length_unit=length_unit,
                    )
                    for bundle in mesh_bundles
                )
                _write_json(
                    run_dir / "input-mesh-facts.json",
                    input_mesh_facts,
                )
                executed = []
                for index, bundle in enumerate(mesh_bundles, start=1):
                    probe_case = run_dir / f"pre-authoring-mesh-probe-{index:02d}"
                    stage_public_assets(
                        task.model_copy(
                            update={
                                "public_assets": [
                                    asset
                                    for asset in task.public_assets
                                    if asset.path == bundle.source_path
                                ]
                            }
                        ),
                        effective_public_asset_root,
                        probe_case,
                    )
                    if self.runner is not None and hasattr(
                        self.runner,
                        "probe_provided_mesh",
                    ):
                        fact = self.runner.probe_provided_mesh(
                            case_root=probe_case,
                            environment=environment,
                            runtime_config=self.runtime_config,
                            budget_seconds=min(
                                task.resource_budget.max_wall_seconds,
                                60,
                            ),
                        )
                    else:
                        fact = probe_provided_mesh(
                            probe_case,
                            environment,
                            self.runtime_config,
                            budget_seconds=min(
                                task.resource_budget.max_wall_seconds,
                                60,
                            ),
                        )
                    executed.append(fact)
                executed_mesh_facts = tuple(executed)
                _write_json(
                    run_dir / "pre-authoring-mesh-facts.json",
                    executed_mesh_facts,
                )
                if any(
                    item.mesh_check.mesh_ok is not True
                    for item in executed_mesh_facts
                ):
                    raise ValueError("provided mesh did not pass checkMesh")
            except (OSError, ValueError, PolyMeshInspectionError) as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="MESH_QUALITY_FAILED",
                    attempts=attempts,
                    message=f"Provided mesh preprocessing failed: {error}",
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.MESH,
                        code=getattr(error, "code", "PROVIDED_MESH_INVALID"),
                        detail=str(error),
                        message="提供的原生 polyMesh 未通过生成前确定性检查。",
                    ),
                )

        geometry_facts: GeometryFacts | None = None
        if (
            task.geometry is not None
            and task.geometry.mode != "openfoam_mesh"
        ):
            try:
                geometry_key: str | None = None
                if derived_cache_store is not None:
                    geometry_key = geometry_cache_key(
                        task,
                        Path(effective_public_asset_root or run_dir),
                    )
                    geometry_lookup = derived_cache_store.load_geometry(
                        geometry_key
                    )
                    _write_json(
                        run_dir / "geometry-cache.json",
                        {
                            "schema_version": 1,
                            "status": geometry_lookup.status,
                            "cache_key": geometry_lookup.key,
                            "reason_code": geometry_lookup.reason_code,
                        },
                    )
                    if geometry_lookup.status == "hit":
                        geometry_facts = geometry_lookup.value
                        _update_performance_context(
                            run_dir,
                            geometry="hit",
                        )
                    elif geometry_lookup.reason_code == "DERIVED_CACHE_INVALID":
                        _update_performance_context(
                            run_dir,
                            geometry="miss",
                            diagnostic="DERIVED_CACHE_INVALID: geometry",
                        )
                if geometry_facts is None:
                    geometry_facts = probe_geometry(
                        task,
                        Path(effective_public_asset_root or run_dir),
                    )
                    if (
                        derived_cache_store is not None
                        and geometry_key is not None
                        and geometry_facts is not None
                    ):
                        derived_cache_store.store_geometry(
                            geometry_key,
                            geometry_facts,
                        )
            except GeometryProbeError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=f"公开几何探测失败: {error}",
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.TASK,
                        code=error.code,
                        detail=str(error),
                        message="公开几何输入无法安全、确定地解释。",
                    ),
                )
            assert geometry_facts is not None
            _write_json(run_dir / "geometry-facts.json", geometry_facts)
            _record_event(
                workflow,
                stage=WorkflowStage.GEOMETRY_READY,
                state=WorkflowEventState.COMPLETED,
                evidence_paths=["geometry-facts.json"],
            )

        verified_source = None
        if reuse_verified_plan is not None:
            try:
                verified_source = load_verified_plan_source(
                    reuse_verified_plan,
                    task=task,
                    environment=environment,
                    public_asset_root=effective_public_asset_root,
                )
            except PlanReuseError as error:
                _write_json(
                    run_dir / "plan-reuse.json",
                    {
                        "schema_version": 1,
                        "status": "rejected",
                        "source_run": str(Path(reuse_verified_plan).resolve()),
                        "reason_code": error.reason_code,
                        "detail": error.detail,
                    },
                )
                _update_performance_context(
                    run_dir,
                    path_kind="warm_plan",
                    plan="miss",
                    diagnostic=(
                        f"PLAN_REUSE_REJECTED: {error.reason_code}"
                    ),
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="PLAN_REUSE_REJECTED",
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.PLAN,
                        code="PLAN_REUSE_REJECTED",
                        detail=str(error),
                        message="显式复用的 ExecutionPlan 不满足严格兼容条件。",
                        recovery="请选择匹配的已验证 run，或不带复用参数重新生成。",
                    ),
                )
            _write_json(run_dir / "plan-reuse.json", verified_source.record())
            _update_performance_context(
                run_dir,
                path_kind="warm_plan",
                plan="hit",
            )

        try:
            if verified_source is not None:
                capability = verified_source.capability
            elif _continuation is not None:
                capability = self._parent_capability(
                    _continuation.parent_run
                )
            else:
                if self.gateway is None:
                    raise ValueError(
                        "live authoring requires a model gateway"
                    )
                knowledge_root = (
                    Path(__file__).resolve().parents[1]
                    / "knowledge/openfoam10"
                )
                capability = route_capability(
                    task,
                    environment,
                    load_knowledge_corpus(knowledge_root),
                    gateway=self.gateway,
                    budget=ROUTING_MODEL_POLICY.open(
                        model_ledger,
                        ModelStage.ROUTING,
                    ),
                    trace=model_trace,
                )
        except OperationCancelled:
            return self._finish_cancelled(
                run_dir=run_dir,
                task=task,
                attempts=attempts,
                model_calls=model_calls,
                stage="routing",
            )
        except RoutingError as error:
            if error.model_route_used:
                model_calls += 1
            _write_json(
                run_dir / "capability-profile.json",
                error.profile,
            )
            return self._finish(
                run_dir=run_dir,
                task=task,
                status=error.code,
                attempts=attempts,
                message=str(error),
                model_calls=model_calls,
            )
        except GatewayRequestError as error:
            model_calls += 1
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="ROUTING_UNRESOLVED",
                attempts=attempts,
                message=f"Capability routing is unavailable: {error}",
                model_calls=model_calls,
                workflow_state=WorkflowState.DEFERRED,
                terminal_blocker=_backend_blocker(error),
                resume=ResumeMetadata(
                    allowed=False,
                    reason=(
                        "routing has no frozen capability checkpoint; "
                        "rerun when the backend is available"
                    ),
                ),
            )
        except LineageBudgetExhausted as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="ROUTING_UNRESOLVED",
                attempts=attempts,
                message=str(error),
                model_calls=model_calls,
                primary_failure=FailureRecord(
                    domain=FailureDomain.BACKEND,
                    code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                    detail=str(error),
                ),
            )
        except (OSError, ValueError) as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="ROUTING_UNRESOLVED",
                attempts=attempts,
                message=f"Capability routing is invalid: {error}",
                model_calls=model_calls,
            )
        _write_json(
            run_dir / "capability-profile.json",
            capability,
        )
        _record_event(
            workflow,
            stage=WorkflowStage.ROUTING_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["capability-profile.json"],
        )

        try:
            context = self._context(
                task,
                capability,
                geometry_facts=geometry_facts,
            )
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
                "knowledge_slots": context.knowledge_slots,
                "missing_slots": context.missing_slots,
                "selected_knowledge_ids": context.selected_knowledge_ids,
                "selected_source_hashes": context.selected_source_hashes,
                "skill_names": context.skill_names,
            },
        )
        _record_event(
            workflow,
            stage=WorkflowStage.CONTEXT_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=["agent-context.json"],
        )

        case_design: CaseDesign | None = None
        intent: SimulationIntent | None = None
        selected_registry: CapabilityRegistry | None = None
        if verified_source is None:
            if _confirmation_resume is not None:
                case_design = _confirmation_resume.design
                intent = _confirmation_resume.intent
                for name in (
                    "simulation-intent.json",
                    "resolved-requirements.json",
                    "case-design-proposal.json",
                    "risk-decision.json",
                    "case-design.json",
                    "confirmation-records.json",
                ):
                    source = _confirmation_resume.checkpoint_run / name
                    if source.is_file():
                        (run_dir / name).write_bytes(source.read_bytes())
            elif _continuation is not None:
                parent_design = (
                    _continuation.active_plan_path.parent / "case-design.json"
                    if _continuation.from_stage
                    == WorkflowStage.MODEL_REPAIR_STARTED
                    and _continuation.active_plan_path is not None
                    else _continuation.parent_run / "case-design.json"
                )
                if not parent_design.is_file():
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="CASE_DESIGN_CHECKPOINT_MISSING",
                        attempts=attempts,
                        message=(
                            "Strict continuation has no frozen CaseDesign "
                            "checkpoint."
                        ),
                        model_calls=model_calls,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="CASE_DESIGN_CHECKPOINT_MISSING",
                            detail=(
                                "parent generation/repair continuation does "
                                "not contain case-design.json"
                            ),
                            message="父运行缺少冻结算例设计，不能严格恢复。",
                            recovery="使用 rerun 启动新的完整设计与求解。",
                        ),
                    )
                try:
                    case_design = CaseDesign.model_validate_json(
                        parent_design.read_text(encoding="utf-8")
                    )
                    intent = SimulationIntent.model_validate_json(
                        (_continuation.parent_run / "simulation-intent.json")
                        .read_text(encoding="utf-8")
                    )
                    for name in (
                        "simulation-intent.json",
                        "resolved-requirements.json",
                        "case-design-proposal.json",
                        "risk-decision.json",
                        "case-design.json",
                    ):
                        source = (
                            parent_design
                            if name == "case-design.json"
                            else _continuation.parent_run / name
                        )
                        if not source.is_file():
                            raise ValueError(
                                f"parent design checkpoint is missing {name}"
                            )
                        (run_dir / name).write_bytes(source.read_bytes())
                except (OSError, ValueError) as error:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="CASE_DESIGN_CHECKPOINT_INVALID",
                        attempts=attempts,
                        message=f"Frozen CaseDesign cannot be reused: {error}",
                        model_calls=model_calls,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="CASE_DESIGN_CHECKPOINT_INVALID",
                            detail=str(error),
                            message="父运行的冻结算例设计证据不完整或无效。",
                            recovery="检查父 run manifest，或使用 rerun。",
                        ),
                    )
            else:
                if self.gateway is None:
                    raise ValueError("live design requires a model gateway")
                try:
                    selected_registry = _production_capability_registry(
                        capability,
                        task,
                    )
                    descriptors = tuple(
                        selected_registry.descriptor(extension_id)
                        for extension_id in selected_registry.extension_ids()
                    )
                    _record_event(
                        workflow,
                        stage=WorkflowStage.INTERPRETING_INTENT,
                        state=WorkflowEventState.STARTED,
                    )
                    model_calls += 1
                    intent = interpret_intent(
                        execution_task,
                        asset_facts=tuple(asset_bundles),
                        mesh_facts=input_mesh_facts,
                        executed_mesh_facts=executed_mesh_facts,
                        capability_kinds=tuple(
                            kind
                            for descriptor in descriptors
                            for kind in descriptor.capability_kinds
                        ),
                        gateway=self.gateway,
                        budget=INTENT_MODEL_POLICY.open(
                            model_ledger,
                            ModelStage.INTENT_INTERPRETATION,
                        ),
                        trace=model_trace,
                    )
                    _write_json(run_dir / "simulation-intent.json", intent)
                    workflow.checkpoint("simulation-intent", intent)
                    _record_event(
                        workflow,
                        stage=WorkflowStage.INTERPRETING_INTENT,
                        state=WorkflowEventState.COMPLETED,
                        evidence_paths=["simulation-intent.json"],
                    )

                    _record_event(
                        workflow,
                        stage=WorkflowStage.RESOLVING_REQUIREMENTS,
                        state=WorkflowEventState.STARTED,
                    )
                    requirements = resolve_requirements(
                        intent=intent,
                        mesh_facts=input_mesh_facts,
                        executed_mesh_facts=executed_mesh_facts,
                        capabilities=descriptors,
                    )
                    _write_json(
                        run_dir / "resolved-requirements.json",
                        requirements,
                    )
                    workflow.checkpoint(
                        "resolved-requirements",
                        requirements,
                    )
                    _record_event(
                        workflow,
                        stage=WorkflowStage.RESOLVING_REQUIREMENTS,
                        state=WorkflowEventState.COMPLETED,
                        evidence_paths=["resolved-requirements.json"],
                    )
                    hard_intent_questions = tuple(
                        item
                        for item in intent.uncertainties
                        if item.kind in {"information_required", "conflict"}
                    )
                    hard_requirement_gaps = tuple(
                        item
                        for item in requirements.gaps
                        if item.kind == "information_required"
                    )
                    if (
                        hard_intent_questions
                        or hard_requirement_gaps
                        or requirements.conflicts
                    ):
                        question_payload = {
                            "schema_version": 1,
                            "state": "INFORMATION_REQUIRED",
                            "questions": [
                                item.model_dump(mode="json")
                                for item in hard_intent_questions
                            ],
                            "requirement_gaps": [
                                item.model_dump(mode="json")
                                for item in hard_requirement_gaps
                            ],
                            "requirement_conflicts": [
                                item.model_dump(mode="json")
                                for item in requirements.conflicts
                            ],
                        }
                        _write_json(run_dir / "questions.json", question_payload)
                        _record_event(
                            workflow,
                            stage=WorkflowStage.WAITING_FOR_INFORMATION,
                            state=WorkflowEventState.DEFERRED,
                            detail=(
                                "Required design facts are missing or conflicting."
                            ),
                            evidence_paths=["questions.json"],
                        )
                        return self._finish(
                            run_dir=run_dir,
                            task=task,
                            status="INFORMATION_REQUIRED",
                            attempts=attempts,
                            message=(
                                "Simulation design needs additional concrete "
                                "information before authoring."
                            ),
                            model_calls=model_calls,
                            workflow_state=WorkflowState.DEFERRED,
                            primary_failure=FailureRecord(
                                domain=FailureDomain.DESIGN,
                                code="INFORMATION_REQUIRED",
                                detail=(
                                    "one or more high-impact facts are missing "
                                    "or conflicting"
                                ),
                                message="算例设计缺少必要的具体信息。",
                                recovery=(
                                    "查看 questions.json，补充其中列出的字段后"
                                    "以新任务重新求解。"
                                ),
                                evidence_paths=[
                                    "simulation-intent.json",
                                    "resolved-requirements.json",
                                    "questions.json",
                                ],
                            ),
                        )

                    _record_event(
                        workflow,
                        stage=WorkflowStage.DESIGNING_CASE,
                        state=WorkflowEventState.STARTED,
                    )
                    model_calls += 1
                    proposal = design_case(
                        task=execution_task,
                        intent=intent,
                        requirements=requirements,
                        mesh_facts=input_mesh_facts,
                        registry=selected_registry,
                        context=context,
                        available_executables=tuple(
                            environment.available_executable_names
                        ),
                        gateway=self.gateway,
                        budget=DESIGN_MODEL_POLICY.open(
                            model_ledger,
                            ModelStage.CASE_DESIGN,
                        ),
                        trace=model_trace,
                    )
                    proposal = _complete_planning_extensions(
                        proposal,
                        registry=selected_registry,
                        task=execution_task,
                        capability=capability,
                    )
                    _write_json(
                        run_dir / "case-design-proposal.json",
                        proposal,
                    )
                    workflow.checkpoint("case-design-proposal", proposal)
                    _record_event(
                        workflow,
                        stage=WorkflowStage.DESIGNING_CASE,
                        state=WorkflowEventState.COMPLETED,
                        evidence_paths=["case-design-proposal.json"],
                    )
                    decision = evaluate_design_risk(
                        intent=intent,
                        requirements=requirements,
                        proposal=proposal,
                        registry=selected_registry,
                    )
                    _write_json(run_dir / "risk-decision.json", decision)
                    workflow.checkpoint("risk-decision", decision)
                    if decision.state != "READY_TO_AUTHOR":
                        if decision.questions:
                            _write_json(
                                run_dir / "questions.json",
                                decision,
                            )
                        waiting_stage = (
                            WorkflowStage.WAITING_FOR_CONFIRMATION
                            if decision.state == "CONFIRMATION_REQUIRED"
                            else WorkflowStage.WAITING_FOR_INFORMATION
                        )
                        _record_event(
                            workflow,
                            stage=waiting_stage,
                            state=WorkflowEventState.DEFERRED,
                            detail=decision.state,
                            evidence_paths=[
                                "risk-decision.json",
                                *(
                                    ["questions.json"]
                                    if decision.questions
                                    else []
                                ),
                            ],
                        )
                        return self._finish(
                            run_dir=run_dir,
                            task=task,
                            status=decision.state,
                            attempts=attempts,
                            message=(
                                "Simulation design did not pass the authoring "
                                f"gate: {decision.state}."
                            ),
                            model_calls=model_calls,
                            workflow_state=WorkflowState.DEFERRED,
                            primary_failure=FailureRecord(
                                domain=FailureDomain.DESIGN,
                                code=decision.state,
                                detail="; ".join(decision.reason_codes),
                                message=(
                                    "需要逐字段确认具体工程值。"
                                    if decision.state
                                    == "CONFIRMATION_REQUIRED"
                                    else (
                                        "当前受信任能力无法实现该设计。"
                                        if decision.state
                                        == "CAPABILITY_UNAVAILABLE"
                                        else "算例设计需要补充信息。"
                                    )
                                ),
                                recovery=(
                                    "运行 foampilot questions 查看字段、原因和候选；"
                                    "使用 foampilot confirm 提交逐字段确认。"
                                    if decision.state
                                    == "CONFIRMATION_REQUIRED"
                                    else "查看 risk-decision.json 和 questions.json。"
                                ),
                                evidence_paths=[
                                    "case-design-proposal.json",
                                    "risk-decision.json",
                                    *(
                                        ["questions.json"]
                                        if decision.questions
                                        else []
                                    ),
                                ],
                            ),
                        )
                    case_design = freeze_case_design(
                        proposal=proposal,
                        decision=decision,
                        intent=intent,
                        numerical_repair_envelope=(
                            _default_numerical_repair_envelope(proposal)
                        ),
                    )
                    _write_json(run_dir / "case-design.json", case_design)
                    workflow.checkpoint("case-design", case_design)
                except OperationCancelled:
                    return self._finish_cancelled(
                        run_dir=run_dir,
                        task=task,
                        attempts=attempts,
                        model_calls=model_calls,
                        stage="case-design",
                    )
                except GatewayRequestError as error:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="DEFERRED",
                        attempts=attempts,
                        message=f"Design model transport is unavailable: {error}",
                        model_calls=model_calls,
                        workflow_state=WorkflowState.DEFERRED,
                        terminal_blocker=_backend_blocker(error),
                        resume=ResumeMetadata(
                            allowed=False,
                            reason=(
                                "intent/design has no strict continuation path; "
                                "rerun when the backend is available"
                            ),
                        ),
                    )
                except (LineageBudgetExhausted, OSError, ValueError) as error:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="CASE_DESIGN_FAILED",
                        attempts=attempts,
                        message=f"Case design failed: {error}",
                        model_calls=model_calls,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="CASE_DESIGN_FAILED",
                            detail=str(error),
                            message="算例设计阶段失败。",
                            recovery="查看结构化设计产物与模型 trace 后修正输入。",
                        ),
                    )

        if verified_source is not None:
            case_design = verified_source.design
            intent = verified_source.intent
            _write_json(run_dir / "simulation-intent.json", intent)
            _write_json(run_dir / "case-design.json", case_design)

        if case_design is not None and selected_registry is None:
            try:
                resumed_registry = _production_capability_registry(
                    capability,
                    task,
                )
                resumed_identities = {
                    extension_id: (
                        f"{resumed_registry.descriptor(extension_id).extension_version}"
                        f"/protocol-{resumed_registry.descriptor(extension_id).protocol_version}"
                    )
                    for extension_id in resumed_registry.extension_ids()
                }
                if resumed_identities != case_design.extension_identities:
                    raise ValueError(
                        "frozen extension identities do not match the current "
                        "trusted capability registry"
                    )
                selected_registry = resumed_registry
            except (LookupError, ValueError) as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_DESIGN_CHECKPOINT_INVALID",
                    attempts=attempts,
                    message=f"Frozen CaseDesign capabilities cannot be rebound: {error}",
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.DESIGN,
                        code="CASE_DESIGN_EXTENSION_IDENTITY_MISMATCH",
                        detail=str(error),
                        message="冻结设计与当前受信任扩展身份不一致。",
                        recovery="使用 rerun 重新执行完整设计和编译。",
                    ),
                )

        if intent is None and (run_dir / "simulation-intent.json").is_file():
            try:
                intent = SimulationIntent.model_validate_json(
                    (run_dir / "simulation-intent.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, ValueError) as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="OBSERVATION_PLANNING_FAILED",
                    attempts=attempts,
                    message=f"Frozen intent cannot be loaded: {error}",
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.DESIGN,
                        code="SIMULATION_INTENT_CHECKPOINT_INVALID",
                        detail=str(error),
                    ),
                )

        assert case_design is not None
        contract_pipeline = ContractStagePipeline(
            run_dir=run_dir,
            task_id=task.task_id,
            workflow=workflow,
            cancellation_requested=activity_reporter.is_cancel_requested,
        )
        try:
            planning_contracts = contract_pipeline.plan_before_authoring(
                intent=intent or SimulationIntent(),
                design=case_design,
                mesh_facts=input_mesh_facts,
            )
            acceptance_plan = planning_contracts.acceptance
            observation_plan = planning_contracts.observations
            if verified_source is not None and (
                acceptance_plan.canonical_sha256()
                != verified_source.acceptance_plan.canonical_sha256()
                or observation_plan.canonical_sha256()
                != verified_source.observation_plan.canonical_sha256()
            ):
                raise ContractStageError(
                    FailureRecord(
                        domain=FailureDomain.PLAN,
                        code="PLAN_REUSE_CONTRACT_MISMATCH",
                        detail=(
                            "recompiled acceptance or observation contract "
                            "differs from the verified source"
                        ),
                        message="复用源的观测或验收契约不再与当前输入一致。",
                        recovery="不使用 warm-plan，重新执行完整设计与编译。",
                    )
                )
        except ContractStageError as error:
            return self._finish(
                run_dir=run_dir,
                task=task,
                status=error.failure.code,
                attempts=attempts,
                message=f"Contract planning failed: {error}",
                model_calls=model_calls,
                workflow_state=(
                    WorkflowState.CANCELLED
                    if error.failure.code == "USER_CANCELLED"
                    else WorkflowState.FAILED
                ),
                primary_failure=error.failure,
            )

        if self.gateway is not None:
            fingerprint = build_resume_fingerprint(
                task=task,
                environment=environment,
                runtime_config=self.runtime_config,
                model=self.gateway.primary_model,
                backend_id=self.gateway.primary_backend_id,
                backend_policy_sha256=self.gateway.policy_sha256,
                knowledge_ids=context.selected_knowledge_ids,
                knowledge_text=context.knowledge_text,
                skill_ids=context.skill_names,
                skills_text=context.skills_text,
                public_asset_root=effective_public_asset_root,
                acceptance_plan_sha256=sha256(
                    (run_dir / "acceptance-plan.json").read_bytes()
                ).hexdigest(),
                observation_plan_sha256=sha256(
                    (run_dir / "observation-plan.json").read_bytes()
                ).hexdigest(),
            )
            _write_json(
                run_dir / "resume-compatibility.json",
                fingerprint,
            )
            if _rerun is not None:
                _write_json(
                    run_dir / "lineage.json",
                    build_lineage_record(
                        rerun=_rerun,
                        task=task,
                        current_fingerprint=fingerprint,
                    ),
                )

        continuation_index = (
            _continuation.continuation_index_for_stage
            if _continuation is not None
            else 0
        )
        pending_repair_changes: RepairChangeSet | None = None
        pending_repair_source_attempt: Path | None = None

        if verified_source is not None:
            plan = verified_source.plan
            contract_pipeline.record_case_authored(_bundle_from_plan(plan))
        elif (
            _continuation is not None
            and _continuation.from_stage
            == WorkflowStage.MODEL_REPAIR_STARTED
        ):
            parent_plan = load_parent_plan(_continuation)
            assert _continuation.run_assessment_path is not None
            assert _continuation.active_plan_path is not None
            assert _continuation.run_facts_path is not None
            report = RunAssessment.model_validate_json(
                _continuation.run_assessment_path.read_text(
                    encoding="utf-8"
                )
            )
            parent_case = _continuation.active_plan_path.parent / "case"
            parent_mesh_quality_path = (
                _continuation.active_plan_path.parent
                / "mesh-quality-report.json"
            )
            parent_mesh_quality = (
                MeshQualityReport.model_validate_json(
                    parent_mesh_quality_path.read_text(encoding="utf-8")
                )
                if parent_mesh_quality_path.is_file()
                else None
            )
            current_files = _read_declared_files(
                parent_case,
                parent_plan,
            )
            run_facts = RunFacts.model_validate_json(
                _continuation.run_facts_path.read_text(encoding="utf-8")
            )
            log_text = json.dumps(
                run_facts.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            parent_attempt = attempts[-1].attempt
            assert case_design is not None
            assert selected_registry is not None
            try:
                parent_inspection_path = (
                    _continuation.active_plan_path.parent
                    / "static-inspection.json"
                )
                parent_inspection = (
                    InspectionReport.model_validate_json(
                        parent_inspection_path.read_text(encoding="utf-8")
                    )
                    if parent_inspection_path.is_file()
                    else None
                )
                classification = classify_native_failure(
                    report=report,
                    plan=parent_plan,
                    run_facts=run_facts,
                    inspection=(
                        parent_inspection
                        if parent_inspection is not None
                        and not parent_inspection.passed
                        else None
                    ),
                    prior_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                )
                classification_name = (
                    f"failure-classification-attempt-{parent_attempt:02d}.json"
                )
                _write_json(run_dir / classification_name, classification)
                eligible, ineligible_reason = _automatic_repair_eligible(
                    classification,
                    case_design,
                )
                if not eligible:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status=(
                            _continuation.parent_summary.native_status
                            or attempts[-1].status
                        ),
                        attempts=attempts,
                        message=(
                            "Automatic repair is not authorized: "
                            + ineligible_reason
                        ),
                        model_calls=model_calls,
                        workflow_state=WorkflowState.FAILED,
                        primary_failure=(
                            _continuation.parent_summary.primary_failure
                        ),
                        terminal_blocker=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="AUTOMATIC_REPAIR_NOT_AUTHORIZED",
                            detail=ineligible_reason,
                            message="续跑失败不属于冻结设计允许的自动数值修复。",
                            recovery="使用 rerun 补充信息或重新设计。",
                            evidence_paths=[classification_name],
                        ),
                    )
                policy = _repair_policy(execution_task)
                if not policy.automatic_numerical_repair:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status=(
                            _continuation.parent_summary.native_status
                            or attempts[-1].status
                        ),
                        attempts=attempts,
                        message="Automatic numerical repair is disabled.",
                        model_calls=model_calls,
                        workflow_state=WorkflowState.FAILED,
                        primary_failure=(
                            _continuation.parent_summary.primary_failure
                        ),
                        terminal_blocker=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="AUTOMATIC_NUMERICAL_REPAIR_DISABLED",
                            detail=(
                                "task repair policy disables automatic "
                                "numerical repair"
                            ),
                            message="任务按配置未执行自动数值修复。",
                            recovery="调整 repair_policy 后使用 rerun。",
                            evidence_paths=[classification_name],
                        ),
                    )
                repair_context = self._context(
                    task,
                    capability,
                    repair=True,
                    repair_evidence=(
                        report.detail + "\n" + log_text
                    ),
                    geometry_facts=geometry_facts,
                )
                _write_json(
                    run_dir / "repair-agent-context.json",
                    {
                        "knowledge_slots": (
                            repair_context.knowledge_slots
                        ),
                        "missing_slots": repair_context.missing_slots,
                        "selected_knowledge_ids": (
                            repair_context.selected_knowledge_ids
                        ),
                        "selected_source_hashes": (
                            repair_context.selected_source_hashes
                        ),
                        "skill_names": repair_context.skill_names,
                    },
                )
                repair_scope = build_repair_scope(
                    classification=classification,
                    task=execution_task,
                    plan=parent_plan,
                    current_files=current_files,
                    knowledge_ids=(
                        repair_context.selected_knowledge_ids
                    ),
                )
                scope_name = (
                    f"repair-scope-attempt-{parent_attempt:02d}.json"
                )
                _write_json(run_dir / scope_name, repair_scope)
                _record_event(
                    workflow,
                    stage=WorkflowStage.REPAIR_SCOPE_READY,
                    state=WorkflowEventState.COMPLETED,
                    attempt=parent_attempt,
                    evidence_paths=[classification_name, scope_name],
                )
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_REPAIR_STARTED,
                    state=WorkflowEventState.STARTED,
                    attempt=parent_attempt,
                )
                continuation_failure = FailureRecord(
                    domain=classification.domain,
                    code=classification.code,
                    step_id=classification.failed_step_id,
                    detail=report.detail,
                    evidence_paths=[classification_name, scope_name],
                )
                repair_status = build_agent_status_snapshot(
                    decision_stage=AgentDecisionStage.REPAIR,
                    task=execution_task,
                    capability=capability,
                    context=repair_context,
                    workflow=workflow,
                    model_budget=model_ledger,
                    logical_requests_used=(
                        lineage_logical_requests_before_run + model_calls
                    ),
                    logical_request_limit=logical_request_limit,
                    current_attempt=parent_attempt,
                    execution_seconds_used=execution_seconds_used,
                    plan=parent_plan,
                    latest_failure=continuation_failure,
                    allowed_actions=list(
                        dict.fromkeys(
                            operation.replace("insert_command_before", "insert_command")
                            .replace("insert_command_after", "insert_command")
                            for operation in repair_scope.allowed_operations
                        )
                    ),
                )
                repair_status_artifact = _write_status_artifact(
                    run_dir=run_dir,
                    name=f"agent-status-repair-{parent_attempt:02d}.json",
                    snapshot=repair_status,
                )
                model_calls += 1
                proposal = request_repair_proposal(
                    task=execution_task,
                    plan=parent_plan,
                    classification=classification,
                    repair_scope=repair_scope,
                    run_facts=run_facts,
                    knowledge_text=repair_context.knowledge_text,
                    skills_text=repair_context.skills_text,
                    geometry_facts=geometry_facts,
                    mesh_quality_report=parent_mesh_quality,
                    status_snapshot=repair_status,
                    status_artifact=repair_status_artifact,
                    gateway=self.gateway,
                    budget=REPAIR_MODEL_POLICY.open(
                        model_ledger,
                        ModelStage.REPAIR,
                    ),
                    trace=model_trace,
                )
                authorization = authorize_repair(
                    proposal=proposal,
                    design=case_design,
                    policy=policy,
                )
                if authorization.state != "AUTHORIZED_AUTOMATIC":
                    raise ValueError(
                        "REPAIR_NOT_AUTOMATICALLY_AUTHORIZED: "
                        + ", ".join(authorization.reason_codes)
                    )
                repaired = apply_authorized_repair(
                    proposal=proposal,
                    authorization=authorization,
                    design=case_design,
                    bundle=_bundle_from_plan(parent_plan),
                    mesh_facts=input_mesh_facts,
                    extensions=selected_registry,
                    public_asset_install_paths=tuple(
                        item.install_path
                        if item.kind == "directory"
                        else item.path
                        for item in execution_task.public_assets
                        if (
                            item.install_path
                            if item.kind == "directory"
                            else item.path
                        )
                        is not None
                    ),
                    protected_paths=tuple(execution_task.protected_paths),
                )
                case_design = repaired.design
                plan = compile_execution_plan(
                    design=case_design,
                    bundle=repaired.bundle,
                    environment=environment,
                    task=execution_task,
                    registry=selected_registry,
                    observation_plan=observation_plan,
                )
            except OperationCancelled:
                return self._finish_cancelled(
                    run_dir=run_dir,
                    task=task,
                    attempts=attempts,
                    model_calls=model_calls,
                    stage="repair",
                )
            except (
                FailureClassificationError,
                RepairScopeError,
            ) as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.WORKFLOW,
                        code=error.code,
                        detail=error.detail,
                        message=error.message,
                        recovery=error.recovery,
                    ),
                )
            except AgentStatusError as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_agent_status_failure(error),
                )
            except GatewayRequestError as error:
                can_resume = (
                    error.failure.retryable
                    and continuation_index < 2
                    and model_ledger.transport_attempts_remaining > 0
                )
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                    workflow_state=WorkflowState.DEFERRED,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                    terminal_blocker=_backend_blocker(error),
                    resume=ResumeMetadata(
                        allowed=can_resume,
                        from_stage=(
                            WorkflowStage.MODEL_REPAIR_STARTED
                            if can_resume
                            else None
                        ),
                        reason=(
                            "retryable backend failure during repair"
                            if can_resume
                            else "continuation or transport budget exhausted"
                        ),
                    ),
                )
            except LineageBudgetExhausted as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.BACKEND,
                        code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                        detail=str(error),
                    ),
                )
            except Exception as error:
                status = (
                    _continuation.parent_summary.native_status
                    or attempts[-1].status
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Repair proposal failed: {error}",
                    model_calls=model_calls,
                    primary_failure=(
                        _continuation.parent_summary.primary_failure
                    ),
                )
            patch_name = f"repair-proposal-attempt-{parent_attempt:02d}.json"
            derived_name = (
                f"derived-case-design-attempt-{parent_attempt:02d}.json"
            )
            _write_json(run_dir / patch_name, proposal)
            _write_json(run_dir / derived_name, repaired.derived)
            _write_json(
                run_dir
                / f"design-conformance-attempt-{parent_attempt:02d}.json",
                repaired.conformance,
            )
            pending_repair_changes = RepairChangeSet(
                changed_file_paths=tuple(
                    item.path for item in proposal.file_operations
                ),
                changed_files=tuple(
                    GeneratedFile(path=item.path, content=item.content)
                    for item in proposal.file_operations
                ),
            )
            pending_repair_source_attempt = (
                _continuation.active_plan_path.parent
            )
            _record_event(
                workflow,
                stage=WorkflowStage.REPAIR_APPLIED,
                state=WorkflowEventState.COMPLETED,
                attempt=parent_attempt,
                evidence_paths=[patch_name, derived_name],
            )
        else:
            try:
                if self.gateway is None:
                    raise ValueError(
                        "live authoring requires a model gateway"
                    )
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_GENERATION_STARTED,
                    state=WorkflowEventState.STARTED,
                )
                author_status = build_agent_status_snapshot(
                    decision_stage=AgentDecisionStage.AUTHOR,
                    task=execution_task,
                    capability=capability,
                    context=context,
                    workflow=workflow,
                    model_budget=model_ledger,
                    logical_requests_used=(
                        lineage_logical_requests_before_run + model_calls
                    ),
                    logical_request_limit=logical_request_limit,
                    current_attempt=1,
                    execution_seconds_used=execution_seconds_used,
                )
                _write_status_artifact(
                    run_dir=run_dir,
                    name="agent-status-author-01.json",
                    snapshot=author_status,
                )
                model_calls += 1
                assert case_design is not None
                assert selected_registry is not None
                bundle = author_case(
                    design=case_design,
                    mesh_facts=input_mesh_facts,
                    geometry_facts=geometry_facts,
                    target_facts=_author_target_facts(
                        task=execution_task,
                        design=case_design,
                        capability=capability,
                        extensions=selected_registry,
                    ),
                    context=context,
                    gateway=self.gateway,
                    budget=AUTHOR_MODEL_POLICY.open(
                        model_ledger,
                        ModelStage.CASE_AUTHORING,
                    ),
                    trace=model_trace,
                )
                bundle, _observation_fragments = inject_observation_fragments(
                    bundle,
                    observation_plan,
                )
                _write_json(run_dir / "case-bundle.json", bundle)
                conformance = verify_design_conformance(
                    design=case_design,
                    bundle=bundle,
                    mesh_facts=input_mesh_facts,
                    extensions=selected_registry,
                )
                _write_json(run_dir / "design-conformance.json", conformance)
                if not conformance.passed:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="CASE_DESIGN_CONTRADICTED",
                        attempts=attempts,
                        message=(
                            "Authored CaseBundle contradicts the frozen "
                            "CaseDesign."
                        ),
                        model_calls=model_calls,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="CASE_DESIGN_CONTRADICTED",
                            detail="; ".join(
                                f"{item.code}: {item.detail}"
                                for item in conformance.issues
                            ),
                            message="生成的 case 与冻结设计不一致，未执行。",
                            recovery=(
                                "检查 case-design.json 与 design-conformance.json；"
                                "不得放宽 RiskGate。"
                            ),
                            evidence_paths=[
                                "case-design.json",
                                "design-conformance.json",
                            ],
                        ),
                    )
                plan = compile_execution_plan(
                    design=case_design,
                    bundle=bundle,
                    environment=environment,
                    task=execution_task,
                    registry=selected_registry,
                    observation_plan=observation_plan,
                )
                contract_pipeline.record_case_authored(bundle)
            except OperationCancelled:
                return self._finish_cancelled(
                    run_dir=run_dir,
                    task=task,
                    attempts=attempts,
                    model_calls=model_calls,
                    stage="generation",
                )
            except AgentStatusError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_agent_status_failure(error),
                )
            except GatewayRequestError as error:
                if error.failure.kind == BackendFailureKind.SCHEMA_INVALID:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="GENERATION_INVALID",
                        attempts=attempts,
                        message=(
                            "模型输出未能形成有效的 CaseBundle："
                            f"{error.failure.detail}"
                        ),
                        model_calls=model_calls,
                        workflow_state=WorkflowState.FAILED,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.PLAN,
                            code="GENERATION_INVALID",
                            detail=error.failure.detail,
                            message="模型输出的算例文件包结构无效。",
                            recovery=(
                                "调整生成上下文或模型后重新生成；"
                                "该错误不是模型服务不可用。"
                            ),
                        ),
                        resume=ResumeMetadata(
                            allowed=False,
                            reason=(
                                "invalid generated plan is not a "
                                "transport continuation"
                            ),
                        ),
                    )
                can_resume = (
                    error.failure.retryable
                    and continuation_index < 2
                    and model_ledger.transport_attempts_remaining > 0
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="DEFERRED",
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                    workflow_state=WorkflowState.DEFERRED,
                    terminal_blocker=_backend_blocker(error),
                    resume=ResumeMetadata(
                        allowed=can_resume,
                        from_stage=(
                            WorkflowStage.MODEL_GENERATION_STARTED
                            if can_resume
                            else None
                        ),
                        reason=(
                            "retryable backend failure during generation"
                            if can_resume
                            else "continuation or transport budget exhausted"
                        ),
                    ),
                )
            except LineageBudgetExhausted as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.BACKEND,
                        code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                        detail=str(error),
                    ),
                )
            except CaseAuthoringError as error:
                _write_json(
                    run_dir / "authoring-error.json",
                    {
                        "schema_version": 1,
                        "code": str(error).partition(":")[0],
                        "detail": str(error),
                        "design_sha256": (
                            case_design.design_sha256
                            if case_design is not None
                            else None
                        ),
                    },
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_DESIGN_CONTRADICTED",
                    attempts=attempts,
                    message=f"Case Author contradicted the frozen design: {error}",
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.DESIGN,
                        code="CASE_DESIGN_CONTRADICTED",
                        detail=str(error),
                        message="生成的算例文件与冻结设计不一致，未执行。",
                        recovery="检查 case-design.json 和 authoring-error.json。",
                        evidence_paths=["case-design.json", "authoring-error.json"],
                    ),
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
        if verified_source is None:
            _write_json(run_dir / "compiled-execution-plan.json", plan)
        normalization = normalize_execution_plan(
            plan,
            execution_task,
            environment.available_executable_names,
        )
        _write_json(
            run_dir / "plan-normalization.json",
            [
                *normalization.records,
                *normalization.stage_records,
            ],
        )
        plan = normalization.plan
        next_attempt = len(attempts) + 1
        _write_json(run_dir / "execution-plan.json", plan)
        active_checkpoint = workflow.checkpoint(
            f"active-plan-attempt-{next_attempt:02d}",
            plan,
        )
        _record_event(
            workflow,
            stage=WorkflowStage.PLAN_READY,
            state=WorkflowEventState.COMPLETED,
            evidence_paths=[
                "execution-plan.json",
                active_checkpoint.relative_to(run_dir).as_posix(),
            ],
        )

        plan_issues = validate_execution_plan(
            plan,
            execution_task,
            environment.available_executable_names,
        )
        if plan_issues:
            _write_json(run_dir / "plan-issues.json", plan_issues)
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="PLAN_INVALID",
                attempts=attempts,
                message="The compiled execution plan violates safety policy.",
                model_calls=model_calls,
            )

        if _plan_only:
            _write_json(
                run_dir / "plan-only.json",
                {
                    "schema_version": 1,
                    "status": "PLAN_READY",
                    "execution_plan": "execution-plan.json",
                    "case_design": "case-design.json",
                    "case_bundle": "case-bundle.json",
                    "design_conformance": "design-conformance.json",
                },
            )
            return self._finish(
                run_dir=run_dir,
                task=task,
                status="PLAN_READY",
                attempts=attempts,
                message="Canonical ExecutionPlan v4 compiled; no case was executed.",
                model_calls=model_calls,
                workflow_state=WorkflowState.COMPLETED,
            )

        active_plan = plan
        fingerprints: list[str] = [
            item.failure_fingerprint
            for item in attempts
            if item.failure_fingerprint is not None
        ]
        for attempt_number in range(
            next_attempt,
            task.resource_budget.max_attempts + 1,
        ):
            attempt_root = run_dir / f"attempt-{attempt_number:02d}"
            case_root = attempt_root / "case"
            case_root.mkdir(parents=True)
            _write_json(attempt_root / "execution-plan.json", active_plan)
            if case_design is not None:
                active_bundle = _bundle_from_plan(active_plan)
                active_conformance = verify_design_conformance(
                    design=case_design,
                    bundle=active_bundle,
                    mesh_facts=input_mesh_facts,
                    extensions=selected_registry,
                )
                _write_json(attempt_root / "case-design.json", case_design)
                _write_json(attempt_root / "case-bundle.json", active_bundle)
                _write_json(
                    attempt_root / "design-conformance.json",
                    active_conformance,
                )
                if not active_conformance.passed:
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="CASE_DESIGN_CONTRADICTED",
                        attempts=attempts,
                        message=(
                            "Active attempt bundle contradicts its compiled "
                            "CaseDesign."
                        ),
                        model_calls=model_calls,
                        primary_failure=FailureRecord(
                            domain=FailureDomain.DESIGN,
                            code="CASE_DESIGN_CONTRADICTED",
                            detail="; ".join(
                                f"{item.code}: {item.detail}"
                                for item in active_conformance.issues
                            ),
                            evidence_paths=[
                                f"attempt-{attempt_number:02d}/case-design.json",
                                f"attempt-{attempt_number:02d}/case-bundle.json",
                                (
                                    f"attempt-{attempt_number:02d}/"
                                    "design-conformance.json"
                                ),
                            ],
                        ),
                    )
            repair_preparation = None

            try:
                if task.public_assets:
                    assert effective_public_asset_root is not None
                    stage_public_assets(
                        task,
                        effective_public_asset_root,
                        case_root,
                    )
                materialize_case(active_plan, execution_task, case_root)
                if (
                    pending_repair_changes is not None
                    and pending_repair_source_attempt is not None
                ):
                    repair_preparation = prepare_repair_reuse(
                        parent_attempt=pending_repair_source_attempt,
                        next_case_root=case_root,
                        plan=active_plan,
                        changes=pending_repair_changes,
                    )
                    _write_json(
                        attempt_root / "execution-reuse.json",
                        repair_preparation.record,
                    )
                    if repair_preparation.record.get("applied") is True:
                        _update_performance_context(
                            run_dir,
                            path_kind="repair_reuse",
                            repair_start_stage=(
                                repair_preparation.decision.earliest_rerun_stage
                            ),
                        )
                    else:
                        _update_performance_context(
                            run_dir,
                            repair_start_stage="mesh",
                            diagnostic=(
                                "REPAIR_REUSE_UNSAFE: "
                                + ",".join(
                                    repair_preparation.decision.reason_codes
                                )
                            ),
                        )
                    pending_repair_changes = None
                    pending_repair_source_attempt = None
            except Exception as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status="CASE_GENERATION_FAILED",
                    attempts=attempts,
                    message=f"Case materialization failed: {error}",
                    model_calls=model_calls,
                )
            _record_event(
                workflow,
                stage=WorkflowStage.CASE_MATERIALIZED,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    f"attempt-{attempt_number:02d}/case",
                ],
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
                task=execution_task,
                plan=active_plan,
                available_executables=(
                    environment.available_executable_names
                ),
            )
            _write_json(
                attempt_root / "static-inspection.json",
                inspection,
            )
            _record_event(
                workflow,
                stage=WorkflowStage.STATIC_INSPECTION_COMPLETE,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    (
                        f"attempt-{attempt_number:02d}/"
                        "static-inspection.json"
                    )
                ],
            )
            risk_report = scan_execution_risk(
                case_root,
                openfoam_root=self.runtime_config.openfoam_root,
                trusted_readonly_roots=(
                    self.runtime_config.trusted_readonly_roots
                ),
                commands=active_plan.commands,
            )
            risk_path = attempt_root / "execution-risk-report.json"
            _write_json(risk_path, risk_report)
            mesh_quality: MeshQualityReport | None = None
            if inspection.passed:
                commands_to_execute = (
                    list(repair_preparation.commands_to_execute)
                    if repair_preparation is not None
                    else list(active_plan.commands)
                )
                reused_steps: list[ReusedStepResult] = (
                    list(repair_preparation.reused_steps)
                    if repair_preparation is not None
                    else []
                )
                mesh_key_result = None
                mesh_lookup = None
                if (
                    derived_cache_store is not None
                    and attempt_number == next_attempt
                    and repair_preparation is None
                ):
                    mesh_key_result = mesh_cache_key(
                        task,
                        geometry_facts=geometry_facts,
                        input_mesh_facts=(
                            input_mesh_facts[0]
                            if len(input_mesh_facts) == 1
                            else None
                        ),
                        plan=active_plan,
                        environment=environment,
                        public_asset_root=Path(
                            effective_public_asset_root or run_dir
                        ),
                    )
                    check_mesh_present = any(
                        command.stage == "check"
                        and command.executable == "checkMesh"
                        for command in active_plan.commands
                    )
                    if not mesh_key_result.cacheable:
                        _write_json(
                            attempt_root / "mesh-cache.json",
                            {
                                "schema_version": 1,
                                "status": "miss",
                                "cache_key": None,
                                "reason_code": mesh_key_result.reason_code,
                            },
                        )
                        _update_performance_context(
                            run_dir,
                            mesh="miss",
                            diagnostic=(
                                f"DERIVED_CACHE_MISS: "
                                f"{mesh_key_result.reason_code}"
                            ),
                        )
                    elif not check_mesh_present:
                        _write_json(
                            attempt_root / "mesh-cache.json",
                            {
                                "schema_version": 1,
                                "status": "miss",
                                "cache_key": mesh_key_result.key,
                                "reason_code": (
                                    "MESH_REUSE_REQUIRES_CHECKMESH"
                                ),
                            },
                        )
                        _update_performance_context(
                            run_dir,
                            mesh="miss",
                            diagnostic=(
                                "DERIVED_CACHE_MISS: "
                                "MESH_REUSE_REQUIRES_CHECKMESH"
                            ),
                        )
                    else:
                        assert mesh_key_result.key is not None
                        mesh_lookup = derived_cache_store.restore_mesh(
                            mesh_key_result.key,
                            case_root=case_root,
                        )
                        _write_json(
                            attempt_root / "mesh-cache.json",
                            {
                                "schema_version": 1,
                                "status": mesh_lookup.status,
                                "cache_key": mesh_lookup.key,
                                "reason_code": mesh_lookup.reason_code,
                                "source": mesh_lookup.value,
                            },
                        )
                        if mesh_lookup.status == "hit":
                            mesh_commands = [
                                command
                                for command in active_plan.commands
                                if command.stage == "mesh"
                            ]
                            commands_to_execute = [
                                command
                                for command in active_plan.commands
                                if command.stage != "mesh"
                            ]
                            source = mesh_lookup.value or {}
                            source_id = (
                                f"{source.get('source_run_id', 'cache')}:"
                                f"attempt-{int(source.get('source_attempt', 0)):02d}"
                            )
                            reused_steps = [
                                ReusedStepResult(
                                    step_id=command.step_id,
                                    stage=command.stage.value,
                                    executable=command.executable,
                                    source_kind="derived_cache",
                                    source_id=source_id,
                                    reason_codes=[
                                        "EXACT_MESH_CACHE_KEY_MATCH"
                                    ],
                                )
                                for command in mesh_commands
                            ]
                            _write_json(
                                attempt_root / "execution-reuse.json",
                                {
                                    "schema_version": 1,
                                    "source_kind": "derived_cache",
                                    "source_hash": mesh_lookup.key,
                                    "reused_step_ids": [
                                        item.step_id for item in reused_steps
                                    ],
                                    "commands_to_execute": [
                                        item.step_id
                                        for item in commands_to_execute
                                    ],
                                    "reason_codes": [
                                        "EXACT_MESH_CACHE_KEY_MATCH"
                                    ],
                                },
                            )
                            _update_performance_context(
                                run_dir,
                                path_kind="warm_mesh",
                                mesh="hit",
                            )
                        elif (
                            mesh_lookup.reason_code
                            == "DERIVED_CACHE_INVALID"
                        ):
                            _update_performance_context(
                                run_dir,
                                mesh="miss",
                                diagnostic="DERIVED_CACHE_INVALID: mesh",
                            )
                runner = self.runner
                if runner is None:
                    runner = PlanRunner.from_runtime_config(
                        self.runtime_config,
                        environment.available_executable_names,
                        environment=environment,
                        workspace_root=run_dir,
                        activity_reporter=activity_reporter,
                    )
                elif isinstance(runner, PlanRunner):
                    runner.activity_reporter = activity_reporter
                protected_paths = runtime_protected_paths(
                    execution_task.protected_paths,
                    environment,
                    self.protected_runtime_roots,
                )
                risk_report = scan_execution_risk(
                    case_root,
                    openfoam_root=self.runtime_config.openfoam_root,
                    trusted_readonly_roots=(
                        self.runtime_config.trusted_readonly_roots
                    ),
                    commands=commands_to_execute,
                )
                _write_json(risk_path, risk_report)
                runner_records_live_workflow = bool(
                    getattr(runner, "emits_live_workflow", False)
                )
                runner_arguments = {
                    "case_dir": case_root,
                    "commands": commands_to_execute,
                    "budget": task.resource_budget,
                    "risk_report": risk_report,
                    "protected_paths": protected_paths,
                    "execution_seconds_used": execution_seconds_used,
                }
                if runner_records_live_workflow:
                    runner_arguments.update(
                        {
                            "workflow": workflow,
                            "attempt": attempt_number,
                        }
                    )
                try:
                    run_result = runner.run(**runner_arguments)
                except RuntimeExecutionError as error:
                    risk_report = risk_report.model_copy(
                        update={"policy_decision": error.decision.code}
                    )
                    _write_json(risk_path, risk_report)
                    _write_json(
                        attempt_root / "sandbox-probe.json",
                        error.probe,
                    )
                    _write_json(
                        attempt_root / "execution-policy.json",
                        error.decision,
                    )
                    _write_json(run_dir / "sandbox-probe.json", error.probe)
                    _write_json(
                        run_dir / "execution-policy.json",
                        error.decision,
                    )
                    attempts.append(
                        AttemptSummary(
                            attempt=attempt_number,
                            status="BLOCKED_ENVIRONMENT",
                        )
                    )
                    evidence = [
                        f"attempt-{attempt_number:02d}/execution-risk-report.json",
                        f"attempt-{attempt_number:02d}/sandbox-probe.json",
                        f"attempt-{attempt_number:02d}/execution-policy.json",
                    ]
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status="BLOCKED_ENVIRONMENT",
                        attempts=attempts,
                        message="Execution policy blocked the attempt.",
                        model_calls=model_calls,
                        primary_failure=_execution_environment_failure(
                            code=error.code,
                            detail=error.probe.detail,
                            evidence_paths=evidence,
                        ),
                    )
                if (
                    run_result.sandbox_probe is None
                    or run_result.execution_policy is None
                ):
                    raise RuntimeError(
                        "runner omitted sandbox probe or execution policy evidence"
                    )
                risk_report = risk_report.model_copy(
                    update={
                        "policy_decision": run_result.execution_policy.code
                    }
                )
                _write_json(risk_path, risk_report)
                _write_json(
                    attempt_root / "sandbox-probe.json",
                    run_result.sandbox_probe,
                )
                _write_json(
                    attempt_root / "execution-policy.json",
                    run_result.execution_policy,
                )
                _write_json(
                    run_dir / "sandbox-probe.json",
                    run_result.sandbox_probe,
                )
                _write_json(
                    run_dir / "execution-policy.json",
                    run_result.execution_policy,
                )
                execution_seconds_used += _run_result_seconds(run_result)
                if reused_steps:
                    run_result = run_result.model_copy(
                        update={"reused_steps": reused_steps}
                    )
                _write_json(attempt_root / "run-result.json", run_result)
                if run_result.cancelled:
                    attempts.append(
                        AttemptSummary(
                            attempt=attempt_number,
                            status="CANCELLED",
                        )
                    )
                    return self._finish_cancelled(
                        run_dir=run_dir,
                        task=task,
                        attempts=attempts,
                        model_calls=model_calls,
                        stage="openfoam",
                    )
                if run_result.execution_error_code is not None:
                    wall_budget_exhausted = (
                        run_result.execution_error_code
                        == "EXECUTION_WALL_BUDGET_EXHAUSTED"
                    )
                    attempts.append(
                        AttemptSummary(
                            attempt=attempt_number,
                            status=(
                                "EXECUTION_BUDGET_EXHAUSTED"
                                if wall_budget_exhausted
                                else "BLOCKED_ENVIRONMENT"
                            ),
                            failed_step_id=run_result.failed_step_id,
                        )
                    )
                    evidence = [
                        f"attempt-{attempt_number:02d}/execution-risk-report.json",
                        f"attempt-{attempt_number:02d}/sandbox-probe.json",
                        f"attempt-{attempt_number:02d}/execution-policy.json",
                        f"attempt-{attempt_number:02d}/run-result.json",
                    ]
                    return self._finish(
                        run_dir=run_dir,
                        task=task,
                        status=(
                            "EXECUTION_WALL_BUDGET_EXHAUSTED"
                            if wall_budget_exhausted
                            else "BLOCKED_ENVIRONMENT"
                        ),
                        attempts=attempts,
                        message=(
                            "Cumulative OpenFOAM execution budget exhausted."
                            if wall_budget_exhausted
                            else "Sandbox setup failed during execution."
                        ),
                        model_calls=model_calls,
                        primary_failure=(
                            _execution_budget_failure(
                                evidence_paths=evidence,
                                step_id=run_result.failed_step_id,
                            )
                            if wall_budget_exhausted
                            else _execution_environment_failure(
                                code=run_result.execution_error_code,
                                detail=(
                                    "the selected sandbox backend failed before "
                                    "a trustworthy OpenFOAM result was produced"
                                ),
                                evidence_paths=evidence,
                                step_id=run_result.failed_step_id,
                            )
                        ),
                    )
                if not runner_records_live_workflow:
                    for step in run_result.steps:
                        _record_event(
                            workflow,
                            stage=WorkflowStage.OPENFOAM_STEP_STARTED,
                            state=WorkflowEventState.STARTED,
                            attempt=attempt_number,
                            step_id=step.step_id,
                            detail="typed OpenFOAM command started",
                            occurred_at=step.started_at,
                        )
                        _record_event(
                            workflow,
                            stage=WorkflowStage.OPENFOAM_STEP_COMPLETE,
                            state=(
                                WorkflowEventState.FAILED
                                if step.timed_out
                                or step.return_code != 0
                                else WorkflowEventState.COMPLETED
                            ),
                            attempt=attempt_number,
                            step_id=step.step_id,
                            detail=(
                                f"return_code={step.return_code}; "
                                f"timed_out={step.timed_out}"
                            ),
                            evidence_paths=[
                                step.stdout_path.relative_to(
                                    run_dir
                                ).as_posix(),
                                step.stderr_path.relative_to(
                                    run_dir
                                ).as_posix(),
                            ],
                            occurred_at=step.finished_at,
                        )
                run_facts = (
                    EvidenceExtractorRegistry.first_party()
                    .resolve(
                        self.runtime_config.distribution,
                        self.runtime_config.version,
                    )
                    .extract(run_result, active_plan, case_root)
                )
                _write_json(attempt_root / "run-facts.json", run_facts)
                _record_event(
                    workflow,
                    stage=WorkflowStage.EXTRACTING_EVIDENCE,
                    state=WorkflowEventState.COMPLETED,
                    attempt=attempt_number,
                    evidence_paths=[
                        f"attempt-{attempt_number:02d}/run-facts.json"
                    ],
                )
                mesh_quality = mesh_quality_from_run_facts(
                    run_facts,
                    task.mesh,
                    case_root,
                )
                if (
                    task.mesh is not None
                    and task.mesh.strategy == "provided"
                    and len(executed_mesh_facts) == 1
                ):
                    probe_metrics = executed_mesh_facts[0].metrics
                    mesh_quality = mesh_quality.model_copy(
                        update={
                            "commands_completed": tuple(
                                dict.fromkeys(
                                    [
                                        *probe_metrics.commands_completed,
                                        *mesh_quality.commands_completed,
                                    ]
                                )
                            ),
                            "check_mesh_passed": (
                                probe_metrics.check_mesh_passed
                            ),
                            "cells": probe_metrics.cells,
                            "faces": probe_metrics.faces,
                            "points": probe_metrics.points,
                            "regions": probe_metrics.regions,
                            "max_non_orthogonality": (
                                probe_metrics.max_non_orthogonality
                            ),
                            "max_skewness": probe_metrics.max_skewness,
                            "negative_volume_count": (
                                probe_metrics.negative_volume_count
                            ),
                            "failed_requirements": (
                                probe_metrics.failed_requirements
                            ),
                            "warnings": tuple(
                                dict.fromkeys(
                                    [
                                        *probe_metrics.warnings,
                                        *mesh_quality.warnings,
                                    ]
                                )
                            ),
                            "evidence_files": tuple(
                                dict.fromkeys(
                                    [
                                        *probe_metrics.evidence_files,
                                        *mesh_quality.evidence_files,
                                    ]
                                )
                            ),
                        }
                    )
                _write_json(
                    attempt_root / "mesh-quality-report.json",
                    mesh_quality,
                )
                if (
                    derived_cache_store is not None
                    and mesh_key_result is not None
                    and mesh_key_result.cacheable
                    and mesh_key_result.key is not None
                    and (
                        mesh_lookup is None
                        or mesh_lookup.status != "hit"
                    )
                ):
                    stored = derived_cache_store.store_mesh(
                        mesh_key_result.key,
                        case_root=case_root,
                        mesh_quality=mesh_quality,
                        plan=active_plan,
                        source_run_id=run_dir.name,
                        source_attempt=attempt_number,
                    )
                    if stored:
                        cache_record_path = attempt_root / "mesh-cache.json"
                        cache_record = (
                            _read_json(cache_record_path)
                            if cache_record_path.is_file()
                            else {"schema_version": 1}
                        )
                        cache_record["stored"] = True
                        _write_json(cache_record_path, cache_record)
                _record_event(
                    workflow,
                    stage=WorkflowStage.MESH_QUALITY_COMPLETE,
                    state=WorkflowEventState.COMPLETED,
                    attempt=attempt_number,
                    evidence_paths=[
                        (
                            f"attempt-{attempt_number:02d}/"
                            "mesh-quality-report.json"
                        )
                    ],
                )
                report = assess_native_run(
                    run_facts,
                    mesh_quality=(
                        mesh_quality if task.mesh is not None else None
                    ),
                )
                log_text = json.dumps(
                    run_facts.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            else:
                report = _inspection_validation_report(inspection)
                run_facts = RunFacts(
                    run_id=run_dir.name,
                    attempt=attempt_number,
                    plan_sha256=sha256(
                        json.dumps(
                            active_plan.model_dump(mode="json"),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    extractor_identities={
                        "static-inspection": "foampilot.inspection/1.0.0"
                    },
                    raw_steps=(),
                    source_sha256={},
                )
                _write_json(attempt_root / "run-facts.json", run_facts)
                _record_event(
                    workflow,
                    stage=WorkflowStage.EXTRACTING_EVIDENCE,
                    state=WorkflowEventState.COMPLETED,
                    attempt=attempt_number,
                    evidence_paths=[
                        f"attempt-{attempt_number:02d}/run-facts.json"
                    ],
                )
                log_text = json.dumps(
                    run_facts.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            _write_json(
                attempt_root / "run-assessment.json",
                report,
            )
            validation_checkpoint = workflow.checkpoint(
                f"run-assessment-attempt-{attempt_number:02d}",
                report,
            )
            _record_event(
                workflow,
                stage=WorkflowStage.RUN_ASSESSED,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    (
                        f"attempt-{attempt_number:02d}/"
                        "run-assessment.json"
                    ),
                    validation_checkpoint.relative_to(
                        run_dir
                    ).as_posix(),
                ],
            )
            try:
                public_results = contract_pipeline.evaluate_after_evidence(
                    contracts=planning_contracts,
                    run_facts=run_facts,
                    case_root=case_root,
                    attempt_root=attempt_root,
                    attempt_number=attempt_number,
                )
            except ContractStageError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.failure.code,
                    attempts=attempts,
                    message=f"Result processing failed: {error}",
                    model_calls=model_calls,
                    primary_failure=error.failure,
                )
            status = _status_for_assessment(report)
            if report.ok and public_results.report.verdict in {
                "PASS",
                "NOT_REQUESTED",
            }:
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
                    message=(
                        "Case authoring and deterministic checks completed; "
                        "no solver was run."
                        if "CASE_AUTHORING_CHECKS_PASSED"
                        in report.reason_codes
                        else (
                            "Native execution completed and explicit "
                            "acceptance conditions passed."
                            if public_results.report.verdict == "PASS"
                            else (
                                "Native execution completed; no explicit "
                                "acceptance conditions were requested."
                            )
                        )
                    ),
                    model_calls=model_calls,
                )
            if report.ok:
                attempts.append(
                    AttemptSummary(
                        attempt=attempt_number,
                        status=(
                            "ACCEPTANCE_FAILED"
                            if public_results.report.verdict == "FAIL"
                            else "ACCEPTANCE_INCOMPLETE"
                        ),
                    )
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=(
                        "ACCEPTANCE_FAILED"
                        if public_results.report.verdict == "FAIL"
                        else "ACCEPTANCE_INCOMPLETE"
                    ),
                    attempts=attempts,
                    message=(
                        "Explicit acceptance conditions failed or could not "
                        "be evaluated; see result-report.json."
                    ),
                    model_calls=model_calls,
                    primary_failure=FailureRecord(
                        domain=FailureDomain.VALIDATION,
                        code=(
                            "ACCEPTANCE_FAILED"
                            if public_results.report.verdict == "FAIL"
                            else "ACCEPTANCE_EVIDENCE_INCOMPLETE"
                        ),
                        detail=(
                            "; ".join(
                                item.detail
                                for item in public_results.report.conditions
                                if item.status != "PASS"
                            )
                            or "explicit acceptance evidence is incomplete"
                        ),
                        message="显式验收条件未通过或证据不足。",
                        recovery="查看 result-report.json 的条件状态与缺失证据。",
                        evidence_paths=["result-report.json"],
                    ),
                )

            fingerprint = failure_fingerprint(
                report,
                run_facts=run_facts,
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

            try:
                classification = classify_native_failure(
                    report=report,
                    plan=active_plan,
                    run_facts=run_facts,
                    inspection=(inspection if not inspection.passed else None),
                )
            except FailureClassificationError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_failure_record(
                        status=status,
                        message=report.detail,
                        attempts=attempts,
                    ),
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.WORKFLOW,
                        code=error.code,
                        detail=error.detail,
                        message=error.message,
                        recovery=error.recovery,
                    ),
                )
            classification_name = (
                f"failure-classification-attempt-{attempt_number:02d}.json"
            )
            _write_json(run_dir / classification_name, classification)
            classified_failure = FailureRecord(
                domain=classification.domain,
                code=classification.code,
                step_id=classification.failed_step_id,
                detail=report.detail,
                evidence_paths=[classification_name],
            )

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

            if verified_source is not None:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=(
                        "Verified-plan execution failed; warm plan reuse "
                        "does not invoke a repair model."
                    ),
                    model_calls=model_calls,
                )

            current_files = _read_declared_files(case_root, active_plan)
            assert case_design is not None
            eligible, ineligible_reason = _automatic_repair_eligible(
                classification,
                case_design,
            )
            if not eligible:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=(
                        "Automatic repair is not authorized: "
                        + ineligible_reason
                    ),
                    model_calls=model_calls,
                    primary_failure=classified_failure,
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.DESIGN,
                        code="AUTOMATIC_REPAIR_NOT_AUTHORIZED",
                        detail=ineligible_reason,
                        message="当前失败不属于已冻结的自动数值修复范围。",
                        recovery=(
                            "补充或确认所需物理/能力信息后重新设计；"
                            "不要让 repair 模型临时修改命令或物理模型。"
                        ),
                        evidence_paths=[classification_name],
                    ),
                )
            policy = _repair_policy(execution_task)
            if not policy.automatic_numerical_repair:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message="Automatic numerical repair is disabled.",
                    model_calls=model_calls,
                    primary_failure=classified_failure,
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.DESIGN,
                        code="AUTOMATIC_NUMERICAL_REPAIR_DISABLED",
                        detail="task repair policy disables automatic numerical repair",
                        message="任务按配置未执行自动数值修复。",
                        recovery="调整 repair_policy 后以新任务重新求解。",
                    ),
                )
            try:
                if self.gateway is None:
                    raise ValueError("repair requires a model gateway")
                repair_context = self._context(
                    task,
                    capability,
                    repair=True,
                    repair_evidence=(
                        report.detail + "\n" + log_text
                    ),
                    geometry_facts=geometry_facts,
                )
                _write_json(
                    attempt_root / "repair-agent-context.json",
                    {
                        "knowledge_slots": (
                            repair_context.knowledge_slots
                        ),
                        "missing_slots": repair_context.missing_slots,
                        "selected_knowledge_ids": (
                            repair_context.selected_knowledge_ids
                        ),
                        "selected_source_hashes": (
                            repair_context.selected_source_hashes
                        ),
                        "skill_names": repair_context.skill_names,
                    },
                )
                repair_scope = build_repair_scope(
                    classification=classification,
                    task=execution_task,
                    plan=active_plan,
                    current_files=current_files,
                    knowledge_ids=(
                        repair_context.selected_knowledge_ids
                    ),
                )
                scope_name = (
                    f"repair-scope-attempt-{attempt_number:02d}.json"
                )
                _write_json(run_dir / scope_name, repair_scope)
                _record_event(
                    workflow,
                    stage=WorkflowStage.REPAIR_SCOPE_READY,
                    state=WorkflowEventState.COMPLETED,
                    attempt=attempt_number,
                    evidence_paths=[classification_name, scope_name],
                )
                workflow.checkpoint(
                    f"repair-evidence-attempt-{attempt_number:02d}",
                    {
                        "failed_step_id": report.failed_step_id,
                        "failure_fingerprint": fingerprint,
                        "run_assessment_path": (
                            f"attempt-{attempt_number:02d}/"
                            "run-assessment.json"
                        ),
                        "run_facts_path": (
                            f"attempt-{attempt_number:02d}/run-facts.json"
                        ),
                    },
                )
                _record_event(
                    workflow,
                    stage=WorkflowStage.MODEL_REPAIR_STARTED,
                    state=WorkflowEventState.STARTED,
                    attempt=attempt_number,
                )
                repair_status = build_agent_status_snapshot(
                    decision_stage=AgentDecisionStage.REPAIR,
                    task=execution_task,
                    capability=capability,
                    context=repair_context,
                    workflow=workflow,
                    model_budget=model_ledger,
                    logical_requests_used=(
                        lineage_logical_requests_before_run + model_calls
                    ),
                    logical_request_limit=logical_request_limit,
                    current_attempt=attempt_number,
                    execution_seconds_used=execution_seconds_used,
                    plan=active_plan,
                    latest_failure=classified_failure,
                    allowed_actions=list(
                        dict.fromkeys(
                            operation.replace("insert_command_before", "insert_command")
                            .replace("insert_command_after", "insert_command")
                            for operation in repair_scope.allowed_operations
                        )
                    ),
                )
                repair_status_artifact = _write_status_artifact(
                    run_dir=run_dir,
                    name=f"agent-status-repair-{attempt_number:02d}.json",
                    snapshot=repair_status,
                )
                model_calls += 1
                proposal = request_repair_proposal(
                    task=execution_task,
                    plan=active_plan,
                    classification=classification,
                    repair_scope=repair_scope,
                    run_facts=run_facts,
                    knowledge_text=repair_context.knowledge_text,
                    skills_text=repair_context.skills_text,
                    geometry_facts=geometry_facts,
                    mesh_quality_report=mesh_quality,
                    status_snapshot=repair_status,
                    status_artifact=repair_status_artifact,
                    gateway=self.gateway,
                    budget=REPAIR_MODEL_POLICY.open(
                        model_ledger,
                        ModelStage.REPAIR,
                    ),
                    trace=model_trace,
                )
                authorization = authorize_repair(
                    proposal=proposal,
                    design=case_design,
                    policy=policy,
                )
                if authorization.state != "AUTHORIZED_AUTOMATIC":
                    raise ValueError(
                        "REPAIR_NOT_AUTOMATICALLY_AUTHORIZED: "
                        + ", ".join(authorization.reason_codes)
                    )
                repaired = apply_authorized_repair(
                    proposal=proposal,
                    authorization=authorization,
                    design=case_design,
                    bundle=_bundle_from_plan(active_plan),
                    mesh_facts=input_mesh_facts,
                    extensions=selected_registry,
                    public_asset_install_paths=tuple(
                        item.install_path
                        if item.kind == "directory"
                        else item.path
                        for item in execution_task.public_assets
                        if (
                            item.install_path
                            if item.kind == "directory"
                            else item.path
                        )
                        is not None
                    ),
                    protected_paths=tuple(execution_task.protected_paths),
                )
                case_design = repaired.design
                active_plan = compile_execution_plan(
                    design=case_design,
                    bundle=repaired.bundle,
                    environment=environment,
                    task=execution_task,
                    registry=selected_registry,
                    observation_plan=observation_plan,
                )
            except OperationCancelled:
                return self._finish_cancelled(
                    run_dir=run_dir,
                    task=task,
                    attempts=attempts,
                    model_calls=model_calls,
                    stage="repair",
                )
            except RepairScopeError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=classified_failure,
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.WORKFLOW,
                        code=error.code,
                        detail=error.detail,
                        message=error.message,
                        recovery=error.recovery,
                    ),
                )
            except AgentStatusError as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=error.code,
                    attempts=attempts,
                    message=error.message,
                    model_calls=model_calls,
                    workflow_state=WorkflowState.FAILED,
                    primary_failure=_agent_status_failure(error),
                )
            except GatewayRequestError as error:
                can_resume = (
                    error.failure.retryable
                    and continuation_index < 2
                    and model_ledger.transport_attempts_remaining > 0
                )
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=f"Model transport is unavailable: {error}",
                    model_calls=model_calls,
                    workflow_state=WorkflowState.DEFERRED,
                    primary_failure=classified_failure,
                    terminal_blocker=_backend_blocker(error),
                    resume=ResumeMetadata(
                        allowed=can_resume,
                        from_stage=(
                            WorkflowStage.MODEL_REPAIR_STARTED
                            if can_resume
                            else None
                        ),
                        reason=(
                            "retryable backend failure during repair"
                            if can_resume
                            else "continuation or transport budget exhausted"
                        ),
                    ),
                )
            except LineageBudgetExhausted as error:
                return self._finish(
                    run_dir=run_dir,
                    task=task,
                    status=status,
                    attempts=attempts,
                    message=str(error),
                    model_calls=model_calls,
                    primary_failure=classified_failure,
                    terminal_blocker=FailureRecord(
                        domain=FailureDomain.BACKEND,
                        code="LINEAGE_TRANSPORT_BUDGET_EXHAUSTED",
                        detail=str(error),
                    ),
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
            patch_name = f"repair-proposal-attempt-{attempt_number:02d}.json"
            derived_name = (
                f"derived-case-design-attempt-{attempt_number:02d}.json"
            )
            _write_json(run_dir / patch_name, proposal)
            _write_json(run_dir / derived_name, repaired.derived)
            _write_json(
                run_dir
                / f"design-conformance-attempt-{attempt_number:02d}.json",
                repaired.conformance,
            )
            attempt_summary.changed_files = [
                item.path for item in proposal.file_operations
            ]
            pending_repair_changes = RepairChangeSet(
                changed_file_paths=tuple(attempt_summary.changed_files),
                changed_files=tuple(
                    GeneratedFile(path=item.path, content=item.content)
                    for item in proposal.file_operations
                ),
            )
            pending_repair_source_attempt = attempt_root
            workflow.checkpoint(
                f"active-plan-attempt-{attempt_number + 1:02d}",
                active_plan,
            )
            _record_event(
                workflow,
                stage=WorkflowStage.REPAIR_APPLIED,
                state=WorkflowEventState.COMPLETED,
                attempt=attempt_number,
                evidence_paths=[
                    patch_name,
                    derived_name,
                    (
                        "checkpoints/active-plan-attempt-"
                        f"{attempt_number + 1:02d}.json"
                    ),
                ],
            )

        raise AssertionError("attempt loop exhausted without terminal result")
