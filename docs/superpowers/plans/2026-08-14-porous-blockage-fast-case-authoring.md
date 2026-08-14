# Porous Blockage Fast Case Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use FoamPilot's canonical plan-only path to author and materialize the provided-polyMesh Foundation OpenFOAM 10 `pisoFoam` case without launching the CFD solver.

**Architecture:** Preserve the immutable intent/design/risk/author pipeline. Record the user's reviewed engineering values in a derived TaskSpec, run `foampilot plan` so authoring and plan validation occur without execution, then stage the verified public mesh and atomically materialize only the prevalidated generated files.

**Tech Stack:** Python 3.12, FoamPilot TaskSpec v3, NativeAgent plan-only workflow, Foundation OpenFOAM 10, provided `constant/polyMesh`.

## Global Constraints

- Work directly on the current `main` checkout; do not create a branch or worktree.
- Preserve every program file's frozen responsibility boundary.
- Use `endTime = 80000 s`, `deltaT = 100 s`, `writeInterval = 20000 s`, and do not start `pisoFoam`.
- Preserve the provided polyMesh byte-for-byte and install it only at `constant/polyMesh`.
- Success means a manifested plan-only run, a complete materialized case, design conformance, static inspection, and generated-case `checkMesh`; it does not mean a CFD solution exists.

---

### Task 1: Freeze the approved fast authoring input

**Files:**
- Create: `/tmp/foampilot-porousblockage-fast-case-20260814/task-pisofoam-fast-case-only.yaml`
- Reference: `/tmp/foampilot-porousblockage-user-test-jBv81G/task-pisofoam-selected.yaml`

**Interfaces:**
- Consumes: the validated TaskSpec v3 and the reviewed 21-field design candidate set.
- Produces: a validated TaskSpec v3 whose engineering values are concrete user-confirmed facts.

- [ ] Copy the existing validated TaskSpec to the new temporary experiment directory.
- [ ] Update only the run scope and reviewed field facts: retain all non-time values, set `time.end` to `80000 s`, `numerics.delta_t` to `100 s`, retain the `20000 s` output interval, and record the confirmed discretization, linear solvers, and porous coordinate system.
- [ ] Run `foampilot validate` and require status `PASS` before any model call.

### Task 2: Run canonical plan-only authoring

**Files:**
- Create: `/tmp/foampilot-porousblockage-fast-case-20260814/execution-plan.json`
- Create: `/tmp/foampilot-porousblockage-fast-case-20260814/plan-runs/run-*`

**Interfaces:**
- Consumes: the validated fast TaskSpec and the original public-asset root.
- Produces: an immutable, manifested `COMPLETED` plan-only run containing `case-bundle.json`, `design-conformance.json`, and `execution-plan.json`.

- [ ] Run `foampilot plan` with Foundation OpenFOAM 10, one MPI rank, sandbox-preferred execution policy, and the configured authenticated model.
- [ ] Require no `INFORMATION_REQUIRED`, `CONFIRMATION_REQUIRED`, or capability conflict; otherwise stop with the exact risk-gate evidence.
- [ ] Verify the run artifact manifest and require `design-conformance.json` to report `passed: true`.

### Task 3: Reconcile a uniquely misclassified cell-zone observation scope

**Files:**
- Modify: `src/foampilot/simulation/intent.py`
- Test: `tests/test_intent_interpreter.py`
- Reference: `docs/superpowers/specs/2026-08-14-intent-cell-zone-scope-reconciliation-design.md`

**Interfaces:**
- Consumes: model-emitted `SimulationIntent` plus authoritative `InputMeshFacts` already available to `interpret_intent()`.
- Produces: an intent whose uniquely identifiable cell-zone observations use `ObservationScope(kind="cell_zone", ...)` before strict observation planning.

- [ ] Add a failing interpreter test with a `region` scope named `porous` and one authoritative cell zone named `porous`; require the reconciled scope to be `cell_zone`, its region binding to be `None`, and a stable audit warning.
- [ ] Run the focused test and require it to fail because the current reconciler preserves `kind="region"`.
- [ ] Add tests proving a true mesh region remains `region` and duplicate cell-zone names across regions remain unresolved.
- [ ] Implement one helper in `simulation/intent.py`, apply it to observation and acceptance scopes during `_reconcile_intent()`, and clarify the model system prompt without changing `ObservationPlanner`.
- [ ] Run `tests/test_intent_interpreter.py` and `tests/test_observation_planner.py`, then run the complete deterministic suite.
- [ ] Resume Task 2 with the unchanged confirmed fast TaskSpec and require observation planning to pass before Author starts.

### Task 4: Materialize and inspect the case without solving

**Files:**
- Create: `/tmp/foampilot-porousblockage-fast-case-20260814/case/`

**Interfaces:**
- Consumes: the verified execution plan, its immutable public-asset snapshot, and TaskSpec.
- Produces: a complete OpenFOAM case directory; it launches no solver command.

- [ ] Call the existing `stage_public_assets()` API to install the snapshotted polyMesh at `constant/polyMesh`.
- [ ] Call the existing `materialize_case()` API to atomically write only the prevalidated generated files.
- [ ] Run FoamPilot static case inspection and Foundation v10 `checkMesh` against the materialized case.
- [ ] Verify `system/controlDict` contains `application pisoFoam`, `endTime 80000`, `deltaT 100`, and `writeInterval 20000`; verify the porous source selects only `porousBlockage`.
- [ ] Confirm no solver log or numerical time directory was created, then report the case path and evidence boundary.
