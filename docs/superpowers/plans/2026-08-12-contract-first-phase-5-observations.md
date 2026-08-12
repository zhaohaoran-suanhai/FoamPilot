# Phase 5 Observation Planning, Post-processing, and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn user-requested outputs and acceptance language into a pre-solve evidence plan, compute generic CFD metrics after execution, and evaluate only explicit, executable acceptance conditions without coupling validation logic to case authoring.

**Architecture:** `AcceptanceCompiler` first turns verified acceptance intent into safe typed conditions. `ObservationPlanner` then consumes frozen intent/design, those conditions, and capability descriptors before case authoring. It first prefers evidence already available in native logs and written fields, then adds only allowlisted runtime or post-process collection steps when the requested history cannot otherwise be recovered. `PostProcessor` consumes `RunFacts` and immutable case outputs to produce `DerivedMetrics`; `AcceptanceEvaluator` consumes those metrics and typed conditions to produce `ResultReport`. No component re-parses raw solver logs or reads private evaluator assets.

**Tech Stack:** Python 3.12, Pydantic 2, Foundation OpenFOAM 10 post-processing commands, existing typed execution plan and artifact store, PySide6-Essentials 6, pytest 8.

## Global Constraints

- Observation planning happens before Case Author and is frozen into authoring and plan inputs.
- Prefer solver logs and required written fields; do not inject an optional function object merely to manufacture benchmark evidence.
- Runtime collection is allowed only when a requested time history cannot be recovered after the solve and the relevant extension declares an allowlisted Foundation v10 configuration.
- Post-processing commands use typed argv and the same executable, path, sandbox, timeout, and cancellation policy as solver commands.
- Never run shell snippets, arbitrary user expressions, or model-authored post-processing code.
- User text without a numerical operator, reference, and tolerance is an observation request, not a pass/fail condition.
- Inferred engineering thresholds are labeled recommendations and require per-field confirmation before becoming gates.
- Every metric carries unit/dimension semantics, scope, time selection, status, and source evidence references.
- Missing evidence produces `UNAVAILABLE`/`NOT_EVALUATED`; it never silently passes.
- `AcceptanceEvaluator` is deterministic and extension-driven. A model may explain results but cannot alter verdicts.
- Public workflow artifacts never expose evaluator-private reference values. The physical evaluator/wheel split remains a separately deferred packaging task and is not broadened by this roadmap.
- CLI and Desktop render the same `ResultReport`; neither implements metric or acceptance rules.
- First-party extensions are enabled. Third-party entry-point discovery stays disabled.
- Use TDD and commit after every task.

---

## File Structure

- `src/foampilot/observations/models.py`: observation requests, collection strategies, and frozen `ObservationPlan`.
- `src/foampilot/observations/registry.py`: first-party extension registry and capability descriptors.
- `src/foampilot/observations/planner.py`: deterministic evidence-feasibility and plan compilation.
- `src/foampilot/observations/openfoam10.py`: Foundation v10 collection fragments.
- `src/foampilot/postprocessing/models.py`: metric values, series, provenance, and `DerivedMetrics`.
- `src/foampilot/postprocessing/engine.py`: metric calculator registry and execution.
- `src/foampilot/postprocessing/openfoam10.py`: first-party CFD calculators/adapters.
- `src/foampilot/acceptance/models.py`: typed conditions and `ResultReport`.
- `src/foampilot/acceptance/compiler.py`: explicit acceptance text/facts to safe conditions.
- `src/foampilot/acceptance/evaluator.py`: deterministic condition evaluation.

### Task 1: Define observation contracts and a closed first-party registry

**Files:**
- Create: `src/foampilot/observations/__init__.py`
- Create: `src/foampilot/observations/models.py`
- Create: `src/foampilot/observations/registry.py`
- Test: `tests/test_observation_models.py`
- Test: `tests/test_observation_registry.py`

**Interfaces:**
- Produces: `ObservationRequest`, `EvidenceStrategy`, `ObservationItem`, `ObservationPlan`.
- Produces: `ObservationExtensionDescriptor`, `ObservationExtensionRegistry`.
- First-party kinds: `residual`, `continuity`, `flow_rate`, `pressure_difference`, `region_average`, `force`, `heat_flux`.

- [ ] **Step 1: Write failing strict-contract tests**

```python
def test_observation_plan_is_frozen_and_canonically_hashable() -> None:
    plan = ObservationPlan(
        schema_version=1,
        items=(observation_item("flow_rate"),),
        warnings=(),
    )
    assert plan.canonical_sha256() == plan.canonical_sha256()
    with pytest.raises(ValidationError):
        plan.items = ()


def test_unknown_observation_kind_is_rejected() -> None:
    registry = first_party_observation_registry()
    with pytest.raises(UnsupportedObservationError):
        registry.resolve("arbitrary_model_script")
```

Also test unique observation IDs, safe field/patch/zone identifiers, explicit time selection, dimensional metadata, JSON round-trip, closed first-party IDs, and disabled entry-point discovery.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_observation_models.py tests/test_observation_registry.py \
  -q -p no:cacheprovider
```

Expected: FAIL because `foampilot.observations` is absent.

- [ ] **Step 3: Implement frozen contracts and descriptor-only registry**

```python
class ObservationItem(StrictFrozenModel):
    observation_id: str
    kind: ObservationKind
    quantity: str
    scope: ObservationScope
    time_selection: TimeSelection
    evidence_strategy: EvidenceStrategy
    required_for_condition_ids: tuple[str, ...] = ()
    provenance: FactProvenance


class ObservationPlan(StrictFrozenModel):
    schema_version: Literal[1] = 1
    items: tuple[ObservationItem, ...]
    warnings: tuple[ObservationWarning, ...] = ()
```

Descriptors declare supported scopes, required fields, available evidence strategies, Foundation distribution/version support, and whether a runtime configuration fragment is necessary. They contain no executable Python import path supplied by the user or model.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/observations tests/test_observation_models.py \
  tests/test_observation_registry.py
git commit -m "feat: define observation contracts"
```

### Task 2: Compile evidence-feasible observations before authoring

**Files:**
- Create: `src/foampilot/observations/planner.py`
- Modify: `src/foampilot/simulation/intent.py`
- Modify: `src/foampilot/simulation/design.py`
- Modify: `src/foampilot/taskbuilder/compiler.py`
- Delete: `src/foampilot/taskbuilder/checks.py` after its callers are removed
- Test: `tests/test_observation_planner.py`
- Modify: `tests/test_task_compiler.py`

**Interfaces:**
- Produces initially: `ObservationPlanner.compile(intent, design, mesh_facts, registry, acceptance_plan=None) -> ObservationPlan`.
- Inputs are frozen contracts only; raw prompt access is prohibited.

- [ ] **Step 1: Write failing precedence and feasibility tests**

```python
def test_continuity_and_residuals_reuse_run_facts() -> None:
    plan = planner.compile(intent, design, mesh_facts, registry)
    assert item(plan, "continuity").evidence_strategy.kind == "run_facts"
    assert item(plan, "residual").evidence_strategy.kind == "run_facts"


def test_requested_flow_history_requires_collection_before_authoring() -> None:
    plan = planner.compile(
        intent_with("inlet and outlet flow history"),
        design,
        mesh_with_patches("inlet", "outlet"),
        registry,
    )
    flow = item(plan, "flow_rate")
    assert flow.evidence_strategy.kind in {
        "runtime_configuration",
        "written_field_postprocess",
    }
```

Also test unknown patch/zone references become blocking unresolved facts, final-only data does not force a time-history collector, duplicate requests are deduplicated, impossible evidence gets a precise warning, and absent tolerance remains observation-only.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_observation_planner.py tests/test_task_compiler.py -q -p no:cacheprovider`.

Expected: FAIL because planner is absent.

- [ ] **Step 3: Implement deterministic strategy precedence**

For every request, choose the first supported strategy in this order:

1. canonical `RunFacts`;
2. already-required written fields;
3. allowlisted post-process command on written fields;
4. allowlisted runtime collection, only for unrecoverable requested history;
5. unavailable with explicit reason and recovery.

Do not let the model choose the strategy. Validate every patch, zone, field, and region reference against `InputMeshFacts`/`CaseDesign`.

- [ ] **Step 4: Replace legacy `build_public_checks()` output at the boundary**

Remove the TaskSpec v3 taskbuilder's legacy `PublicCheck` production. The taskbuilder retains only `required_outputs` and verified `acceptance_intent`; `IntentInterpreter` and `AcceptanceCompiler` own the typed transformation. Keep the existing advisory that a requested metric without tolerance is observation-only. TaskSpec v2 remains run-report-only and must never compile a new plan. Add source-boundary tests proving no new workflow consumes `PublicCheck` directly.

- [ ] **Step 5: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/foampilot/observations src/foampilot/simulation/intent.py \
  src/foampilot/simulation/design.py \
  src/foampilot/taskbuilder/compiler.py src/foampilot/taskbuilder/checks.py \
  tests/test_observation_planner.py tests/test_task_compiler.py
git commit -m "feat: compile pre-solve observation plans"
```

### Task 3: Compile safe, explicit acceptance conditions

**Files:**
- Create: `src/foampilot/acceptance/__init__.py`
- Create: `src/foampilot/acceptance/models.py`
- Create: `src/foampilot/acceptance/compiler.py`
- Modify: `src/foampilot/observations/planner.py`
- Test: `tests/test_acceptance_compiler.py`
- Modify: `tests/test_observation_planner.py`

**Interfaces:**
- Produces: `AcceptanceCondition`, `AcceptancePlan`.
- Supported operators: `exists`, `finite`, `less_equal`, `greater_equal`, `between`, `relative_error`, `absolute_balance`.
- No arbitrary expression language.

- [ ] **Step 1: Write failing explicitness tests**

```python
def test_metric_without_limit_is_not_a_gate() -> None:
    compiled = compiler.compile(
        acceptance_text=("monitor porous-zone average velocity",),
        confirmed_facts=(),
    )
    assert compiled.conditions == ()
    assert compiled.observation_requests[0].kind == "region_average"


def test_explicit_continuity_limit_becomes_a_condition() -> None:
    compiled = compiler.compile(
        acceptance_text=("absolute cumulative continuity <= 1e-5",),
        confirmed_facts=(),
    )
    condition = compiled.conditions[0]
    assert condition.operator == "less_equal"
    assert condition.limit == pytest.approx(1e-5)
```

Cover units, references, two-sided limits, balance semantics, ambiguous phrases, unconfirmed inferred thresholds, unsupported free-form expressions, duplicate IDs, and provenance.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_acceptance_compiler.py -q -p no:cacheprovider`.

Expected: FAIL because compiler is absent.

- [ ] **Step 3: Implement a closed condition compiler**

Compile only from verified structured facts emitted by Phase 2. Natural-language extraction stays in the Intent Interpreter; this module never reparses the raw prompt. Reject conditions whose observable, unit, scope, operator, or threshold is unresolved. Preserve them as structured `UncompiledRequirement` items with user-facing recovery. Then pass `AcceptancePlan` to `ObservationPlanner` so every condition's observable is present and `required_for_condition_ids` is populated before authoring.

- [ ] **Step 4: Run focused tests**

Run Step 2 and `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_observation_planner.py -q -p no:cacheprovider`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/acceptance src/foampilot/observations/planner.py \
  tests/test_acceptance_compiler.py tests/test_observation_planner.py
git commit -m "feat: compile explicit acceptance conditions"
```

### Task 4: Add Foundation v10 collection fragments without model-authored code

**Files:**
- Create: `src/foampilot/observations/openfoam10.py`
- Modify: `src/foampilot/authoring/models.py`
- Modify: `src/foampilot/authoring/case_author.py`
- Modify: `src/foampilot/plans/compiler.py`
- Test: `tests/test_openfoam10_observation_fragments.py`
- Test: `tests/test_case_author_observations.py`
- Test: `tests/test_plan_compiler_observations.py`

**Interfaces:**
- Produces: deterministic `ObservationConfigFragment` and `PostProcessPlanFragment`.
- Case Author receives `ObservationPlan` but cannot change observation identities or conditions.

- [ ] **Step 1: Write failing injection-boundary tests**

```python
def test_final_field_metric_does_not_inject_function_object() -> None:
    bundle = author.author(intent, design, final_field_plan(), inputs)
    assert "functions" not in control_dict(bundle)


def test_flow_history_fragment_is_system_owned_and_allowlisted() -> None:
    bundle = author.author(intent, design, flow_history_plan(), inputs)
    assert bundle.system_owned_paths == ("system/foampilot-observations",)
    assert model_cannot_overwrite(bundle, "system/foampilot-observations")
```

Also test exact patch/zone names, dictionary include syntax for Foundation v10, collision rejection, no `#codeStream`/`#calc`/system calls, serial execution, and typed post-process argv.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_openfoam10_observation_fragments.py \
  tests/test_case_author_observations.py \
  tests/test_plan_compiler_observations.py \
  -q -p no:cacheprovider
```

Expected: FAIL because observation fragments are absent.

- [ ] **Step 3: Implement system-owned fragments**

For runtime collection, generate a deterministic include owned by FoamPilot and merge it through a fixed Foundation v10 template. The model receives the resolved observation contract but never authors the fragment. For post-processing, emit allowlisted `NativeCommand` entries after the solver and before metric derivation.

- [ ] **Step 4: Re-run case risk and protection gates**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_openfoam10_observation_fragments.py \
  tests/test_case_author_observations.py \
  tests/test_plan_compiler_observations.py \
  tests/test_execution_risk.py tests/test_runtime_protection.py \
  -q -p no:cacheprovider
```

Expected: PASS; observation fragments cannot introduce a dynamic-code or path escape.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/observations/openfoam10.py src/foampilot/authoring \
  src/foampilot/plans/compiler.py tests/test_openfoam10_observation_fragments.py \
  tests/test_case_author_observations.py tests/test_plan_compiler_observations.py
git commit -m "feat: add controlled observation collection"
```

### Task 5: Define `DerivedMetrics` and execute metric calculators

**Files:**
- Create: `src/foampilot/postprocessing/__init__.py`
- Create: `src/foampilot/postprocessing/models.py`
- Create: `src/foampilot/postprocessing/engine.py`
- Test: `tests/test_postprocessing_engine.py`

**Interfaces:**
- Produces: `MetricSample`, `MetricSeries`, `MetricStatus`, `DerivedMetrics`.
- Produces: `MetricCalculator.calculate(item, run_facts, case_root, artifacts) -> MetricSeries`.

- [ ] **Step 1: Write failing provenance and isolation tests**

```python
def test_metric_carries_evidence_provenance() -> None:
    metrics = engine.derive(plan, run_facts, case_root)
    flow = metrics.require("outlet-flow")
    assert flow.samples[-1].unit == "m3/s"
    assert flow.evidence_refs


def test_one_failed_metric_does_not_erase_other_metrics() -> None:
    metrics = engine.derive(plan_with_good_and_missing_items(), run_facts, case_root)
    assert metrics.require("continuity").status == "AVAILABLE"
    assert metrics.require("missing-zone-average").status == "UNAVAILABLE"
```

Also test deterministic ordering, finite-value enforcement, dimensions, region/time scope, source hashes, duplicate calculator rejection, cancellation, and bounded series size.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_postprocessing_engine.py -q -p no:cacheprovider`.

Expected: FAIL because post-processing engine is absent.

- [ ] **Step 3: Implement the extension-driven engine**

The engine resolves a first-party calculator for each observation item, passes only canonical facts and declared files, catches calculator-local failures, and writes `derived-metrics.json` atomically. It never mutates the accepted case and never infers a verdict.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/postprocessing tests/test_postprocessing_engine.py
git commit -m "feat: add derived metrics engine"
```

### Task 6: Implement the first generic CFD metric family

**Files:**
- Create: `src/foampilot/postprocessing/openfoam10.py`
- Modify: `src/foampilot/physics/wall_heat_flux.py`
- Test: `tests/fixtures/postprocessing/**`
- Test: `tests/test_openfoam10_postprocessing.py`
- Modify: `tests/test_physics_audits.py`

**Interfaces:**
- Implements residual, continuity, flow rate, pressure difference, region average, force, and heat-flux calculators.
- All calculators consume `RunFacts` or declared Foundation v10 outputs; none parse solver logs.

- [ ] **Step 1: Write failing replay tests for every first-party kind**

```python
@pytest.mark.parametrize(
    ("kind", "expected_unit"),
    [
        ("residual", "1"),
        ("continuity", "1"),
        ("flow_rate", "m3/s"),
        ("pressure_difference", "m2/s2"),
        ("region_average", "m/s"),
        ("force", "N"),
        ("heat_flux", "W"),
    ],
)
def test_first_party_metric_fixture(kind: str, expected_unit: str) -> None:
    series = calculate_fixture(kind)
    assert series.status == "AVAILABLE"
    assert series.samples[-1].unit == expected_unit
```

Add independent sign-convention tests for inlet/outlet flow, kinematic versus physical pressure, vector-component and magnitude averages, multi-region naming, wall force reference density, conductive/radiative heat flux, and missing source data.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_openfoam10_postprocessing.py tests/test_physics_audits.py \
  -q -p no:cacheprovider
```

Expected: FAIL because generic calculators are absent.

- [ ] **Step 3: Implement calculators with explicit semantics**

- Residual/continuity project directly from `RunFacts`.
- Flow rate consumes an allowlisted `surfaceFieldValue` result or performs a declared post-process command on written `phi`.
- Pressure difference declares kinematic/physical pressure and density conversion in the metric.
- Region average validates the cellZone/region identity from authoritative mesh facts.
- Force uses Foundation v10 force output with declared patches and reference-density semantics.
- Heat flux reuses the existing audited Foundation v10 `wallHeatFlux` integration and returns generic metric provenance.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/postprocessing src/foampilot/physics/wall_heat_flux.py \
  tests/fixtures/postprocessing tests/test_openfoam10_postprocessing.py \
  tests/test_physics_audits.py
git commit -m "feat: derive generic CFD metrics"
```

### Task 7: Evaluate conditions into one truthful `ResultReport`

**Files:**
- Create: `src/foampilot/acceptance/evaluator.py`
- Modify: `src/foampilot/acceptance/models.py`
- Test: `tests/test_acceptance_evaluator.py`

**Interfaces:**
- Produces: `ConditionResult`, `AcceptanceVerdict`, `ResultReport`.
- Verdicts: `PASS`, `FAIL`, `INCOMPLETE`, `NOT_REQUESTED`.

- [ ] **Step 1: Write failing truth-table tests**

```python
def test_missing_required_metric_is_incomplete_not_pass() -> None:
    report = evaluator.evaluate(plan, metrics_missing_required_value())
    assert report.verdict == "INCOMPLETE"
    assert report.conditions[0].status == "NOT_EVALUATED"


def test_observation_only_metric_never_changes_verdict() -> None:
    report = evaluator.evaluate(empty_acceptance_plan(), observed_metrics())
    assert report.verdict == "NOT_REQUESTED"
    assert report.observations
```

Cover every operator, unit mismatch, vector/scalar mismatch, tolerance equality, NaN/Inf, unavailable evidence, multiple failing conditions, and deterministic explanation order.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_acceptance_evaluator.py -q -p no:cacheprovider`.

Expected: FAIL because evaluator is absent.

- [ ] **Step 3: Implement deterministic evaluation**

`ResultReport` records hashes of `AcceptancePlan`, `ObservationPlan`, `RunFacts`, and `DerivedMetrics`; includes every requested observation; separates failed conditions from missing evidence; and contains no private reference values unless they were part of the user-visible TaskSpec.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/acceptance tests/test_acceptance_evaluator.py
git commit -m "feat: evaluate public acceptance conditions"
```

### Task 8: Wire observation, metrics, and acceptance stages through the coordinator

**Files:**
- Modify: `src/foampilot/workflow/services.py`
- Modify: `src/foampilot/workflow/coordinator.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `src/foampilot/workflow/projection.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Test: `tests/test_workflow_coordinator.py`
- Test: `tests/test_workflow_lineage.py`
- Test: `tests/test_native_agent_state_machine.py`

**Interfaces:**
- Adds stages `ACCEPTANCE_COMPILED`, `OBSERVATION_PLANNED`, `POSTPROCESSED`, `ACCEPTANCE_EVALUATED`.
- Records all contract hashes in lineage and strict-resume fingerprint.

- [ ] **Step 1: Write failing state and resume tests**

```python
def test_observation_plan_precedes_case_authoring(event_names: list[str]) -> None:
    assert event_names.index("ACCEPTANCE_COMPILED") < event_names.index("OBSERVATION_PLANNED")
    assert event_names.index("OBSERVATION_PLANNED") < event_names.index("CASE_AUTHORED")


def test_strict_resume_rejects_changed_acceptance_plan() -> None:
    with pytest.raises(ResumeCompatibilityError, match="acceptance plan"):
        resume_with_modified_condition(original_run)
```

Also test post-processing after `RunFacts`, acceptance after metrics, no-condition result, post-process failure isolation, cancellation, and old-run read-only projection.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_workflow_coordinator.py tests/test_workflow_lineage.py \
  tests/test_native_agent_state_machine.py -q -p no:cacheprovider
```

Expected: FAIL because the new stages are absent.

- [ ] **Step 3: Add service calls and immutable artifacts**

Persist in this order:

- `acceptance-plan.json` before observation planning;
- `observation-plan.json` before authoring;
- `derived-metrics.json` after fact extraction;
- `result-report.json` after evaluation.

Coordinator decisions remain limited to stage outcomes and declared terminal policy. It does not inspect metric names or values.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/workflow src/foampilot/agent/native_orchestrator.py \
  tests/test_workflow_coordinator.py tests/test_workflow_lineage.py \
  tests/test_native_agent_state_machine.py
git commit -m "feat: integrate observation and acceptance stages"
```

### Task 9: Project the same result report to CLI and Desktop

**Files:**
- Modify: `src/foampilot/workflow/projection.py`
- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/desktop/viewmodels.py`
- Modify: `src/foampilot/desktop/repository.py`
- Modify: `src/foampilot/desktop/main_window.py`
- Test: `tests/test_cli_results.py`
- Test: `tests/test_desktop_repository.py`
- Test: `tests/test_desktop_main_window.py`

**Interfaces:**
- Adds shared projections for observations, metric series, condition results, missing evidence, and overall verdict.
- CLI supports `foampilot results RUN --json` and a concise human table.

- [ ] **Step 1: Write failing CLI/Desktop parity tests**

```python
def test_cli_and_desktop_share_condition_status(run_fixture: Path) -> None:
    projection = load_workflow_projection(run_fixture)
    cli = invoke_results_json(run_fixture)
    desktop = RunRepository(run_fixture).snapshot()
    assert cli["result_report"] == projection.result_report.model_dump(mode="json")
    assert desktop.result_report == projection.result_report
```

Also test long series downsampling, unavailable metrics, observation-only labeling, no-result legacy run, malformed artifact diagnostics, and Chinese failure/recovery text.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_cli_results.py tests/test_desktop_repository.py \
  tests/test_desktop_main_window.py -q -p no:cacheprovider
```

Expected: FAIL because result projection is absent.

- [ ] **Step 3: Implement read-only presentation**

Desktop adds an “结果/验收” panel with metric name, scope, latest value/unit, status, condition, verdict, and evidence link. Plot only bounded projection samples. CLI and Desktop may translate labels but cannot recompute values or verdicts.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/workflow/projection.py src/foampilot/cli/main.py \
  src/foampilot/desktop tests/test_cli_results.py \
  tests/test_desktop_repository.py tests/test_desktop_main_window.py
git commit -m "feat: display observations and acceptance results"
```

### Task 10: Delete legacy public validation contracts and prove generic extension boundaries

**Files:**
- Modify/Delete: `src/foampilot/validation/native.py`
- Modify/Delete: `src/foampilot/validation/public_checks.py`
- Modify: `src/foampilot/validation/__init__.py`
- Modify: `src/foampilot/agent/failure.py`
- Modify: `src/foampilot/agent/repair.py`
- Modify: `src/foampilot/qualification/reporting.py`
- Modify: `src/foampilot/performance/plan_reuse.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `tests/test_native_validation.py`
- Modify: `tests/test_public_checks.py`
- Create: `tests/test_no_duplicate_evidence_parsers.py`
- Create: `tests/test_extension_architecture.py`

**Interfaces:**
- Legacy public validation artifacts remain readable through an adapter only.
- New runs write `result-report.json`, not a newly parsed `public-validation.json`.

- [ ] **Step 1: Write failing source-boundary tests**

```python
def test_only_evidence_package_imports_parse_openfoam_log() -> None:
    violations = imports_outside("src/foampilot/evidence", "parse_openfoam_log")
    assert violations == []


def test_coordinator_has_no_first_party_metric_names() -> None:
    source = Path("src/foampilot/workflow/coordinator.py").read_text()
    for token in first_party_observation_registry().ids():
        assert token not in source
```

Also assert canonical solve imports no `PublicCheck`/`PublicValidationReport`, forbid entry-point loading, and prove a synthetic first-party descriptor can be registered without editing coordinator code. Retain the Phase 4 rule that raw log reads are forbidden in validation, repair, qualification, post-processing, CLI, and Desktop.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_no_duplicate_evidence_parsers.py tests/test_extension_architecture.py \
  tests/test_native_validation.py tests/test_public_checks.py \
  -q -p no:cacheprovider
```

Expected: parser-boundary assertions already PASS from Phase 4, while canonical-workflow assertions FAIL because legacy public-validation contracts and direct consumers remain.

- [ ] **Step 3: Migrate consumers and remove duplicate parsing**

Replace validation and qualification reads with `ResultReport`/`RunFacts`. Move retained family-specific evaluators behind observation/metric extension descriptors. Keep private holdout loaders outside public contracts and do not expose their references to the Agent. Do not relocate the existing private asset tree in this roadmap; the physical distribution split remains deferred.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot tests/test_no_duplicate_evidence_parsers.py \
  tests/test_extension_architecture.py tests/test_native_validation.py \
  tests/test_public_checks.py
git commit -m "refactor: remove duplicate validation parsers"
```

### Task 11: Run cross-scenario gates and document the completed architecture

**Files:**
- Create: `tests/fixtures/contract_first/**`
- Create: `tests/test_contract_first_replay_matrix.py`
- Modify: `tests/test_real_native_vertical_slice.py`
- Modify: `docs/architecture.md`
- Modify: `docs/agent-integration.md`
- Modify: `docs/desktop-ide.md`
- Modify: `docs/qualification.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Replay matrix covers: provided polyMesh, generated mesh, region case, steady incompressible, transient incompressible, compressible, heat transfer, multiphase, failure/repair, cancellation/resume.
- At least one real Foundation v10 case proves flow/continuity/pressure/region metrics.

- [ ] **Step 1: Add replay fixtures from current public evidence**

Store only public, minimal, immutable inputs and normalized logs/outputs. Each fixture asserts the complete chain:

```text
AssetBundle -> MeshFacts -> Intent -> CaseDesign -> ObservationPlan
-> CaseBundle -> ExecutionPlan -> RunFacts -> DerivedMetrics -> ResultReport
```

- [ ] **Step 2: Run replay matrix**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_contract_first_replay_matrix.py -q -p no:cacheprovider`.

Expected: PASS for every supported combination; unsupported combinations terminate with a typed capability error rather than a guessed case.

- [ ] **Step 3: Run the real Foundation v10 gate**

Run:

```bash
FOAMPILOT_RUN_REAL_CASES=1 \
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_real_native_vertical_slice.py -q -p no:cacheprovider
```

Expected: provided polyMesh is unchanged; `checkMesh` reports Mesh OK; solver exits normally; `RunFacts` has completion/residual/continuity; `DerivedMetrics` contains inlet/outlet flow, pressure difference, and zone average; `ResultReport` evaluates only explicit limits.

- [ ] **Step 4: Run the complete deterministic gate**

Run:

```bash
git diff --check
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: no whitespace errors and complete suite PASS.

- [ ] **Step 5: Build and inspect distributions**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m build
/home/edwin/feal-venv-py312/bin/python -m zipfile -l dist/foampilot-*.whl
```

Expected: wheel/sdist build; public first-party descriptors and replay-safe package resources are present; no new evaluator-private references or machine-local paths appear in public workflow artifacts. Existing evaluator packaging is unchanged pending its separate task.

- [ ] **Step 6: Update architecture and user documentation**

Document:

- authoritative versus user versus model facts;
- confidence gate and per-field confirmation;
- observation-only versus pass/fail conditions;
- evidence collection precedence and overhead;
- new CLI/Desktop results behavior;
- Foundation v10-only first-release boundary;
- local-machine qualification status and known cross-machine gate gap.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/contract_first tests/test_contract_first_replay_matrix.py \
  tests/test_real_native_vertical_slice.py docs README.md CHANGELOG.md
git commit -m "docs: complete contract-first workflow"
```

## Final Completion Gate

Phase 5 and the full roadmap are complete only when:

- every requested observation is either available or explicitly unavailable with recovery;
- no unconfirmed threshold affects a verdict;
- runtime collection is minimal and system-owned;
- no module outside the evidence package interprets raw native logs;
- all first-party metric kinds pass fixture tests;
- CLI and Desktop display the same result report;
- the replay matrix and at least one real Foundation v10 closed loop pass;
- legacy coupled validation paths are removed from new-run execution;
- complete tests and package builds pass.
