# Architecture

FoamPilot has one supported path from a public TaskSpec to an evidence-scoped
result.

## Components

- `tasks`: strict public requirements and resource budgets;
- `knowledge`: reviewed Foundation OpenFOAM v10 facts with provenance;
- `skills`: portable behavioral guidance for case authoring and selected
  solver families;
- `agent`: prompt construction, complete case-bundle authoring, and bounded
  repair;
- `plans`: complete generated files and typed native commands;
- `inspection`: case-local static safety checks;
- `runtime`: Foundation v10 discovery, networkless bubblewrap execution,
  explicit MPI launch, budgets, and logs;
- `validation`: evaluator-owned checks over commands, logs, and written
  fields;
- `artifacts`: immutable attempts and SHA256 manifests;
- `qualification`: role-aware suite execution and external physics
  comparisons using compact packaged references.

## Data flow

The model sees the public task, the factual environment inventory, dynamically
selected public knowledge, and the native authoring Skill. It does not see
the target tutorial, protected paths, evaluator validation YAML, or reference
JSON.

One model call returns every required case file and command. Deterministic
policy checks only safety, installed executables, paths, protected data,
resource limits, and command shape. OpenFOAM then reads the model-authored
dictionaries directly.

After execution, public validation determines whether the requested result
exists and meets declared checks. If the task permits another attempt, the
repair model receives public failure evidence plus the same dynamically
selected public knowledge and workflow Skill used during authoring. It may
change generated files or existing typed commands. The revised plan is
materialized in a new attempt.

## Isolation

The Runner binds an attempt case directory as `/case`, disables network
access, and accepts no shell program. MPI ranks are part of the typed command
record and must remain within the TaskSpec budget.

Evaluator-only qualification runs on a temporary copy of the completed case,
so VTK marker files and post-processing cannot mutate the artifact manifest.
