# FoamPilot Verified ExecutionPlan Reuse Design

**Status:** approved design, not yet implemented
**Date:** 2026-07-31
**Repository baseline:** local `main` working tree after Stage B

## 1. Purpose

FoamPilot's live authoring path is now bounded, observable, and recoverable,
but a successful model request still commonly takes 65–131 seconds before the
first OpenFOAM command. A transient provider failure can extend this to several
minutes. This is acceptable for blind qualification, but it is not a stable
demonstration path.

For demonstrations, the immediate objective is:

> Given exactly the same TaskSpec, reuse a previously verified ExecutionPlan
> and quickly execute a fresh native OpenFOAM case without depending on a
> model provider.

The reused plan must have previously completed its target solver normally.
Physics accuracy and external qualification are deliberately not part of
reuse eligibility. They remain later optimization targets.

## 2. Goals

- Start the target solver within 30 seconds on the validated workstation
  when a compatible verified plan is supplied.
- Make plan reuse explicit at the CLI and programmatic boundary.
- Require an exact TaskSpec match; never perform fuzzy task matching.
- Re-run policy, semantic inspection, mesh generation, mesh checking, and the
  target solver in a new case directory.
- Avoid all model credentials, provider requests, and model repair during a
  reuse run.
- Preserve immutable artifacts and record the complete source provenance.
- Leave live solve, qualification, and continuation behavior unchanged.

## 3. Non-goals

This phase does not add:

- an automatic or global cache;
- cache discovery, indexing, eviction, or synchronization;
- fuzzy TaskSpec matching;
- pre-generated template selection;
- copied meshes, time directories, or solver results;
- a deterministic OpenFOAM renderer or family compiler;
- model fallback after a reuse rejection;
- model repair after a reused solve;
- new physics-accuracy guarantees;
- qualification reuse.

## 4. User interface

The only new public entrypoint is an optional `solve` argument:

```bash
foampilot solve TASK.yaml \
  --reuse-verified-plan SOURCE_RUN_DIR \
  --run-root NEW_RUN_ROOT \
  --json
```

Without `--reuse-verified-plan`, `solve` retains the existing live authoring
path.

With `--reuse-verified-plan`:

- `SOURCE_RUN_DIR` must identify one finalized FoamPilot run;
- the CLI does not read OAuth credentials;
- the CLI does not construct a ModelGateway;
- no model name is required for execution;
- a reuse rejection is terminal and never falls back to live generation.

The argument is not added to `plan`, `resume`, or `qualify`. Qualification
therefore continues to measure live Agent authoring.

## 5. Architecture

The two authoring sources converge before execution:

```text
TaskSpec validation
        |
OpenFOAM environment discovery
        |
        +---------------- live ----------------+
        | route -> context -> model generation |
        |                                       |
        +--------------- reuse -----------------+
          verify source -> load frozen v3 plan
        |
normalize -> policy -> semantic/native inspection
        |
materialize a new empty case
        |
blockMesh/checkMesh/target solver
        |
public validation
        |
immutable summary and artifact manifest
```

The reuse branch replaces only the source of the ExecutionPlan. It does not
replace or bypass downstream safety and execution.

### 5.1 Component boundary

One focused module owns reuse validation:

```text
src/foampilot/plans/reuse.py
    VerifiedPlanLoader
    PlanReuseRecord
    PlanReuseError
```

Responsibilities:

- verify the source ArtifactStore manifest;
- load the source TaskSpec, final eligible attempt, and ExecutionPlan;
- compare the current task and environment with source evidence;
- verify mesh/check/target-solver completion evidence;
- return one canonical ExecutionPlan v3 plus a provenance record;
- expose stable machine-readable rejection codes.

It does not materialize files, execute commands, inspect physics accuracy, or
mutate either run.

### 5.2 NativeAgent boundary

`NativeAgent` permits a missing gateway only for explicit verified-plan reuse.
The intended programmatic invariant is:

```text
live solve:
    gateway is present
    reuse source is absent

reuse solve:
    gateway is absent
    reuse source is present
```

Supplying neither is invalid. Supplying both is invalid. This prevents hidden
fallback or accidental model use.

`resume` continues to require a gateway and cannot resume a reuse run into a
model stage.

## 6. Exact task identity

Reuse uses a canonical TaskSpec SHA256, not the input file's byte hash. The
digest is computed from the validated TaskSpec using stable JSON ordering and
serialization.

The comparison includes:

- task identity, title, prompt, and required outputs;
- OpenFOAM target;
- resource budget;
- acceptance requirements and public checks;
- public asset declarations and hashes;
- protected-path declarations.

Changing any TaskSpec field produces a different digest and rejects reuse.

Public assets are staged again from their current declared source and
independently hash-verified. Matching declarations are not sufficient when
the asset bytes are unavailable or invalid.

## 7. Source eligibility

The source run must satisfy all of the following.

### 7.1 Artifact integrity

- The source directory is a finalized FoamPilot run.
- `artifact-manifest.json` exists and verifies without issues.
- The source summary, TaskSpec, final-attempt plan, RunResult, and required
  logs are covered by the manifest.
- The source run remains byte-for-byte unchanged throughout reuse.

### 7.2 Plan compatibility

- The source plan is ExecutionPlan schema v3.
- The current canonical TaskSpec SHA256 equals the source digest.
- Public asset declarations and actual bytes match.
- OpenFOAM distribution and version match the current task and environment.
- The plan manifest's solver is installed in the current environment.
- Every command remains within the current TaskSpec and environment MPI
  limits.

The model name, provider, Knowledge hashes, and Skill hashes do not have to
match. The completed plan is the reused object, and current normalization,
policy, and semantic inspection remain authoritative.

### 7.3 Execution evidence

The loader scans source attempts from newest to oldest and selects the most
recent attempt that satisfies all reuse conditions. It requires:

- a declared mesh-stage command that returned zero and did not time out;
- a declared check-stage `checkMesh` command that returned zero and reported
  `Mesh OK`;
- a declared solve-stage command for the manifest solver;
- a zero target-solver return code;
- no target-solver timeout;
- a normal solver-end marker in the target-solver log.

A run that only started the solver is not eligible. A run whose target solver
ended normally remains eligible even when public validation or external
physics qualification failed.

The selected attempt's ExecutionPlan is reused so that an evidence-scoped
repair which produced the latest successful source attempt is preserved.

## 8. Reuse execution semantics

After source validation, FoamPilot:

1. records the reuse provenance;
2. normalizes the loaded plan again;
3. applies the current typed-command policy;
4. stages current public assets;
5. materializes all plan-authored files into a new empty attempt directory;
6. runs current semantic and native inspection;
7. executes every declared OpenFOAM command through the existing Runner;
8. performs current public validation;
9. finalizes the new immutable run.

No file is copied from the source case directory. In particular, FoamPilot
does not copy:

- `constant/polyMesh`;
- decomposed processor directories;
- time directories;
- `.foampilot` logs;
- post-processing data;
- source validation reports.

The new run may independently reproduce the same case bytes because they are
declared in the reused plan.

## 9. Model and repair behavior

Verified-plan reuse is a provider-independent deterministic mode:

- no authentication file is opened;
- no ModelGateway is constructed;
- no route, generation, or repair request is made;
- `model_calls` is zero;
- `transport_attempts` is zero;
- no model trace is created.

Reporting treats the reuse record as authoritative authoring metadata and
reports zero logical model requests, transports, and model seconds even though
no `model-configuration.json` is required.

If execution or public validation fails, the reuse run ends with the existing
native failure layer and preserves the evidence. It does not request a model
repair.

This is an intentional difference from live solve. The demonstration goal is
stable native execution, not online plan improvement.

## 10. Workflow and artifacts

The reuse workflow omits `ROUTING_READY`, `CONTEXT_READY`, and
`MODEL_GENERATION_STARTED`. It adds:

```text
PLAN_REUSE_VALIDATION_STARTED
PLAN_REUSE_VALIDATED
PLAN_READY
```

On rejection it records:

```text
PLAN_REUSE_VALIDATION_STARTED
PLAN_REUSE_REJECTED
RUN_FINALIZED
```

The new run contains `plan-reuse.json`:

```yaml
schema_version: 1
authoring_source: verified_plan_reuse
source_run_id: run-...
source_manifest_sha256: ...
source_attempt: 2
task_sha256: ...
execution_plan_schema: 3
source_run_native_status: PUBLIC_VALIDATION_FAILED
source_plan_sha256: ...
eligibility:
  artifact_manifest_verified: true
  task_match: true
  assets_verified: true
  environment_compatible: true
  mesh_completed: true
  check_mesh_completed: true
  target_solver_completed: true
evidence_paths:
  - attempt-02/run-result.json
  - attempt-02/case/.foampilot/logs/02-check_mesh.stdout.log
  - attempt-02/case/.foampilot/logs/04-solve.stdout.log
```

The raw source path is not required in the portable artifact. Source run ID
and manifest hash provide stable provenance. The current CLI may report the
resolved source path to the invoking user without sending it to a model.

A reused run is not a continuation child. `parent_run` remains unset.
`plan-reuse.json` carries a separate plan-source relationship.

## 11. Rejection semantics

Reuse failures return the top-level status:

```text
PLAN_REUSE_REJECTED
```

with one stable reason code:

```text
SOURCE_NOT_FINALIZED
SOURCE_MANIFEST_INVALID
TASK_HASH_MISMATCH
PUBLIC_ASSET_MISMATCH
OPENFOAM_TARGET_MISMATCH
SCHEMA_UNSUPPORTED
SOLVER_UNAVAILABLE
RESOURCE_BUDGET_MISMATCH
SOURCE_MESH_NOT_COMPLETED
SOURCE_CHECK_MESH_NOT_COMPLETED
SOURCE_SOLVER_NOT_COMPLETED
SOURCE_EVIDENCE_INCOMPLETE
```

The failure is deterministic, non-retryable, and occurs before case
materialization. It is not classified as a provider or environment failure
unless current environment discovery itself fails.

Its primary failure uses `FailureDomain.PLAN` and the stable rejection reason
as the failure code. No new broad failure domain is introduced.

Current plan policy or semantic inspection can still reject a formerly valid
plan after reuse validation. Those failures retain the existing
`PLAN_INVALID` or `STATIC_INSPECTION_FAILED` layers rather than being relabeled
as source eligibility failures.

## 12. Security and leakage

- The source run is read-only.
- Source manifest verification precedes plan loading.
- The reused plan is subjected to the current path and typed-command policy.
- Protected paths remain unavailable to case files and commands.
- No target tutorial, evaluator-private reference, or golden result is added
  to the Agent-visible path.
- Source case files and solver outputs are not treated as public assets.
- Reuse does not broaden executable, filesystem, MPI, timeout, or memory
  authority.

## 13. Tests

### 13.1 Unit tests

- Valid schema-v3 source produces a PlanReuseRecord and plan.
- TaskSpec mismatch is rejected.
- Public asset declaration or byte mismatch is rejected.
- Source manifest corruption is rejected.
- Foundation distribution/version mismatch is rejected.
- Schema v2 is rejected.
- Missing current solver is rejected.
- MPI rank incompatibility is rejected.
- Mesh failure is rejected.
- `checkMesh` failure or missing `Mesh OK` is rejected.
- Solver timeout, non-zero return, or missing normal-end evidence is rejected.
- Public-validation failure with normal solver completion remains eligible.

### 13.2 State-machine tests

- Reuse succeeds with `gateway=None`.
- Reuse rejects a non-null gateway.
- Live solve rejects a missing gateway.
- Reuse emits no routing, context, generation, or repair events.
- Reuse emits the new validation events and `plan-reuse.json`.
- Provider/auth loading is not attempted.
- Model-call and transport-attempt metrics remain zero.
- Reused public-validation failure finalizes without repair.
- Source artifacts are byte-identical before and after the new run.
- New attempts contain no copied source mesh, time, log, or result directory.
- Current normalization, policy, and semantic inspection still execute.

### 13.3 Regression tests

- Existing live solve behavior remains unchanged.
- Qualification always uses live authoring.
- Strict generation and repair continuation remain unchanged.
- Frozen artifact replay remains unchanged.
- All deterministic repository tests remain green.

### 13.4 Real OpenFOAM gate

Use one previously verified, non-tutorial, schema-v3 run:

```text
no model credentials
no provider network
fresh run directory
verified plan reuse
blockMesh
checkMesh
target solver
artifact verification
```

The gate must prove:

- target solver starts within 30 seconds of CLI invocation on the validated
  workstation, measured from CLI start to the solve-stage
  `OPENFOAM_STEP_STARTED` event;
- model calls and transport attempts are zero;
- `blockMesh`, `checkMesh`, and the target solver actually run;
- the target solver ends normally;
- the new artifact manifest verifies without issues;
- the source run remains unchanged.

The 30-second value is a workstation demonstration gate, not a portable
cross-platform service-level guarantee.

## 14. Documentation changes

Implementation will update:

- `README.md` with the explicit reuse command and claim boundary;
- `AGENTS.md` with reuse safety rules;
- `docs/architecture.md` with the two authoring sources;
- `docs/independent-agent-quickstart.md` with live versus reuse usage;
- reporting documentation with `authoring_source` and zero-model metrics.

## 15. Acceptance boundary

This feature will demonstrate:

- a reproducible, provider-independent path from an exact known TaskSpec to a
  fresh native OpenFOAM solve;
- deterministic source validation and provenance;
- real mesh generation, mesh checking, and target-solver execution;
- a substantially faster warm demonstration path.

It will not demonstrate:

- live model authoring latency improvement;
- generalization to a new TaskSpec;
- physics accuracy improvement;
- automatic cache selection;
- qualification performance.

Those claims remain separate and must not be inferred from a successful
verified-plan reuse demonstration.
