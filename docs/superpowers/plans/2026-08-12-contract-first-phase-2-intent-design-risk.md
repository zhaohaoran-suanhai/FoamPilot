# Phase 2 Simulation Intent, Case Design, and Risk Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split model reasoning into intent interpretation and case design, then allow case authoring only from a frozen design whose high-impact facts have concrete provenance.

**Architecture:** Introduce a `foampilot.simulation` package containing strict provenance, intent, requirement-resolution, proposal, frozen-design, risk, and confirmation contracts. Replace TaskDraft's free-form fact map with a canonical TaskSpec v3 request envelope and immutable run artifacts; migrate repository tasks in one change and keep v2 only in the old-run read-only adapter. A deterministic `RequirementResolver` establishes completeness and conflicts before design. The system computes `RiskDecision`; the model only returns candidates, alternatives, evidence, and unresolved questions.

**Tech Stack:** Python 3.12, Pydantic 2 discriminated unions, PyYAML, pytest 8, existing `ModelGateway`, Foundation OpenFOAM 10 capability registry from Phase 1.

## Global Constraints

- `CaseDesignProposal` contains no OpenFOAM file content and no executable commands.
- Model output may never use `user_confirmation`, `public_asset_fact`, `system_default`, or `deterministic_rule` as self-asserted authority.
- Model confidence is not a field in any model response schema.
- `READY_TO_AUTHOR` requires all required high-impact decisions to be concretely resolved.
- A confirmable question must contain one or more explicit typed candidates.
- An information-required question has no safe unique candidate and cannot be bypassed.
- There is no accept-all, continue-anyway, or risk-override API for high-impact facts.
- One form may submit multiple answers, but every answer produces a separate `ConfirmationRecord`.
- User confirmation creates a child continuation run; the parent remains immutable.
- Qualification tasks migrate to frozen explicit request facts and use the same RiskGate as ordinary tasks.
- Do not introduce the new Case Author contract during this phase. The existing author bridge may run only after `READY_TO_AUTHOR` until Phase 3 replaces it.
- Use TDD and commit after every task.

---

## File Structure

- `src/foampilot/simulation/provenance.py`: stable fact-source, impact, evidence, uncertainty, and confirmation types.
- `src/foampilot/simulation/intent.py`: `SimulationIntent` and its model interpreter.
- `src/foampilot/simulation/requirements.py`: deterministic required-fact, conflict, and capability-input resolution.
- `src/foampilot/simulation/design.py`: `CaseDesignProposal`, frozen `CaseDesign`, and design-model request.
- `src/foampilot/simulation/risk_gate.py`: deterministic decision and concrete confirmation rules.
- `src/foampilot/simulation/io.py`: canonical JSON/YAML hashing and immutable artifact read/write.
- `src/foampilot/tasks/models.py`: TaskSpec v3 request envelope.
- `src/foampilot/tasks/legacy.py`: run-report-only TaskSpec v2 reader; not exported to authoring.
- `src/foampilot/workflow/confirmation.py`: parent verification, answer application, and child continuation input.
- `src/foampilot/cli/main.py`: `task prepare`, `questions`, and `confirm` interfaces.
- `src/foampilot/agent/native_orchestrator.py`: stage the two model calls and stop truthfully on pending information.
- `tests/fixtures/tasks-v3/**`: migrated public task fixtures.

### Task 1: Define provenance and uncertainty contracts

**Files:**
- Create: `src/foampilot/simulation/__init__.py`
- Create: `src/foampilot/simulation/provenance.py`
- Create: `src/foampilot/simulation/io.py`
- Test: `tests/test_simulation_provenance.py`

**Interfaces:**
- Produces: `EvidenceSource`, `ImpactLevel`, `FactEvidence`, `ResolvedValue[T]`, `DesignCandidate`, `Uncertainty`, `ConfirmationRecord`.
- Produces: `canonical_sha256(model: BaseModel) -> str` and exclusive artifact writers.

- [ ] **Step 1: Write failing source-authority tests**

```python
def test_model_inference_cannot_be_marked_confirmed() -> None:
    with pytest.raises(ValidationError, match="model inference cannot self-confirm"):
        ResolvedValue[str](
            field_path="solver.family",
            value="pisoFoam",
            source="model_inference",
            impact="high",
            evidence=(FactEvidence(kind="model_reason", detail="candidate"),),
            confirmed=True,
        )


def test_confirmation_binds_one_question_field_and_value() -> None:
    record = ConfirmationRecord(
        confirmation_id="confirm-1",
        question_id="question-1",
        field_path="materials.fluid.nu",
        candidate_id="water-like-nu",
        confirmed_value={"value": 1e-6, "unit": "m2/s"},
        source="user_confirmation",
        answered_at="2026-08-12T12:00:00Z",
    )
    assert record.field_path == "materials.fluid.nu"
```

Also test unique evidence, legal field paths, JSON-only values, canonical-hash stability, and that system defaults are low impact only.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_simulation_provenance.py -q -p no:cacheprovider`

Expected: FAIL because `foampilot.simulation` does not exist.

- [ ] **Step 3: Implement frozen generic contracts**

Use these exact enums and invariants:

```python
EvidenceSource = Literal[
    "user_text", "user_confirmation", "public_asset_fact",
    "deterministic_rule", "system_default", "model_inference",
]
ImpactLevel = Literal["low", "medium", "high"]


class DesignCandidate(StrictModel):
    candidate_id: str
    value: JsonValue
    rationale: str
    evidence: tuple[FactEvidence, ...]


class Uncertainty(StrictModel):
    question_id: str
    field_path: str
    impact: ImpactLevel
    kind: Literal["confirmable", "information_required", "conflict"]
    prompt_zh: str
    reason_zh: str
    candidates: tuple[DesignCandidate, ...] = ()
```

`ResolvedValue[T]` always includes `field_path`, `value`, `source`, `impact`,
`evidence`, and `confirmed`; facts cannot rely on their container position for
identity. Require candidates for `confirmable`; forbid candidates for
`information_required`; require at least two candidates or conflicting evidence
for `conflict`.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/simulation tests/test_simulation_provenance.py
git commit -m "feat: add simulation provenance contracts"
```

### Task 2: Replace TaskSpec v2 authoring with a v3 request envelope

**Files:**
- Modify: `src/foampilot/tasks/models.py`
- Create: `src/foampilot/tasks/legacy.py`
- Modify: `src/foampilot/tasks/io.py`
- Modify: `src/foampilot/tasks/__init__.py`
- Modify: `src/foampilot/taskbuilder/models.py`
- Modify: `src/foampilot/taskbuilder/compiler.py`
- Modify: `examples/tasks/*.yaml`
- Modify: `src/foampilot/qualification/data/suites/*.yaml`
- Modify: `tests/fixtures/artifact-replay/*/task.json`
- Test: `tests/test_task_spec.py`
- Test: `tests/test_task_compiler.py`
- Test: `tests/test_artifact_replay.py`

**Interfaces:**
- Produces canonical `TaskSpec.schema_version=3`.
- Produces read-only `load_legacy_task_spec_from_run(path) -> LegacyTaskSpecV2`.
- Removes schema v2 acceptance from `load_task_spec` used by `solve`, `plan`, and `validate`.

- [ ] **Step 1: Write failing canonical/legacy boundary tests**

```python
def test_authoring_loader_rejects_task_v2(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, {**v2_payload(), "schema_version": 2})
    with pytest.raises(ValidationError):
        load_task_spec(path)


def test_legacy_v2_is_only_readable_through_run_adapter(tmp_path: Path) -> None:
    path = write_json(tmp_path, {**v2_payload(), "schema_version": 2})
    legacy = load_legacy_task_spec_from_run(path)
    assert legacy.schema_version == 2
```

Add a repository test loading every example and qualification task through the v3 loader.

- [ ] **Step 2: Run migration tests and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_task_spec.py tests/test_task_compiler.py tests/test_artifact_replay.py -q -p no:cacheprovider`

Expected: FAIL until schema and fixtures migrate.

- [ ] **Step 3: Implement the v3 envelope and migrate tracked tasks**

Use this canonical shape:

```python
class TaskSpec(StrictModel):
    schema_version: Literal[3] = 3
    task_id: str
    title: str
    request_text: str
    openfoam_target: OpenFOAMTarget
    resource_budget: ResourceBudget
    public_assets: list[PublicAsset] = Field(default_factory=list)
    required_outputs: list[str]
    acceptance_intent: list[str]
    protected_paths: list[str] = Field(default_factory=list)
    repair_policy: RepairPolicyInput = Field(default_factory=RepairPolicyInput)
    explicit_facts: list[ResolvedValue[JsonValue]] = Field(default_factory=list)
```

`RepairPolicyInput` contains `automatic_numerical_repair: bool = True` and `model_diagnostic: bool = True`. Preserve the old prompt as `request_text`; convert old explicit geometry/mesh/physics fields to `explicit_facts` with verified provenance. Do not retain `public_checks` in the author-visible task; Phase 5 compiles acceptance intent.

- [ ] **Step 4: Run focused and repository task tests**

Run the Step 2 command and `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_qualification_suites.py tests/test_repository_docs.py -q -p no:cacheprovider`.

Expected: PASS; no tracked authoring task remains schema v2.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/tasks src/foampilot/taskbuilder examples/tasks src/foampilot/qualification/data tests/fixtures/artifact-replay tests/test_task_spec.py tests/test_task_compiler.py tests/test_artifact_replay.py
git commit -m "feat: migrate authoring tasks to schema v3"
```

### Task 3: Implement `SimulationIntent` and the intent-only model call

**Files:**
- Create: `src/foampilot/simulation/intent.py`
- Modify: `src/foampilot/simulation/__init__.py`
- Modify: `src/foampilot/models/budgets.py`
- Modify: `src/foampilot/models/schema.py`
- Modify: `tests/support/model_gateway.py`
- Create: `tests/test_intent_interpreter.py`

**Interfaces:**
- Consumes: `TaskSpec`, `AssetFacts`, `InputMeshFacts`, and capability descriptor summaries.
- Produces: `interpret_intent(task, asset_facts, mesh_facts, capability_kinds, gateway, budget, trace) -> SimulationIntent`.
- Adds `ModelStage.INTENT_INTERPRETATION`.

- [ ] **Step 1: Write failing prompt-boundary and authority tests**

```python
def test_interpreter_request_contains_facts_not_raw_mesh(scripted_gateway) -> None:
    intent = interpret_intent(...)
    request = scripted_gateway.requests[0]
    assert "InputMeshFacts" in request.user_prompt
    assert "FoamFile" not in request.user_prompt
    assert all(fact.source != "user_confirmation" for fact in intent.facts)


def test_false_user_text_evidence_is_downgraded() -> None:
    response = _response_with_fact(
        path="physics.compressibility", value="incompressible",
        source="user_text", evidence="not present verbatim", impact="high",
    )
    intent = run_interpreter(response, request_text="simulate a flow")
    assert intent.fact("physics.compressibility").source == "model_inference"
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_intent_interpreter.py -q -p no:cacheprovider`

Expected: FAIL because the interpreter is absent.

- [ ] **Step 3: Implement strict intent extraction**

`SimulationIntent` contains `facts`, `constraints`, `requested_observables`, `acceptance_intent`, and `uncertainties`; it contains no solver executable, numerical schemes, native file paths, or commands unless explicitly present in verified user text. Re-verify user-text substrings and public-asset fact IDs after model response, downgrading unverifiable authority to `model_inference`.

Use this system instruction:

```text
You interpret simulation intent only. Do not write OpenFOAM files, choose numerical schemes,
create commands, or assign confidence. Report ambiguity as structured uncertainties.
```

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/simulation src/foampilot/models/budgets.py src/foampilot/models/schema.py tests/support/model_gateway.py tests/test_intent_interpreter.py
git commit -m "feat: interpret simulation intent separately"
```

### Task 4: Resolve required facts and conflicts before design

**Files:**
- Create: `src/foampilot/simulation/requirements.py`
- Modify: `src/foampilot/simulation/__init__.py`
- Modify: `src/foampilot/extensions/models.py`
- Test: `tests/test_requirement_resolver.py`

**Interfaces:**
- Consumes: `SimulationIntent`, `AssetFacts`, `InputMeshFacts`, `ExecutedMeshFacts | None`, and selected capability descriptors.
- Produces: `ResolvedRequirement`, `RequirementGap`, `RequirementConflict`, `ResolvedRequirements`.
- Performs no model call.

- [ ] **Step 1: Write failing authority and completeness tests**

```python
def test_user_zone_semantics_and_mesh_zone_existence_remain_distinct() -> None:
    resolved = resolve_requirements(
        intent=intent_saying_zone_is_porous("porousBlockage"),
        mesh_facts=mesh_with_cell_zone("porousBlockage", 64),
        capabilities=porous_capability(),
    )
    assert resolved.require("mesh.cell_zones.porousBlockage.count").source == "public_asset_fact"
    assert resolved.require("regions.porousBlockage.role").source == "user_text"


def test_missing_geometry_unit_is_an_information_gap() -> None:
    resolved = resolve_requirements(
        intent=intent_without_geometry_unit(),
        mesh_facts=dimensionless_mesh_facts(),
        capabilities=incompressible_capability(),
    )
    gap = resolved.gaps[0]
    assert gap.field_path == "geometry.length_unit"
    assert gap.kind == "information_required"
    assert gap.candidates == ()
```

Also test contradictory patch roles, nonexistent zone references, explicit-fact precedence, model-inference downgrade, capability-required inputs, deduplication, deterministic ordering, and canonical hash stability.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_requirement_resolver.py -q -p no:cacheprovider`.

Expected: FAIL because the resolver is absent.

- [ ] **Step 3: Implement deterministic resolution**

Merge facts using this precedence only:

1. concrete user confirmation;
2. verified public asset/mesh fact;
3. verified explicit user text;
4. deterministic policy;
5. low-impact system default;
6. model inference, which remains unresolved for medium/high impact.

Capability descriptors declare required field paths and impact levels. The resolver checks presence, source authority, contradictions, and referential integrity but does not choose solver/numerics or invent candidates. It emits compact unresolved gaps for the Case Designer or a direct information-required outcome.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/simulation src/foampilot/extensions/models.py \
  tests/test_requirement_resolver.py
git commit -m "feat: resolve simulation requirements deterministically"
```

### Task 5: Implement `CaseDesignProposal` and design-only model call

**Files:**
- Create: `src/foampilot/simulation/design.py`
- Modify: `src/foampilot/simulation/__init__.py`
- Modify: `src/foampilot/models/budgets.py`
- Modify: `src/foampilot/context/assembler.py`
- Create: `tests/test_case_designer.py`

**Interfaces:**
- Consumes: verified `SimulationIntent`, `ResolvedRequirements`, mesh facts, `CapabilityRegistry`, selected knowledge, and Skills.
- Produces: `design_case(...) -> CaseDesignProposal`.
- Adds `ModelStage.CASE_DESIGN`.
- Produces extension payloads as `ExtensionDecision(extension_id, schema_version, values, provenance)`.

- [ ] **Step 1: Write failing separation and capability tests**

```python
def test_case_design_proposal_cannot_contain_files_or_commands() -> None:
    with pytest.raises(ValidationError):
        CaseDesignProposal.model_validate({**valid_design(), "commands": []})


def test_designer_cannot_select_unregistered_solver(scripted_gateway) -> None:
    proposal = run_designer(_proposal(solver_family="unregistered"))
    assert proposal.capability_conflicts == (
        "solver family is not registered: unregistered",
    )
```

Also test target-version mismatch, extension incompatibility, missing executable, bounded context size, no evaluator data, and preservation of every explicit high-impact intent fact.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_case_designer.py -q -p no:cacheprovider`

Expected: FAIL because design contracts are absent.

- [ ] **Step 3: Implement proposal generation and deterministic reconciliation**

Use top-level proposal fields `solver_family`, `physical_models`, `materials`, `boundary_designs`, `initial_conditions`, `time_design`, `numerical_design`, `region_models`, `extension_decisions`, `uncertainties`, `alternatives`, `reasoning_evidence`, and `capability_conflicts`. Every high-impact value is wrapped in `ResolvedValue`; model-originated values remain unconfirmed. Resolve every selected extension against `CapabilityRegistry` and populate conflicts deterministically. Do not ask the model to score confidence.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/simulation src/foampilot/models/budgets.py src/foampilot/context/assembler.py tests/test_case_designer.py
git commit -m "feat: separate case design reasoning"
```

### Task 6: Implement deterministic `RiskGate`

**Files:**
- Create: `src/foampilot/simulation/risk_gate.py`
- Modify: `src/foampilot/simulation/__init__.py`
- Create: `tests/test_design_risk_gate.py`

**Interfaces:**
- Consumes: `SimulationIntent`, `ResolvedRequirements`, `CaseDesignProposal`, `CapabilityRegistry`.
- Produces: `evaluate_design_risk(...) -> RiskDecision`.
- Produces: `freeze_case_design(proposal, decision) -> CaseDesign` only for `READY_TO_AUTHOR`.

- [ ] **Step 1: Write failing four-state gate tests**

```python
@pytest.mark.parametrize(
    ("proposal", "expected"),
    [
        (fully_resolved_proposal(), "READY_TO_AUTHOR"),
        (one_high_impact_candidate(), "CONFIRMATION_REQUIRED"),
        (missing_material_without_candidate(), "INFORMATION_REQUIRED"),
        (unsupported_solver(), "CAPABILITY_UNAVAILABLE"),
    ],
)
def test_risk_gate_states(proposal, expected) -> None:
    assert evaluate_design_risk(..., proposal=proposal).state == expected


def test_model_reported_confidence_is_not_a_schema_field() -> None:
    with pytest.raises(ValidationError):
        CaseDesignProposal.model_validate({**valid_design(), "confidence": "high"})
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_design_risk_gate.py -q -p no:cacheprovider`

Expected: FAIL because RiskGate is absent.

- [ ] **Step 3: Implement precedence and freezing**

```python
if capability_conflicts:
    state = "CAPABILITY_UNAVAILABLE"
elif information_required or unresolved_conflicts:
    state = "INFORMATION_REQUIRED"
elif unconfirmed_medium_or_high_candidates:
    state = "CONFIRMATION_REQUIRED"
else:
    state = "READY_TO_AUTHOR"
```

`RiskDecision` contains `state`, `questions`, `reason_codes`, `proposal_sha256`, and `required_extension_ids`. `freeze_case_design` rejects every other state and records intent/proposal hashes, confirmation IDs, extension identities, and final `design_sha256`.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/simulation tests/test_design_risk_gate.py
git commit -m "feat: gate case design with deterministic risk"
```

### Task 7: Add concrete per-field confirmation continuation

**Files:**
- Create: `src/foampilot/workflow/confirmation.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `src/foampilot/workflow/__init__.py`
- Modify: `src/foampilot/cli/main.py`
- Test: `tests/test_design_confirmation.py`
- Test: `tests/test_continuation.py`
- Test: `tests/test_job_cli.py`

**Interfaces:**
- Produces CLI `foampilot questions RUN_DIR --json`.
- Produces CLI `foampilot confirm RUN_DIR --answers ANSWERS.yaml --run-root RUNS --json`.
- Produces `apply_confirmation_records(parent, answers) -> ConfirmationContinuation`.

- [ ] **Step 1: Write failing no-generic-override tests**

```python
def test_confirmation_requires_exact_candidate_id_and_value() -> None:
    with pytest.raises(ConfirmationError, match="CONFIRMATION_VALUE_MISMATCH"):
        apply_confirmation_records(parent, [{
            "question_id": "q-nu", "candidate_id": "nu-1",
            "confirmed_value": 0.5,
        }])


@pytest.mark.parametrize("answer", ["continue", "accept_all", "use_model_judgement"])
def test_generic_confirmation_is_not_an_api(answer: str) -> None:
    with pytest.raises(ConfirmationError, match="CONCRETE_CONFIRMATION_REQUIRED"):
        parse_answers({"action": answer})
```

Also test duplicate answers, missing questions, tampered parent manifest, proposal hash mismatch, information-required questions rejecting answers, and one record per field in a multi-answer form.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_design_confirmation.py tests/test_continuation.py tests/test_job_cli.py -q -p no:cacheprovider`

Expected: FAIL because confirmation continuation is absent.

- [ ] **Step 3: Implement immutable confirmation continuation**

The answer file contains only:

```yaml
schema_version: 1
answers:
  - question_id: q-nu
    candidate_id: nu-water
    confirmed_value: {value: 1.0e-6, unit: m2/s}
```

Verify candidate equality before creating the child. Re-run RiskGate in the child, freeze `case-design.json` only at `READY_TO_AUTHOR`, and include parent manifest SHA256 plus confirmation-record hashes in lineage compatibility.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/workflow src/foampilot/cli/main.py tests/test_design_confirmation.py tests/test_continuation.py tests/test_job_cli.py
git commit -m "feat: add concrete design confirmation continuation"
```

### Task 8: Integrate intent/design/risk stages into solve and Desktop jobs

**Files:**
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/jobs/models.py`
- Modify: `src/foampilot/jobs/worker.py`
- Modify: `src/foampilot/desktop/job_controller.py`
- Modify: `src/foampilot/desktop/main_window.py`
- Modify: `src/foampilot/desktop/viewmodels.py`
- Test: `tests/test_native_agent_state_machine.py`
- Test: `tests/test_job_worker.py`
- Test: `tests/test_desktop_job_controller.py`
- Test: `tests/test_desktop_main_window.py`
- Create: `tests/test_real_contract_first_design_gate.py`

**Interfaces:**
- Produces run artifacts `simulation-intent.json`, `resolved-requirements.json`, `case-design-proposal.json`, `risk-decision.json`, pending `questions.json`, and frozen `case-design.json`.
- Adds workflow stages `INTERPRETING_INTENT`, `RESOLVING_REQUIREMENTS`, `DESIGNING_CASE`, `WAITING_FOR_INFORMATION`, and `WAITING_FOR_CONFIRMATION`.
- Produces non-CFD statuses `INFORMATION_REQUIRED`, `CONFIRMATION_REQUIRED`, `CAPABILITY_UNAVAILABLE`.

- [ ] **Step 1: Add failing no-author-before-gate tests**

```python
def test_confirmation_required_makes_zero_author_calls(scripted_gateway) -> None:
    outcome = solve_with_design(_proposal_requiring_confirmation())
    assert outcome.status == "CONFIRMATION_REQUIRED"
    assert [request.purpose for request in scripted_gateway.requests] == [
        "interpret-simulation-intent", "design-openfoam-case",
    ]


def test_ready_design_is_frozen_before_legacy_author_call(scripted_gateway) -> None:
    outcome = solve_with_ready_design()
    assert (outcome.run_dir / "case-design.json").is_file()
    assert purpose_order(scripted_gateway)[:3] == [
        "interpret-simulation-intent", "design-openfoam-case",
        "author-native-case-bundle",
    ]
```

Add Desktop tests showing field, reason, candidates, and information-needed messages without an accept-all button.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_native_agent_state_machine.py tests/test_job_worker.py tests/test_desktop_job_controller.py tests/test_desktop_main_window.py -q -p no:cacheprovider`

Expected: FAIL because solve skips the new stages.

- [ ] **Step 3: Insert the stages before current authoring**

Persist and checkpoint each artifact before the next call. Run the deterministic Requirement Resolver immediately after intent interpretation. If it has hard information gaps that no design candidate can resolve, stop before the design model call; otherwise pass its compact artifact to Case Designer. Pending information/confirmation finalizes the current immutable run with `workflow_state=DEFERRED`, `native_status=None`, and a non-CFD primary failure record. A ready design passes to the existing author only as a temporary Phase 2 bridge; include the design in the prompt and assert post-response that the old manifest has not contradicted it.

- [ ] **Step 4: Run deterministic and real design gates**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_native_agent_state_machine.py tests/test_job_worker.py tests/test_desktop_job_controller.py tests/test_desktop_main_window.py tests/test_real_contract_first_design_gate.py -q -p no:cacheprovider`

Expected: deterministic PASS; real model gate freezes a concrete design or produces precise field questions, never malformed asset YAML or a generic continue path.

- [ ] **Step 5: Run the complete suite and commit**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests
git add src/foampilot/agent/native_orchestrator.py src/foampilot/workflow src/foampilot/jobs src/foampilot/desktop tests/test_native_agent_state_machine.py tests/test_job_worker.py tests/test_desktop_job_controller.py tests/test_desktop_main_window.py tests/test_real_contract_first_design_gate.py
git commit -m "feat: gate solve on frozen case design"
```

### Task 9: Update architecture docs and Phase 2 migration boundary

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/system-overview.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `tests/test_import_boundary.py`
- Modify: `tests/test_repository_docs.py`

**Interfaces:**
- Documents the v3 task envelope, risk outcomes, concrete confirmation CLI, and temporary Phase 2 author bridge.
- Adds import-boundary checks that taskbuilder does not import Runner and RiskGate does not import model backends.

- [ ] **Step 1: Add failing documentation and import-boundary assertions**

```python
def test_contract_first_docs_name_the_no_generic_override_rule() -> None:
    text = Path("docs/architecture.md").read_text()
    assert "CONCRETE_CONFIRMATION_REQUIRED" in text
    assert "READY_TO_AUTHOR" in text
    assert "模型不能自报 confidence" in text
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_import_boundary.py tests/test_repository_docs.py -q -p no:cacheprovider`

Expected: FAIL until docs and boundaries update.

- [ ] **Step 3: Update docs and boundaries**

Document that TaskSpec v2 is read-only legacy, confirmation creates a child run, pending design is not a CFD failure, and the Phase 2 old author bridge will be deleted in Phase 3.

- [ ] **Step 4: Run Phase 2 release gates**

Run: `git diff --check` and `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests`.

Expected: no whitespace errors and all deterministic tests pass.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md README.md docs/architecture.md docs/system-overview.md docs/independent-agent-quickstart.md tests/test_import_boundary.py tests/test_repository_docs.py
git commit -m "docs: publish staged design and risk workflow"
```
