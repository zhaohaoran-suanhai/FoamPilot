# Task Ingress Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the natural-language entry ordering so immutable mesh facts constrain TaskDraft extraction and engineering choices reach the existing design/risk stages.

**Architecture:** Split unit-independent topology facts from unit-aware `InputMeshFacts`, build one deterministic TaskBuilder ingress context from public assets, reconcile model provenance and gate ownership, then compile only authoritative facts into TaskSpec v3. The canonical solve path remains `InputMeshFacts -> Intent -> Design -> Risk -> Author`.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, Foundation OpenFOAM 10.

## Global Constraints

- Do not add porous-blockage, patch-name, zone-name, solver-name, or tutorial-specific branches.
- Do not infer the polyMesh length unit from raw coordinates or source path.
- Do not expose raw polyMesh members to a model.
- Do not weaken concrete confirmation or information-required RiskGate states.
- Preserve atomic bundle hashing, staging and model overwrite protection.

---

### Task 1: Unit-independent polyMesh topology facts

**Files:**
- Modify: `src/foampilot/preprocessing/models.py`
- Modify: `src/foampilot/preprocessing/poly_mesh.py`
- Modify: `src/foampilot/preprocessing/__init__.py`
- Test: `tests/test_poly_mesh_inspector.py`

**Interfaces:**
- Produces: `inspect_poly_mesh_topology(bundle_root: Path, bundle: AssetBundle) -> PolyMeshTopologyFacts`.
- Preserves: `inspect_poly_mesh(..., length_unit: LengthUnit) -> InputMeshFacts`.

- [ ] Add a failing test asserting topology counts, unscaled bounds, patches and zones without a declared unit.
- [ ] Run the focused test and confirm it fails because the topology contract is absent.
- [ ] Extract the existing parser result into `PolyMeshTopologyFacts` and make the unit-aware inspector scale that result.
- [ ] Run all polyMesh inspector and asset adapter tests.

### Task 2: Deterministic TaskBuilder ingress context

**Files:**
- Create: `src/foampilot/taskbuilder/context.py`
- Modify: `src/foampilot/taskbuilder/models.py`
- Modify: `src/foampilot/taskbuilder/__init__.py`
- Modify: `src/foampilot/cli/main.py`
- Test: `tests/test_taskbuilder_cli.py`
- Test: `tests/test_task_extractor.py`

**Interfaces:**
- Produces: `TaskIngressContext` containing Foundation v10 target, asset bundles and topology facts.
- Produces: `build_task_ingress_context(assets, asset_root) -> TaskIngressContext`.
- Extends: `extract_task_draft(..., ingress_context=...)`.

- [ ] Add a failing CLI test proving an atomic polyMesh is inspected before the extraction gateway and topology facts enter the model request.
- [ ] Add a failing asset-error test proving malformed bundles stop before a model call.
- [ ] Build the context using the registered first-party asset adapter and persist compact topology facts in TaskDraft.
- [ ] Run TaskBuilder CLI and extractor tests.

### Task 3: Provenance and gate-ownership reconciliation

**Files:**
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Modify: `src/foampilot/taskbuilder/validation.py`
- Modify: `src/foampilot/taskbuilder/models.py`
- Test: `tests/test_task_extractor.py`
- Test: `tests/test_task_draft_validation.py`

**Interfaces:**
- Produces: balanced-quote evidence normalization.
- Produces: deterministic provided-mesh route facts.
- Produces: input-only TaskDraft blocking decisions.

- [ ] Add a failing test showing `“采用单相、不可压缩、牛顿流体。”` verifies the inner user quotation.
- [ ] Add a failing test showing omitted solver/material/time/resource details do not create TaskBuilder blockers.
- [ ] Add a failing test showing unknown provided-mesh length unit remains one blocking input gap.
- [ ] Implement reconciliation, discard model-created authority and retain unconfirmed interpretations only as audit data.
- [ ] Run extractor, validation, Desktop workspace and semantic regression tests.

### Task 4: Authoritative TaskSpec compilation

**Files:**
- Modify: `src/foampilot/taskbuilder/compiler.py`
- Test: `tests/test_task_compiler.py`
- Test: `tests/test_taskbuilder_semantics.py`

**Interfaces:**
- Produces: deterministic `geometry.input` and `mesh.intent` for atomic provided meshes.
- Ensures: only authoritative facts enter `TaskSpec.explicit_facts`.

- [ ] Add a failing test compiling a provided mesh with unit into `openfoam_mesh/provided` without solver or time values.
- [ ] Add a failing test proving unconfirmed model inference is absent from TaskSpec explicit facts.
- [ ] Implement deterministic geometry/mesh assembly and preserve visible defaults.
- [ ] Run compiler, TaskSpec and router tests.

### Task 5: Focused and repository verification

**Files:**
- Modify: `README.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Documents: actual pre-draft mesh inspection and gate ownership.

- [ ] Run focused TaskBuilder, preprocessing, intent, design, risk and native-state tests.
- [ ] Run formatting/static checks and the full pytest suite.
- [ ] Build distribution artifacts and run distribution-content tests if source tests are green.

### Task 6: Real original-input gate

**Files:**
- Runtime artifacts only under `/tmp/foampilot-porous-ingress-gate-*`.

**Interfaces:**
- Consumes: the original broad request and original atomic polyMesh path.
- Produces: TaskDraft/TaskSpec or a minimal truthful input question, followed by canonical solve artifacts after required user information is supplied.

- [ ] Run explicit Foundation v10 preflight and model doctor.
- [ ] Run `foampilot task draft` with the unchanged request and polyMesh directory.
- [ ] Verify patch/zone facts come from preprocessing and no solver/material/time question is emitted by TaskBuilder.
- [ ] If the unit is still absent, stop truthfully at that single hard input question; do not infer it.
- [ ] Once authoritative input is complete, compile TaskSpec and run canonical `foampilot solve` without hand-authoring a case.
- [ ] Inspect RiskDecision, case bundle, execution plan, logs, metrics, acceptance report and artifact manifest before claiming a closed loop.
