# FoamPilot Stage B Routing and Semantic Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. This plan
> is intentionally executed inline; do not dispatch subagents.

**Goal:** Add evidence-based capability routing, slot-bounded public context,
ExecutionPlan v3, a region-aware CaseManifest, safe MPI normalization, and
provenance-bearing semantic inspection without changing the Agent-authored
native-case boundary.

**Architecture:** A deterministic router runs before retrieval and uses a
small model route request only when public evidence remains ambiguous. A
ContextAssembler selects at most one knowledge entry per declared slot and at
most two Skills. The authoring model returns one ExecutionPlan v3 containing a
thin CaseManifest, complete native files, and staged typed commands. A
normalizer may only unwrap an unambiguous MPI launcher; safety policy and
semantic inspection remain deterministic.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, Foundation OpenFOAM
v10, existing ModelGateway, ArtifactStore, Runner, and qualification layer.

## Global constraints

- Preserve TaskSpec schema v1.
- Canonical authoring emits only ExecutionPlan schema v3.
- `NativeCommand.stage` is the only command-stage representation.
- `CaseManifest.regions` is required from the first v3 response.
- The Agent continues to write every native OpenFOAM case file.
- No renderer, per-case adapter, MCP, multi-agent graph, or tutorial access.
- A blocking semantic rule requires Foundation v10 source and test
  provenance.
- Uncertain dimensions, numerics, and unregistered solver families warn
  rather than block.
- Historical v2 plans are read only through frozen replay plus reviewed
  manifest overlays and cannot strict-resume.
- Preserve the current safety Runner, evaluator, repair limit, immutable
  attempt model, provider gateway, and continuation lineage.
- Do not commit or push during this implementation.

---

### Task 1: Capability profile and deterministic routing

**Files:**

- Create: `src/foampilot/routing/models.py`
- Create: `src/foampilot/routing/registry.py`
- Create: `src/foampilot/routing/confidence.py`
- Create: `src/foampilot/routing/router.py`
- Create: `src/foampilot/routing/__init__.py`
- Test: `tests/test_capability_routing.py`

**Interfaces:**

- Produces:
  `route_capability(task, environment, corpus, gateway=None, budget=None,
  trace=None) -> CapabilityProfile`.
- `CapabilityProfile.confidence` is computed by
  `calculate_confidence(RouteEvidenceState)`, never accepted from a model.
- `RouteSuggestion` contains only `candidate`, `evidence`, and
  `unresolved_questions`.

- [x] Write tests proving an explicit installed solver routes `high`, an
  explicit missing solver is `ROUTING_UNRESOLVED`, a unique compatible
  knowledge candidate routes `medium`, and multiple candidates remain
  unresolved after a model suggestion.
- [x] Run the tests and verify they fail because `foampilot.routing` does not
  exist.
- [x] Implement strict profile/evidence/suggestion models and a compact
  solver-family registry.
- [x] Implement public TaskSpec evidence extraction, executable cross-check,
  knowledge candidate formation, optional small route request, and
  deterministic confidence.
- [x] Run the routing tests and existing knowledge tests to green.

### Task 2: Slot-based context and dynamic Skill selection

**Files:**

- Create: `src/foampilot/context/models.py`
- Create: `src/foampilot/context/slots.py`
- Create: `src/foampilot/context/skill_registry.py`
- Create: `src/foampilot/context/assembler.py`
- Create: `src/foampilot/context/__init__.py`
- Modify: `src/foampilot/agent/context.py`
- Test: `tests/test_context_assembler.py`
- Modify: `tests/test_agent_context.py`

**Interfaces:**

- Produces:
  `assemble_agent_context(task, capability, package_root=None,
  repair=False, payload_limit_bytes=32768) -> AgentContext`.
- `AgentContext` records `knowledge_slots`, `missing_slots`,
  `selected_knowledge_ids`, hashes, `skill_names`, and rendered payloads.

- [x] Write tests proving one entry per slot, leakage filtering, empty slots
  instead of unrelated fill, parallel/error conditional slots, two-Skill
  maximum, and whole-entry payload pruning.
- [x] Verify the new tests fail against the current top-five context loader.
- [x] Implement slot definitions and one-query-per-slot retrieval using
  solver and knowledge-type filters.
- [x] Implement general plus optional buoyant/rhoCentral family Skill routing.
- [x] Replace the old loader implementation with a thin call into the
  assembler; do not retain two retrieval paths.
- [x] Run context, knowledge, Skill, and package-boundary tests to green.

### Task 3: Region-aware CaseManifest and ExecutionPlan v3

**Files:**

- Create: `src/foampilot/manifests/models.py`
- Create: `src/foampilot/manifests/validation.py`
- Create: `src/foampilot/manifests/__init__.py`
- Modify: `src/foampilot/plans/models.py`
- Modify: `src/foampilot/plans/__init__.py`
- Test: `tests/test_case_manifest.py`
- Modify: `tests/test_execution_plan.py`

**Interfaces:**

- `CaseManifest` contains solver, family, regime, physics, mesh,
  dimensionality, required `regions`, fields, patches, and model declarations.
- `ExecutionPlan.schema_version` is literal `3` and contains `manifest`.
- `NativeCommand.stage` is one of mesh, check, initialize, decompose, solve,
  reconstruct, or postprocess.

- [x] Write failing schema tests for a single-region plan, a CHT multi-region
  plan, duplicate region/field/patch identities, missing region references,
  and the absence of `command_stages`.
- [x] Implement strict manifest models and cross-field identity validation.
- [x] Upgrade the canonical plan and command models to v3.
- [x] Mechanically migrate current source tests and fake provider payloads to
  include a reviewed minimal manifest and command stages.
- [x] Run plan, generation, repair, Runner, and state-machine tests to green.

### Task 4: Safe MPI command normalizer and plan policy

**Files:**

- Create: `src/foampilot/plans/normalizer.py`
- Modify: `src/foampilot/plans/validation.py`
- Modify: `src/foampilot/plans/__init__.py`
- Test: `tests/test_plan_normalizer.py`

**Interfaces:**

- Produces:
  `normalize_execution_plan(plan, task, available_executables) ->
  NormalizationResult`.
- A normalization record contains the original launcher, solver, ranks, and
  command index.

- [x] Write failing tests for `mpirun`, `mpiexec`, and `orterun` positive
  forms and hostfile, shell, unknown solver, unknown extra argument, rank
  conflict, and budget negative forms.
- [x] Implement only the approved launcher-unwrapping transformation.
- [x] Add a policy error for any launcher that remains after normalization.
- [x] Run normalizer and execution-policy tests to green.

### Task 5: Provenance-bearing semantic inspection

**Files:**

- Create: `src/foampilot/manifests/family_contracts.py`
- Create: `src/foampilot/inspection/semantic.py`
- Modify: `src/foampilot/inspection/models.py`
- Modify: `src/foampilot/inspection/native_case.py`
- Modify: `src/foampilot/inspection/__init__.py`
- Test: `tests/test_semantic_inspection.py`

**Interfaces:**

- `SemanticRuleProvenance` contains `rule_id`,
  `openfoam_distribution="foundation"`, `openfoam_version="10"`, `source`,
  `severity`, and non-empty `tested_by`.
- `inspect_semantics(case_root, task, plan) -> InspectionReport`.
- Semantic errors merge into `issues`; warnings merge into `advisories`.

- [x] Write failing tests for solver/solve mismatch,
  controlDict/application mismatch, field path/region mismatch, command-stage
  mismatch, MPI decomposition, requested reconstruction, and a valid
  multi-region manifest.
- [x] Write tests proving every blocking semantic issue has complete
  provenance and unknown family uncertainty is advisory only.
- [x] Implement high-confidence generic rules and a minimal reviewed
  icoFoam family contract; do not encode complete dictionaries.
- [x] Compose semantic and existing native inspection without duplicating
  materialization or policy checks.
- [x] Run semantic, native inspection, and frozen-case tests to green.

### Task 6: Canonical authoring and workflow migration

**Files:**

- Modify: `src/foampilot/agent/prompts.py`
- Modify: `src/foampilot/agent/generation.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/artifacts/models.py`
- Test: `tests/test_native_agent_state_machine.py`
- Test: `tests/test_native_agent_cli.py`
- Test: `tests/test_continuation.py`

**Interfaces:**

- New solve data flow:
  environment → capability profile checkpoint → slot context checkpoint →
  one v3 bundle → normalize → policy → materialize → semantic/native inspect.
- Resume reuses the frozen parent capability profile and recomputes only the
  compatible public context; it never repeats a route model call.

- [x] Write failing state-machine tests for route/context artifacts,
  normalized-plan evidence, v3-only generation, route failure status, and
  continuation compatibility.
- [x] Update the prompt to request manifest and staged commands in the same
  response.
- [x] Insert routing and context before authoring; persist
  `capability-profile.json` and richer `agent-context.json`.
- [x] Normalize before plan policy and persist normalization records.
- [x] Update compatibility fingerprint schema to v3 and include selected
  Skill IDs.
- [x] Run state-machine, CLI, provider, workflow, and continuation tests.

### Task 7: Frozen v2 replay with reviewed v3 overlays

**Files:**

- Create: `src/foampilot/plans/legacy.py`
- Create:
  `tests/fixtures/artifact-replay/*/case-manifest-overlay.json`
- Modify: `tests/fixtures/artifact-replay/index.yaml`
- Modify: `tests/test_artifact_replay.py`
- Modify: `tools/freeze_artifact_replay.py`

**Interfaces:**

- `load_frozen_v2_plan(payload, overlay) -> ExecutionPlan` is test/replay
  compatibility only and is not exported as a canonical authoring fallback.

- [x] Write failing tests that direct v2 validation is rejected, every replay
  fixture has a reviewed overlay, and overlay conversion yields v3.
- [x] Implement the narrow legacy reader and overlay hash validation.
- [x] Add overlays for all six bounded fixtures, including real fluid/solid
  regions for CHT.
- [x] Replay normalizer, policy, semantic inspection, and native inspection on
  copied cases.
- [x] Verify known successes are not newly blocked and the known failure
  preserves its failure layer.

### Task 8: Documentation, packaging, and Stage B gates

**Files:**

- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `docs/qualification.md`
- Create: `docs/reports/2026-07-31-stage-b-acceptance.md`

- [x] Run all deterministic tests and compile all source, tests, and tools.
- [x] Build an isolated wheel and validate package data/import boundaries.
- [x] Run the smallest real non-tutorial OpenFOAM gate before broader
  qualification.
- [x] Run each existing official-six task once only after deterministic and
  frozen replay gates pass; record provider/environment blocks separately
  from CFD failures.
- [x] Update docs with v3 artifacts, route/context observability, warning/error
  semantics, and the exact evidence boundary.
- [x] Audit secrets, protected paths, tutorial leakage, diff whitespace, Git
  status, and confirm no `docs/superpowers` changes.
