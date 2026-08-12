# Phase 4 Thin Coordinator, Run Facts, and Failure Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic native orchestrator and duplicate log parsing with a domain-free coordinator, one authoritative `RunFacts`, structured failure reports, and a shared CLI/Desktop projection.

**Architecture:** Extract pure stage services from `NativeAgent.solve()` and let `WorkflowCoordinator` execute a declarative transition table. Runner returns raw process evidence only; registered evidence extractors parse each output once into a canonical `RunFacts` artifact. Reports, repair, validation, CLI, and Desktop consume projections derived from those facts, while high-frequency metrics use a bounded separate stream.

**Tech Stack:** Python 3.12, Pydantic 2, JSONL, existing workflow/artifact/job modules, PySide6-Essentials 6, pytest 8.

## Global Constraints

- `WorkflowCoordinator` may contain state transitions and service calls only; no OpenFOAM tokens, solver names, mesh formats, field names, or physics decisions.
- Runner returns process identity, argv, timings, return code, cancellation, and log paths; it never reports convergence or CFD meaning.
- Exactly one registered Evidence Extractor interprets each raw evidence kind.
- Mesh quality, normal completion, solver time, residual, continuity, Courant, and native error facts come from `RunFacts`.
- No validation, repair, Desktop, or qualification module may parse raw solver/checkMesh logs.
- Main workflow JSONL remains low frequency. Metrics use a separate bounded stream and never determine workflow success by themselves.
- Every terminal failure writes a deterministic `FailureReport` before optional model diagnosis.
- Observations, confirmed causes, and hypotheses are distinct typed collections.
- Model diagnosis defaults on, is labeled `hypothesis`, cannot change terminal state, and cannot block finalization.
- Disabling automatic numerical repair produces a normal failed terminal state and a full report.
- Existing cancellation, orphan recovery, detached jobs, immutable manifests, and lineage semantics remain valid.
- Use TDD and commit after every task.

---

## File Structure

- `src/foampilot/evidence/models.py`: raw evidence identities and canonical `RunFacts`.
- `src/foampilot/evidence/extractors.py`: registry and one-pass extraction.
- `src/foampilot/evidence/openfoam10.py`: Foundation v10 log/result extractor.
- `src/foampilot/evidence/metrics.py`: bounded metrics JSONL writer/reader and aggregation.
- `src/foampilot/reporting/failure.py`: deterministic `FailureReport` construction.
- `src/foampilot/reporting/model_diagnostic.py`: optional labeled diagnostic append.
- `src/foampilot/workflow/coordinator.py`: transition table and stage execution.
- `src/foampilot/workflow/services.py`: protocols for ingest, intent, design, author, verify, execute, extract, postprocess, evaluate, repair.
- `src/foampilot/workflow/projection.py`: shared `WorkflowProjection`.
- `src/foampilot/agent/native_orchestrator.py`: compatibility facade delegating to coordinator; later reduced below 300 lines.

### Task 1: Define raw evidence and canonical `RunFacts`

**Files:**
- Create: `src/foampilot/evidence/__init__.py`
- Create: `src/foampilot/evidence/models.py`
- Test: `tests/test_run_facts.py`

**Interfaces:**
- Produces: `RawCommandEvidence`, `MeshCheckFact`, `SolverProgressFact`, `ResidualFact`, `ContinuityFact`, `CourantFact`, `NativeErrorFact`, `RunFacts`.
- `RunFacts` records source hashes and extractor identities.

- [ ] **Step 1: Write failing fact-integrity tests**

```python
def test_run_facts_reject_duplicate_step_ids() -> None:
    step = raw_step("solve")
    with pytest.raises(ValidationError):
        RunFacts(raw_steps=(step, step), **fact_defaults())


def test_hypotheses_are_not_confirmed_causes() -> None:
    assert "hypotheses" not in RunFacts.model_fields
```

Also test monotonic elapsed seconds, log SHA256 validation, solver-time ordering, residual numeric bounds, optional legacy absence, and immutable schema.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_run_facts.py -q -p no:cacheprovider`.

Expected: FAIL because `foampilot.evidence` is absent.

- [ ] **Step 3: Implement frozen fact models**

```python
class RunFacts(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    attempt: int
    plan_sha256: str
    extractor_identities: dict[str, str]
    raw_steps: tuple[RawCommandEvidence, ...]
    mesh_checks: tuple[MeshCheckFact, ...] = ()
    solver_progress: tuple[SolverProgressFact, ...] = ()
    residuals: tuple[ResidualFact, ...] = ()
    continuity: tuple[ContinuityFact, ...] = ()
    courant: tuple[CourantFact, ...] = ()
    native_errors: tuple[NativeErrorFact, ...] = ()
    written_times: tuple[float, ...] = ()
    output_files: tuple[str, ...] = ()
    source_sha256: dict[str, str]
```

Facts contain observations only, never pass/fail conclusions.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/evidence tests/test_run_facts.py
git commit -m "feat: add canonical run facts"
```

### Task 2: Implement the one-pass Foundation v10 evidence extractor

**Files:**
- Create: `src/foampilot/evidence/extractors.py`
- Create: `src/foampilot/evidence/openfoam10.py`
- Modify: `src/foampilot/evidence/__init__.py`
- Modify: `src/foampilot/preprocessing/mesh_probe.py`
- Create: `tests/fixtures/evidence/openfoam10/*.log`
- Create: `tests/test_openfoam10_evidence_extractor.py`
- Modify: `tests/test_mesh_probe.py`

**Interfaces:**
- Produces: `EvidenceExtractor.extract(run_result, plan, case_root) -> RunFacts`.
- Produces: `EvidenceExtractorRegistry.resolve(distribution, version) -> EvidenceExtractor`.

- [ ] **Step 1: Write failing replay tests**

```python
def test_extractor_normalizes_absolute_check_mesh_command() -> None:
    facts = extract_fixture("absolute-checkmesh.log")
    assert facts.mesh_checks[0].executed is True
    assert facts.mesh_checks[0].mesh_ok is True


def test_extractor_reports_residual_continuity_and_failure_once() -> None:
    facts = extract_fixture("diverging-piso.log")
    assert facts.residuals[-1].field == "p"
    assert facts.continuity[-1].cumulative is not None
    assert facts.native_errors[0].code == "FLOATING_POINT_EXCEPTION"
```

Cover normal End, no End, NaN/Inf, segmentation fault, compressed log input rejection, truncated final line, multiple regions, and exact source hashes.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_openfoam10_evidence_extractor.py -q -p no:cacheprovider`.

Expected: FAIL because extractor is absent.

- [ ] **Step 3: Implement one streaming parse pass per log**

Open each registered log once, update all parser states in the same line loop, and emit immutable facts at EOF. Identify command semantics through `NativeCommand.stage` and canonical executable identity, never string equality against the displayed argv. Bound a single log read to the run budget and preserve `parse_truncated` warning rather than silently declaring success. Move the Phase 1 pre-authoring `MeshCheckExtractor` implementation into this registry and make `mesh_probe` delegate to it, so pre-authoring and formal execution share one parser.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/evidence src/foampilot/preprocessing/mesh_probe.py \
  tests/fixtures/evidence tests/test_openfoam10_evidence_extractor.py \
  tests/test_mesh_probe.py
git commit -m "feat: extract OpenFOAM evidence once"
```

### Task 3: Move high-frequency solver metrics out of workflow events

**Files:**
- Create: `src/foampilot/evidence/metrics.py`
- Modify: `src/foampilot/activity/reporter.py`
- Modify: `src/foampilot/activity/sinks.py`
- Modify: `src/foampilot/runtime/telemetry.py`
- Test: `tests/test_metrics_stream.py`
- Test: `tests/test_activity.py`
- Test: `tests/test_runtime_telemetry.py`

**Interfaces:**
- Produces: `MetricsWriter(path, sample_interval_seconds, max_points_per_series)`.
- Produces: `MetricsProjection.recent(series, limit) -> tuple[MetricPoint, ...]`.
- Main workflow emits at most one stage heartbeat per configured interval, not one event per residual.

- [ ] **Step 1: Write failing bounded-volume tests**

```python
def test_ten_thousand_residuals_do_not_flood_workflow(tmp_path: Path) -> None:
    feed_solver_lines(10_000, reporter, metrics)
    assert count_jsonl(tmp_path / "workflow-events.jsonl") < 100
    assert metrics.series_count("residual:p") <= 500


def test_metrics_are_non_authoritative() -> None:
    projection = MetricsProjection.from_file(corrupted_metrics_path)
    assert projection.warnings
    assert projection.workflow_state is None
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_metrics_stream.py tests/test_activity.py tests/test_runtime_telemetry.py -q -p no:cacheprovider`.

Expected: FAIL because metrics currently share activity events.

- [ ] **Step 3: Implement bounded metrics storage**

Write `metrics.jsonl` with sequence, occurred_at, attempt, step_id, simulation_time, series, and value. Downsample deterministically by time bucket for live projection while raw solver logs remain immutable evidence. Corruption produces warnings and never changes summary status.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/evidence/metrics.py src/foampilot/activity src/foampilot/runtime/telemetry.py tests/test_metrics_stream.py tests/test_activity.py tests/test_runtime_telemetry.py
git commit -m "feat: separate solver metrics from workflow"
```

### Task 4: Build deterministic `FailureReport`

**Files:**
- Create: `src/foampilot/reporting/__init__.py`
- Create: `src/foampilot/reporting/failure.py`
- Modify: `src/foampilot/agent/failure.py`
- Test: `tests/test_failure_report.py`
- Test: `tests/test_failure_classifier.py`

**Interfaces:**
- Produces: `FailureObservation`, `ConfirmedCause`, `FailureHypothesis`, `RepairDisposition`, `FailureReport`.
- Produces: `build_failure_report(run_facts, classification, repair_decision, progress, artifacts) -> FailureReport`.

- [ ] **Step 1: Write failing evidence-separation tests**

```python
def test_divergence_report_does_not_promote_hypothesis_to_cause() -> None:
    report = build_failure_report(diverging_facts(), low_confidence_classification(), ...)
    assert report.observations[0].code == "COURANT_GROWTH"
    assert report.confirmed_causes == ()
    assert report.hypotheses[0].label == "hypothesis"


def test_disabled_repair_reason_is_explicit() -> None:
    report = build_failure_report(..., repair_decision=disabled_decision())
    assert report.automatic_repair.reason == "disabled_by_user"
```

Also test failed stage/step/attempt, evidence paths, completed progress, preserved artifacts, environment/backend/cancel distinctions, unknown cause, and Chinese actionable recommendations.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_failure_report.py tests/test_failure_classifier.py -q -p no:cacheprovider`.

Expected: FAIL because structured report is absent.

- [ ] **Step 3: Implement deterministic report assembly**

```python
class FailureReport(StrictModel):
    schema_version: Literal[1] = 1
    failure_layer: str
    failure_code: str
    failed_stage: str
    failed_attempt: int | None
    failed_step_id: str | None
    observations: tuple[FailureObservation, ...]
    confirmed_causes: tuple[ConfirmedCause, ...]
    hypotheses: tuple[FailureHypothesis, ...]
    automatic_repair: RepairDisposition
    completed_progress: tuple[str, ...]
    preserved_artifacts: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    model_diagnostic: ModelDiagnostic | None = None
```

Require evidence for confirmed causes; permit an empty cause list. Never derive cause confidence from a model response.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/reporting src/foampilot/agent/failure.py tests/test_failure_report.py tests/test_failure_classifier.py
git commit -m "feat: generate evidence-layered failure reports"
```

### Task 5: Add optional labeled model diagnostics

**Files:**
- Create: `src/foampilot/reporting/model_diagnostic.py`
- Modify: `src/foampilot/reporting/__init__.py`
- Modify: `src/foampilot/models/budgets.py`
- Test: `tests/test_model_failure_diagnostic.py`

**Interfaces:**
- Produces: `append_model_diagnostic(report, public_evidence, gateway, budget, trace) -> FailureReport`.
- Adds `ModelStage.FAILURE_DIAGNOSTIC`.

- [ ] **Step 1: Write failing non-authoritative behavior tests**

```python
def test_model_diagnostic_is_labeled_hypothesis() -> None:
    report = append_model_diagnostic(base_report(), facts(), gateway, budget, trace)
    assert report.model_diagnostic.label == "hypothesis"
    assert report.failure_code == base_report().failure_code


def test_backend_failure_returns_the_complete_base_report() -> None:
    report = append_model_diagnostic(base_report(), facts(), failing_gateway, budget, trace)
    assert report.model_diagnostic.status == "unavailable"
    assert report.observations == base_report().observations
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_model_failure_diagnostic.py -q -p no:cacheprovider`.

Expected: FAIL because the diagnostic is absent.

- [ ] **Step 3: Implement a bounded post-terminal advisory call**

Send only sanitized `FailureReport`, selected compact `RunFacts`, and registered public error knowledge. Schema forbids confirmed cause and terminal-state fields. Catch all backend/model errors, write unavailable status, and finalize normally.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/reporting src/foampilot/models/budgets.py tests/test_model_failure_diagnostic.py
git commit -m "feat: add optional model failure hypotheses"
```

### Task 6: Introduce stage-service protocols and a domain-free coordinator

**Files:**
- Create: `src/foampilot/workflow/services.py`
- Create: `src/foampilot/workflow/coordinator.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/workflow/events.py`
- Test: `tests/test_workflow_coordinator.py`
- Test: `tests/test_import_boundary.py`

**Interfaces:**
- Produces: `StageService.run(context) -> StageOutcome`.
- Produces: `WorkflowCoordinator.run(context, services) -> NativeAgentOutcome`.
- Produces states from the approved INGESTING_ASSETS through terminal sequence.

- [ ] **Step 1: Write failing transition and source-boundary tests**

```python
def test_coordinator_runs_declared_stages_in_order() -> None:
    outcome = WorkflowCoordinator(services).run(context)
    assert services.calls == EXPECTED_STAGE_ORDER
    assert outcome.summary.workflow_state == "COMPLETED"


def test_coordinator_source_contains_no_domain_tokens() -> None:
    source = Path("src/foampilot/workflow/coordinator.py").read_text()
    for forbidden in ("checkMesh", "pisoFoam", "residual", "polyMesh", "Courant"):
        assert forbidden not in source
```

Test pending information, confirmation, capability failure, cancellation at each stage, exception normalization, checkpoint-before-transition, repair loop budget, and terminal report persistence.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_workflow_coordinator.py tests/test_import_boundary.py -q -p no:cacheprovider`.

Expected: FAIL because coordinator/services are absent.

- [ ] **Step 3: Implement transition table and service isolation**

Define ordered stage descriptors with input artifact names, output artifact names, resumability, and failure normalization. `StageOutcome` is `completed`, `deferred`, `failed`, or `cancelled`; only the coordinator writes workflow events and summary transition state. Services write domain artifacts through an injected artifact sink but do not mutate WorkflowStore.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/workflow tests/test_workflow_coordinator.py tests/test_import_boundary.py
git commit -m "feat: add domain-free workflow coordinator"
```

### Task 7: Migrate `NativeAgent` and all validators to `RunFacts`

**Files:**
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/agent/failure.py`
- Modify: `src/foampilot/preprocessing/mesh_quality.py`
- Modify: `src/foampilot/validation/native.py`
- Modify: `src/foampilot/qualification/validators.py`
- Modify: `src/foampilot/qualification/runner.py`
- Modify: `src/foampilot/agent/repair_scope.py`
- Test: `tests/test_native_agent_state_machine.py`
- Test: `tests/test_mesh_quality_report.py`
- Test: `tests/test_native_validation.py`
- Test: `tests/test_qualification_reporting.py`
- Test: `tests/test_import_boundary.py`

**Interfaces:**
- `NativeAgent.solve()` becomes a facade that constructs services/context and delegates to `WorkflowCoordinator`.
- Validation/classification signatures accept `RunFacts`, not log text or `PlanRunResult`.

- [ ] **Step 1: Add failing no-duplicate-parser tests**

```python
def test_validation_accepts_run_facts_not_log_text() -> None:
    assert list(inspect.signature(validate_native_run).parameters) == [
        "task", "run_facts", "case_root",
    ]


def test_only_evidence_package_contains_solver_log_patterns() -> None:
    violations = find_patterns_outside(
        "src/foampilot/evidence", ["Solving for", "Courant Number", "Mesh OK"]
    )
    assert violations == []
```

- [ ] **Step 2: Run migration tests and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_native_agent_state_machine.py tests/test_mesh_quality_report.py tests/test_native_validation.py tests/test_qualification_reporting.py tests/test_import_boundary.py -q -p no:cacheprovider`.

Expected: FAIL while duplicate parsers remain.

- [ ] **Step 3: Migrate consumers and reduce the orchestrator**

Move domain stage bodies into service implementations. Delete `_run_log` and all regex/log reads from validation, mesh quality, classifier, repair scope, and qualification. Preserve public output shapes through adapters where needed, but source every value from `RunFacts`. Reduce `native_orchestrator.py` to facade, dependency assembly, resume/rerun entrypoints, and compatibility helpers; target fewer than 300 lines.

- [ ] **Step 4: Run focused and full tests**

Run Step 2 and then the complete deterministic suite. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot tests/test_native_agent_state_machine.py tests/test_mesh_quality_report.py tests/test_native_validation.py tests/test_qualification_reporting.py tests/test_import_boundary.py
git commit -m "refactor: centralize native run evidence"
```

### Task 8: Provide one `WorkflowProjection` to CLI and Desktop

**Files:**
- Create: `src/foampilot/workflow/projection.py`
- Modify: `src/foampilot/workflow/__init__.py`
- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/desktop/viewmodels.py`
- Modify: `src/foampilot/desktop/repository.py`
- Modify: `src/foampilot/desktop/telemetry.py`
- Modify: `src/foampilot/desktop/main_window.py`
- Test: `tests/test_workflow_projection.py`
- Test: `tests/test_cli_progress.py`
- Test: `tests/test_desktop_repository.py`
- Test: `tests/test_desktop_main_window.py`

**Interfaces:**
- Produces: `build_workflow_projection(run_dir) -> WorkflowProjection`.
- Projection fields: `current_stage`, `stage_progress`, `active_operation`, `latest_solver_time`, `recent_residuals`, `pending_questions`, `failure_summary`, `artifact_links`, `warnings`.

- [ ] **Step 1: Write failing CLI/Desktop parity tests**

```python
def test_cli_and_desktop_use_identical_projection(run_dir: Path) -> None:
    expected = build_workflow_projection(run_dir)
    assert cli_progress_payload(run_dir) == expected.model_dump(mode="json")
    assert desktop_repository.open(run_dir).projection == expected


def test_desktop_repository_does_not_construct_residual_log_cursor() -> None:
    source = Path("src/foampilot/desktop/repository.py").read_text()
    assert "ResidualLogCursor" not in source
```

Also test incomplete/corrupt metrics, pending questions, failed reports, cancelled jobs, legacy read-only runs, and manifest warnings.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_workflow_projection.py tests/test_cli_progress.py tests/test_desktop_repository.py tests/test_desktop_main_window.py -q -p no:cacheprovider`.

Expected: FAIL because consumers currently project independently.

- [ ] **Step 3: Implement projection and remove Desktop log parsing**

Projection reads summary, low-frequency workflow events, metrics projection, pending questions, failure/result report, and manifested artifact links. It never reads native logs. Update CLI and Desktop to render this exact model; preserve old-run read-only adapters with warnings.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/workflow src/foampilot/cli/main.py src/foampilot/desktop tests/test_workflow_projection.py tests/test_cli_progress.py tests/test_desktop_repository.py tests/test_desktop_main_window.py
git commit -m "feat: unify CLI and Desktop workflow projection"
```

### Task 9: Harden terminal persistence and real failure visibility

**Files:**
- Modify: `src/foampilot/jobs/worker.py`
- Modify: `src/foampilot/jobs/recovery.py`
- Modify: `src/foampilot/jobs/store.py`
- Test: `tests/test_job_worker.py`
- Test: `tests/test_job_recovery.py`
- Create: `tests/test_real_failure_report_gate.py`

**Interfaces:**
- Guarantees status-write failures are best-effort persisted as `JOB_STATUS_WRITE_FAILED` evidence.
- A legitimate pre-run `USER_CANCELLED` without a solve run reconciles as cancellation, not damage.
- Real failure gate exposes report and projection after numerical failure with repair disabled.

- [ ] **Step 1: Add failing fault-injection and cancellation tests**

```python
def test_running_status_write_failure_does_not_leave_starting_orphan(monkeypatch) -> None:
    fail_once_on_running(monkeypatch)
    result = run_local_job(job)
    assert result != 0
    assert read_status(job).terminal_code == "JOB_STATUS_WRITE_FAILED"


def test_prerun_cancel_is_not_evidence_damage(job) -> None:
    cancel_before_worker_cli(job)
    decision = reconcile_job(job)
    assert decision.code == "USER_CANCELLED"
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_job_worker.py tests/test_job_recovery.py -q -p no:cacheprovider`.

Expected: the new fault and pre-run cancellation cases fail until hardened.

- [ ] **Step 3: Wrap all status transitions and classify legitimate no-run cancellation**

Guard STARTING, RUNNING, and terminal writes. On failure, append a fsynced emergency record and attempt one exclusive terminal update without recursion. Recovery recognizes a finalized `USER_CANCELLED` job without a run as a legitimate terminal path; failed solve-like jobs without runs remain evidence damage.

- [ ] **Step 4: Run deterministic, real, and full gates**

Run Step 2, `tests/test_real_failure_report_gate.py`, then the complete suite. Expected: PASS; the real gate shows stage, direct observations, uncertain causes, disabled repair reason, actions, and evidence paths in CLI and Desktop projection.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/jobs tests/test_job_worker.py tests/test_job_recovery.py tests/test_real_failure_report_gate.py
git commit -m "fix: preserve truthful terminal job evidence"
```

### Task 10: Publish Phase 4 architecture and remove obsolete telemetry code

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/system-overview.md`
- Modify: `docs/desktop-ide.md`
- Delete: `src/foampilot/desktop/cursors.py` if no legacy adapter requires it
- Modify: `tests/test_repository_docs.py`
- Modify: `tests/test_import_boundary.py`

**Interfaces:**
- Documents Coordinator, RunFacts, FailureReport, metrics stream, and WorkflowProjection.
- Architecture tests forbid duplicate parser patterns outside `foampilot.evidence`.

- [ ] **Step 1: Add failing docs and dead-code tests**

```python
def test_docs_define_observation_cause_hypothesis_layers() -> None:
    text = Path("docs/architecture.md").read_text()
    assert "观察事实 ≠ 确认原因 ≠ 推测原因" in text
    assert "WorkflowProjection" in text
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_repository_docs.py tests/test_import_boundary.py -q -p no:cacheprovider`.

Expected: FAIL until docs and boundaries update.

- [ ] **Step 3: Update docs and delete unused duplicate parsers**

Run `rg -n 'Solving for|Courant Number|Mesh OK|ResidualLogCursor' src/foampilot` and retain matches only in evidence extractors or explicitly labeled legacy read-only adapters. Remove obsolete activity-event residual rendering claims.

- [ ] **Step 4: Run Phase 4 release gates**

Run `git diff --check` and the complete deterministic suite. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A src/foampilot/desktop AGENTS.md README.md docs/architecture.md docs/system-overview.md docs/desktop-ide.md tests/test_repository_docs.py tests/test_import_boundary.py
git commit -m "docs: publish evidence-driven workflow"
```
