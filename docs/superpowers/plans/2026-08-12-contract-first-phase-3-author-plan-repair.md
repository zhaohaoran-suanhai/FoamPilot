# Phase 3 Case Authoring, Plan Compilation, and Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model author one coherent native case bundle from a frozen design while the deterministic extension environment owns commands, conformance checks, and repair authorization.

**Architecture:** Split the model response into `CaseBundle(manifest, files)` and the system product `ExecutionPlan` schema v4. First-party mesh and solver extensions expose plan fragments and semantic validators; `PlanCompiler` composes and validates them. Phase 5 will add observation fragments through the same protocol after the core author/compiler boundary is established. Repair uses a typed `RepairProposal` whose design changes are checked against an optional default-on `NumericalRepairEnvelope` before a scoped file patch can execute.

**Tech Stack:** Python 3.12, Pydantic 2, existing manifests/inspection/runtime modules, pytest 8, Foundation OpenFOAM 10.

## Global Constraints

- The Case Author sees only frozen `CaseDesign`, compact mesh facts, target facts, and selected public knowledge/Skills. Phase 5 extends this input with the frozen observation plan.
- The Case Author returns all related native files in one response and no command list.
- ExecutionPlan schema v4 is always system compiled; schema v3 is read-only replay input.
- Extensions may produce typed command fragments but cannot execute them.
- The compiled plan must preserve Runner-owned MPI launch, budgets, command stages, and executable allowlists.
- `CaseVerifier` rejects any contradiction with frozen design, even if the case would run.
- Mechanical repair may not change design semantics.
- Numerical repair is enabled by default and can be disabled in TaskSpec.
- Automatic numerical changes must be explicitly allowed by `NumericalRepairEnvelope` path, operator, direction, and bound.
- Solver, physical model, material, boundary, initial condition, region meaning, region-model coefficient, turbulence model, and final simulation time changes always require concrete user confirmation.
- Unknown repair paths and undeclared changes are rejected.
- Use TDD and commit after every task.

---

## File Structure

- `src/foampilot/authoring/models.py`: model-authored `CaseBundle` only.
- `src/foampilot/authoring/case_author.py`: frozen-design prompt and one coherent model call.
- `src/foampilot/plans/compiler.py`: extension-fragment composition and ExecutionPlan v4 construction.
- `src/foampilot/plans/legacy.py`: narrow schema-v3 replay reader only.
- `src/foampilot/extensions/mesh/*.py`: first-party mesh plan contributors.
- `src/foampilot/extensions/solver/*.py`: first-party solver plan contributors.
- `src/foampilot/inspection/design_conformance.py`: design-to-manifest/file semantic checks.
- `src/foampilot/repair/models.py`: repair policy, envelope, design change, and proposal contracts.
- `src/foampilot/repair/envelope.py`: deterministic change authorization.
- `src/foampilot/repair/coordinator.py`: mechanical/numerical/physical routing and confirmation return.

### Task 1: Split `CaseBundle` from system `ExecutionPlan`

**Files:**
- Create: `src/foampilot/authoring/__init__.py`
- Create: `src/foampilot/authoring/models.py`
- Modify: `src/foampilot/plans/models.py`
- Create: `src/foampilot/plans/legacy.py`
- Modify: `src/foampilot/plans/__init__.py`
- Modify: `src/foampilot/plans/input_normalizer.py`
- Test: `tests/test_case_bundle.py`
- Test: `tests/test_execution_plan.py`
- Test: `tests/test_artifact_replay.py`

**Interfaces:**
- Produces: `CaseBundle(schema_version=1, manifest: CaseManifest, files: list[GeneratedFile])`.
- Produces: `ExecutionPlan.schema_version: Literal[4]` with `compiled_from_design_sha256`, `compiler_identities`, `manifest`, `files`, and `commands`.
- Produces: `load_legacy_execution_plan_v3_for_replay(path)`, not exported from `foampilot.plans`.

- [ ] **Step 1: Write failing authority-boundary tests**

```python
def test_case_bundle_rejects_commands() -> None:
    with pytest.raises(ValidationError):
        CaseBundle.model_validate({
            "schema_version": 1,
            "manifest": manifest_payload(),
            "files": generated_files(),
            "commands": [],
        })


def test_execution_plan_v4_requires_design_and_compiler_identity() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate({**old_plan_payload(), "schema_version": 4})
```

Also test that the authoring loader rejects plan v3/v4 responses and the replay adapter accepts only manifested historical v3 fixtures.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_case_bundle.py tests/test_execution_plan.py tests/test_artifact_replay.py -q -p no:cacheprovider`

Expected: FAIL until the split and v4 schema exist.

- [ ] **Step 3: Implement strict models and narrow legacy reader**

```python
class CaseBundle(StrictModel):
    schema_version: Literal[1] = 1
    manifest: CaseManifest
    files: list[GeneratedFile] = Field(min_length=1)


class ExecutionPlan(StrictModel):
    schema_version: Literal[4] = 4
    compiled_from_design_sha256: str
    compiler_identities: dict[str, str]
    manifest: CaseManifest
    files: list[GeneratedFile] = Field(min_length=1)
    commands: list[NativeCommand] = Field(min_length=1)
```

Keep `GeneratedFile` and `NativeCommand` safety validation. Move v3 normalization under the replay-only module and prohibit it from canonical solve imports.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/authoring src/foampilot/plans tests/test_case_bundle.py tests/test_execution_plan.py tests/test_artifact_replay.py
git commit -m "feat: separate case bundles from execution plans"
```

### Task 2: Add deterministic plan-contributor protocols

**Files:**
- Modify: `src/foampilot/extensions/models.py`
- Create: `src/foampilot/extensions/planning.py`
- Create: `src/foampilot/extensions/mesh/__init__.py`
- Create: `src/foampilot/extensions/mesh/openfoam_mesh.py`
- Create: `src/foampilot/extensions/mesh/block_mesh.py`
- Create: `src/foampilot/extensions/solver/__init__.py`
- Create: `src/foampilot/extensions/solver/foundation10.py`
- Modify: `src/foampilot/extensions/registry.py`
- Test: `tests/test_plan_extensions.py`

**Interfaces:**
- Produces: `PlanContext`, `PlanFragment`, and `PlanContributor.contribute(context) -> PlanFragment`.
- Registers first-party provided-mesh, blockMesh, serial-solver, and parallel-solver contributors.

- [ ] **Step 1: Write failing contributor tests**

```python
def test_provided_mesh_contributes_check_but_no_mesh_generator() -> None:
    fragment = registry.plan_for(_provided_mesh_design())
    assert [(c.stage, c.executable) for c in fragment.commands] == [
        ("check", "checkMesh"),
        ("solve", "pisoFoam"),
    ]


def test_parallel_fragment_never_contains_mpirun() -> None:
    fragment = registry.plan_for(_parallel_design(ranks=4))
    assert all(c.executable != "mpirun" for c in fragment.commands)
    assert fragment.commands[-1].mpi_ranks == 4
```

Also test missing executable, target mismatch, duplicate step IDs, incompatible fragments, command timeout budgets, generated mesh order, and region arguments.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_plan_extensions.py -q -p no:cacheprovider`

Expected: FAIL because planning extensions do not exist.

- [ ] **Step 3: Implement pure plan contributors**

```python
class PlanContributor(Protocol):
    descriptor: CapabilityDescriptor

    def contribute(self, context: PlanContext) -> PlanFragment: ...
```

Contributors receive only frozen design, manifest, environment command facts, and resource budget. They return typed commands and required authored paths; they do not access filesystem or Runner. Phase 5 extends `PlanContext` with frozen observation-plan facts without changing the contributor authority boundary.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/extensions tests/test_plan_extensions.py
git commit -m "feat: register deterministic plan contributors"
```

### Task 3: Compile ExecutionPlan v4 from the frozen design

**Files:**
- Create: `src/foampilot/plans/compiler.py`
- Modify: `src/foampilot/plans/validation.py`
- Modify: `src/foampilot/plans/__init__.py`
- Test: `tests/test_plan_compiler.py`
- Test: `tests/test_execution_plan.py`

**Interfaces:**
- Consumes: `CaseDesign`, `CaseBundle`, `EnvironmentSnapshot`, `ResourceBudget`, `CapabilityRegistry`.
- Produces: `compile_execution_plan(...) -> ExecutionPlan`.

- [ ] **Step 1: Write failing compile and provenance tests**

```python
def test_compiler_uses_registered_contributors_only() -> None:
    plan = compile_execution_plan(...)
    assert plan.compiled_from_design_sha256 == design.design_sha256
    assert set(plan.compiler_identities) == {
        "foampilot.mesh.openfoam-provided",
        "foampilot.solver.foundation10-piso",
    }


def test_compiler_rejects_manifest_solver_mismatch() -> None:
    with pytest.raises(PlanCompilationError, match="DESIGN_MANIFEST_MISMATCH"):
        compile_execution_plan(design=_piso_design(), bundle=_ico_bundle(), ...)
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_plan_compiler.py tests/test_execution_plan.py -q -p no:cacheprovider`

Expected: FAIL because compiler is absent.

- [ ] **Step 3: Implement deterministic composition**

Resolve contributor IDs frozen in `CaseDesign`, sort fragments by `(stage_order, contributor_id, local_order)`, reject conflicting step IDs or output paths, then call the existing plan safety validation. Commands may only reference paths declared by the bundle, input bundles, or system-owned observation output paths.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/plans tests/test_plan_compiler.py tests/test_execution_plan.py
git commit -m "feat: compile execution plans from frozen designs"
```

### Task 4: Implement design-bound native case authoring

**Files:**
- Create: `src/foampilot/authoring/case_author.py`
- Modify: `src/foampilot/authoring/__init__.py`
- Modify: `src/foampilot/models/budgets.py`
- Modify: `src/foampilot/agent/prompts.py`
- Modify: `src/foampilot/agent/generation.py`
- Test: `tests/test_case_author.py`
- Test: `tests/test_native_case_generation.py`

**Interfaces:**
- Produces: `author_case(design, mesh_facts, target_facts, context, gateway, budget, trace, observation_plan=None) -> CaseBundle`.
- Adds `ModelStage.CASE_AUTHORING` and retires generation-stage `ExecutionPlan` output.

- [ ] **Step 1: Write failing prompt and response tests**

```python
def test_author_prompt_is_bound_to_frozen_design(scripted_gateway) -> None:
    bundle = author_case(...)
    request = scripted_gateway.requests[0]
    assert design.design_sha256 in request.user_prompt
    assert "commands" not in request.system_prompt.lower()
    assert bundle.manifest.solver == design.solver.executable


def test_author_response_with_commands_is_rejected() -> None:
    with pytest.raises(ModelSchemaError):
        run_author({**valid_bundle_payload(), "commands": []})
```

Also test public-bundle overwrite, protected path leakage, target version, full related-file response, and one logical author request.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_case_author.py tests/test_native_case_generation.py -q -p no:cacheprovider`

Expected: FAIL because the new author is absent.

- [ ] **Step 3: Implement one bundle-only model exchange**

The system prompt says:

```text
Implement the frozen CaseDesign exactly. Return one coherent CaseBundle containing the manifest
and every authored native file. Do not return commands, revise design decisions, reinterpret mesh
roles, overwrite input bundles, or access undeclared capabilities.
```

Remove current prompt language asking the model to choose mesh workflow, solver, numerical settings, or commands. Keep all current protected-path and schema normalization defenses.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/authoring src/foampilot/models/budgets.py src/foampilot/agent/prompts.py src/foampilot/agent/generation.py tests/test_case_author.py tests/test_native_case_generation.py
git commit -m "feat: author cases from frozen designs"
```

### Task 5: Verify native case conformance with `CaseDesign`

**Files:**
- Create: `src/foampilot/inspection/design_conformance.py`
- Modify: `src/foampilot/inspection/semantic.py`
- Modify: `src/foampilot/inspection/__init__.py`
- Test: `tests/test_design_conformance.py`
- Test: `tests/test_semantic_inspection.py`

**Interfaces:**
- Produces: `verify_design_conformance(design, bundle, mesh_facts, extensions) -> InspectionReport`.
- Extension validators consume only registered design payloads and relevant authored text.

- [ ] **Step 1: Write failing contradiction tests**

```python
@pytest.mark.parametrize(
    "mutator",
    [change_solver, change_region_role, change_boundary_type, change_end_time],
)
def test_bundle_cannot_contradict_frozen_design(mutator) -> None:
    report = verify_design_conformance(design, mutator(bundle), mesh_facts, registry)
    assert report.passed is False
    assert "DESIGN_CONFORMANCE" in report.issues[0].code
```

Also test missing required model file, extra active physical model, provided-mesh overwrite, field/region mismatch, and an unregistered semantic relation producing `not_verified` rather than a false pass.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_design_conformance.py tests/test_semantic_inspection.py -q -p no:cacheprovider`

Expected: FAIL because conformance verification is absent.

- [ ] **Step 3: Implement common and extension validators**

Common checks compare manifest solver, family, regime, region/field/patch identities, required outputs, input-bundle ownership, and time design. Extension validators check only relations declared in their descriptor. They return `verified`, `contradiction`, or `not_verified`; only high-confidence contradictions block.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/inspection tests/test_design_conformance.py tests/test_semantic_inspection.py
git commit -m "feat: verify case design conformance"
```

### Task 6: Add repair policy and `NumericalRepairEnvelope`

**Files:**
- Create: `src/foampilot/repair/__init__.py`
- Create: `src/foampilot/repair/models.py`
- Create: `src/foampilot/repair/envelope.py`
- Test: `tests/test_numerical_repair_envelope.py`

**Interfaces:**
- Produces: `RepairPolicy`, `RepairCategory`, `NumericalRepairRule`, `NumericalRepairEnvelope`, `DesignChange`, `RepairProposal`.
- Produces: `authorize_repair(proposal, design, policy) -> RepairAuthorization`.

- [ ] **Step 1: Write failing allow/deny matrix tests**

```python
def test_smaller_delta_t_inside_envelope_is_authorized() -> None:
    result = authorize_repair(
        proposal=_numerical_change("numerics.delta_t", 0.02, 0.01),
        design=design_with_envelope(min_delta_t=0.002),
        policy=RepairPolicy(automatic_numerical_repair=True),
    )
    assert result.state == "AUTHORIZED_AUTOMATIC"


@pytest.mark.parametrize("path", [
    "materials.fluid.nu", "boundaries.inlet.value",
    "region_models.porous.coefficient", "time.end_time",
])
def test_physical_change_always_requires_confirmation(path: str) -> None:
    result = authorize_repair(_change(path), design, RepairPolicy())
    assert result.state == "CONFIRMATION_REQUIRED"
```

Also test disabled auto numerical repair, unknown path, wrong direction, lower/upper bounds, multiple changes with one violation, and model claims that omit a changed semantic field.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_numerical_repair_envelope.py -q -p no:cacheprovider`

Expected: FAIL because repair policy contracts are absent.

- [ ] **Step 3: Implement default-on but bounded authorization**

```python
class NumericalRepairRule(StrictModel):
    field_path: str
    operators: tuple[Literal["replace", "scale"], ...]
    direction: Literal["increase", "decrease", "either"]
    minimum: float | None = None
    maximum: float | None = None


class RepairPolicy(StrictModel):
    automatic_numerical_repair: bool = True
    model_diagnostic: bool = True
```

The envelope is frozen in `CaseDesign`; neither the repair model nor an extension may expand it during a run.

- [ ] **Step 4: Run focused tests**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/repair tests/test_numerical_repair_envelope.py
git commit -m "feat: bound automatic numerical repair"
```

### Task 7: Route repair through typed authorization and scoped patches

**Files:**
- Create: `src/foampilot/repair/coordinator.py`
- Modify: `src/foampilot/agent/repair_patch.py`
- Modify: `src/foampilot/agent/repair_scope.py`
- Modify: `src/foampilot/agent/repair.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Test: `tests/test_repair_policy.py`
- Test: `tests/test_repair_patch.py`
- Test: `tests/test_native_repair.py`
- Create: `tests/test_real_numerical_repair_policy_gate.py`

**Interfaces:**
- Consumes: deterministic failure classification, frozen design, policy, envelope, current bundle, and scoped evidence.
- Produces: `RepairDecision` with `mechanical_patch`, `authorized_numerical_patch`, `confirmation_questions`, or `finalize_failed`.

- [ ] **Step 1: Write failing repair-routing tests**

```python
def test_disabled_numerical_repair_makes_zero_repair_model_calls(gateway) -> None:
    decision = coordinate_repair(..., policy=RepairPolicy(
        automatic_numerical_repair=False,
    ))
    assert decision.state == "FINALIZE_FAILED"
    assert gateway.requests == []


def test_undeclared_file_change_is_rejected_even_with_allowed_design_change() -> None:
    with pytest.raises(RepairPatchError, match="UNDECLARED_SEMANTIC_CHANGE"):
        apply_authorized_repair(_proposal_with_hidden_change(), ...)
```

Also test deterministic mechanical repair, authorized numerical patch, physical confirmation, envelope violation, full-case regeneration rejection, and post-patch conformance/plan recompile.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_repair_policy.py tests/test_repair_patch.py tests/test_native_repair.py -q -p no:cacheprovider`

Expected: FAIL until repair routes through authorization.

- [ ] **Step 3: Implement category routing and derived design hashes**

Mechanical fixes use reviewed deterministic transforms. Numerical repair requests return `RepairProposal(design_changes, file_operations, expected_checks)`; apply changes to a derived design record whose parent is the frozen design and whose changed paths all match the envelope. Verify the file patch against that derived design, recompile the plan, run RiskGate, then execute. Physical changes emit concrete confirmation questions and finalize the current run without mutation.

- [ ] **Step 4: Run focused, real, and full tests**

Run Step 2, then `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_real_numerical_repair_policy_gate.py -q -p no:cacheprovider`, then the complete suite.

Expected: deterministic PASS; real gate proves enabled and disabled behavior with explicit evidence.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/repair src/foampilot/agent tests/test_repair_policy.py tests/test_repair_patch.py tests/test_native_repair.py tests/test_real_numerical_repair_policy_gate.py
git commit -m "feat: enforce repair authorization policy"
```

### Task 8: Switch canonical solve and delete model-authored commands

**Files:**
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/agent/__init__.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `src/foampilot/performance/plan_reuse.py`
- Modify: `src/foampilot/qualification/runner.py`
- Modify: `tests/test_import_boundary.py`
- Modify: `tests/test_native_agent_state_machine.py`
- Modify: `tests/test_verified_plan_reuse.py`
- Create: `tests/test_real_compiled_plan_gate.py`

**Interfaces:**
- Removes the Phase 2 legacy author bridge.
- Canonical flow becomes `CaseDesign → CaseAuthor → CaseVerifier → PlanCompiler → Runner`.
- Strict resume and plan reuse fingerprint design, bundle, compiler descriptors, and plan v4.

- [ ] **Step 1: Add failing canonical-path and forbidden-import tests**

```python
def test_canonical_author_response_schema_is_case_bundle() -> None:
    assert canonical_author_response_type() is CaseBundle


def test_production_authoring_never_constructs_native_command() -> None:
    violations = imports_or_calls("src/foampilot/authoring", "NativeCommand")
    assert violations == []
```

Add zero-v3-authoring tests, compiler-identity reuse invalidation, repair compiler invalidation, and qualification parity.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_import_boundary.py tests/test_native_agent_state_machine.py tests/test_verified_plan_reuse.py -q -p no:cacheprovider`

Expected: FAIL while the bridge remains.

- [ ] **Step 3: Remove the bridge and migrate all canonical callers**

Delete current model-to-ExecutionPlan request code and any normalizer used only for model-authored commands. Keep the v3 replay reader isolated. Persist `case-bundle.json`, `design-conformance.json`, and `execution-plan.json` v4 with hashes before materialization.

- [ ] **Step 4: Run full and real gates**

Run the complete deterministic suite and `tests/test_real_compiled_plan_gate.py`.

Expected: all deterministic tests pass; a small Foundation v10 case reaches solver start through a compiled plan.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot tests/test_import_boundary.py tests/test_native_agent_state_machine.py tests/test_verified_plan_reuse.py tests/test_real_compiled_plan_gate.py
git commit -m "refactor: compile all native execution plans"
```

### Task 9: Document Phase 3 authority boundaries

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/system-overview.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `tests/test_repository_docs.py`

**Interfaces:**
- Documents CaseBundle vs ExecutionPlan v4, CaseVerifier, repair switch, envelope, and mandatory confirmation categories.

- [ ] **Step 1: Add a failing docs contract**

```python
def test_docs_forbid_model_authored_commands() -> None:
    text = Path("docs/architecture.md").read_text()
    assert "Case Author 不生成命令" in text
    assert "ExecutionPlan v4" in text
    assert "automatic_numerical_repair" in text
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest tests/test_repository_docs.py -q -p no:cacheprovider`.

Expected: FAIL until docs update.

- [ ] **Step 3: Update docs and remove obsolete v3 authoring claims**

Search all tracked Markdown for `model.*ExecutionPlan`, `模型返回.*command`, and `schema v3`; retain v3 only where explicitly labeled historical replay.

- [ ] **Step 4: Run Phase 3 release gates**

Run `git diff --check` and the complete deterministic suite. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/architecture.md docs/system-overview.md docs/independent-agent-quickstart.md tests/test_repository_docs.py
git commit -m "docs: publish compiled execution authority"
```
