# Architecture

FoamPilot has one supported path from a public TaskSpec to an evidence-scoped
result.

## Components

- `tasks`: strict public requirements and resource budgets;
- `knowledge`: reviewed Foundation OpenFOAM v10 facts with provenance;
- `skills`: portable behavioral guidance for case authoring and selected
  solver families;
- `routing`: evidence-backed solver-family selection and system-computed
  confidence;
- `context`: one bounded public knowledge entry per semantic slot plus routed
  general/family Skills;
- `manifests`: thin region-aware case declarations and provenance-bearing
  family contracts;
- `models`: one-exchange provider clients plus the shared `ModelGateway`,
  retry/deadline policy, transport traces, lineage budgets, and a
  thread-safe circuit breaker;
- `agent`: prompt construction, complete case-bundle authoring, and bounded
  repair;
- `workflow`: ordered durable events, exclusive checkpoints, v2 run state,
  strict compatibility fingerprints, and immutable parent/child
  continuation;
- `plans`: ExecutionPlan v3, complete generated files, staged typed native
  commands, and one narrow safe MPI launcher normalizer;
- `inspection`: case-local static safety plus high-confidence cross-file
  semantic checks;
- `runtime`: Foundation v10 discovery, networkless bubblewrap execution,
  explicit MPI launch, budgets, and logs;
- `validation`: evaluator-owned checks over commands, logs, and written
  fields;
- `artifacts`: immutable attempts and SHA256 manifests;
- `qualification`: role-aware suite execution and external physics
  comparisons using compact packaged references.

## Data flow

After environment discovery, the deterministic router creates a
`CapabilityProfile`. It uses explicit task facts, installed executables, and
reviewed solver-family metadata. A model route request is allowed only for an
ambiguous candidate set and cannot promote its own confidence. Low-confidence
or incomplete routes stop before full case authoring.

The ContextAssembler then selects at most one entry per solver-family, mesh,
boundary, physics/transport, startup/numerics, optional parallel, and optional
repair-error slot. It records missing slots instead of filling them with
unrelated top-N results. Cross-solver entries that are unsafe to infer from
generic words carry explicit `activation_terms` and remain absent unless the
public task states one of those concepts. The model sees this bounded public
context, the factual environment inventory, and at most a general plus one
family Skill.
It does not see the target tutorial, protected paths, evaluator validation
YAML, or reference JSON.

One logical generation request returns every required case file, one
region-aware `CaseManifest`, and every staged typed command as ExecutionPlan
v3.
The `ModelGateway` may use multiple transport attempts within a monotonic
stage deadline, but records logical requests and transports separately. Two
qualification workers share only the Gateway and circuit breaker; each task
retains its own deadline ledger, trace, case, artifact store, and evaluator
workspace.

Before policy, a normalizer may unwrap only an unambiguous local
`mpirun|mpiexec|orterun -n N solver [-parallel]` shape. Deterministic policy
checks safety, installed executables, paths, protected data, resource limits,
and command shape. Semantic inspection checks manifest/solver/application,
region/field paths, explicit mesh patches, command stages, MPI decomposition,
and reviewed family requirements. Unregistered families remain advisory.
OpenFOAM then reads the model-authored dictionaries directly.

Fields marked `author` or `public_asset` must exist before execution. Fields
marked `mesh`, `initialize`, or `solver` are checked for region/path
consistency but are not incorrectly required before their creating command.

After execution, public validation determines whether the requested result
exists and meets declared checks. If the task permits another attempt, the
repair model receives public failure evidence plus the same dynamically
selected public knowledge and workflow Skill used during authoring. It may
change generated files or existing typed commands. The revised plan is
materialized in a new attempt.

## Workflow and failure semantics

`workflow-events.jsonl` is an ordered, fsync-backed record of task,
environment, context, generation, plan, materialization, inspection,
OpenFOAM, public-validation, repair, and finalization stages. Checkpoints are
written exclusively and never replaced.

`RunSummary` schema v2 separates three questions:

- `workflow_state`: `COMPLETED`, `FAILED`, or `DEFERRED`;
- `native_status`: the latest CFD/native result, if native execution exists;
- `primary_failure` and `terminal_blocker`: why the case failed and why work
  cannot currently continue.

For example, a solver can remain `SOLVER_FAILED` while a provider overload is
recorded independently as the retryable terminal blocker. Provider deferral
is therefore not rewritten as an OpenFOAM or Agent-accuracy failure.

## Strict continuation

Retryable generation or repair interruption creates a new child run:

```text
verified immutable parent
-> compatibility fingerprint and lineage-budget checks
-> child continuation run
-> canonical generation or scoped repair
-> canonical inspect/run/validate/finalize
```

The parent is never reopened. Strict resume compares the TaskSpec, public
assets, model/provider policy, package content, source revision, plan schema,
knowledge, Skill, OpenFOAM target, and executable capabilities. Generation
and repair each permit at most two child continuations, and the full lineage
permits at most seven real transport attempts. Code, knowledge, Skill, model,
or policy changes require a new `rerun_with_changes`, not strict resume.

Historical RunSummary v1 files remain reportable through a read-only adapter
but cannot resume.

Canonical authoring and strict resume accept only ExecutionPlan v3. Historical
v2 replay fixtures use a narrow non-exported reader plus separately reviewed,
hashed v3 manifest overlays; this is not an authoring fallback.

## Isolation

The Runner binds an attempt case directory as `/case`, disables network
access, and accepts no shell program. MPI ranks are part of the typed command
record and must remain within the TaskSpec budget.

Evaluator-only qualification runs on a temporary copy of the completed case,
so VTK marker files and post-processing cannot mutate the artifact manifest.

The deterministic replay gate under `tests/fixtures/artifact-replay` contains
bounded, secret-scanned artifacts for single-region, MPI, include, buoyant,
multi-region, and known-failure histories. Replay guards compatibility; it
does not replace native qualification.

See [Runtime workflow and pre-solve health analysis](runtime-workflow-and-pre-solve-health-analysis.md)
for the current end-to-end state machine, measured phase latency, failure
classification, and operational-readiness boundary.
