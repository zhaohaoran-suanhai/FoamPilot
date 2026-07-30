# FoamPilot quickstart

## What the lean path does

`foampilot solve` turns one natural-language-oriented public `TaskSpec` into a
native Foundation OpenFOAM v10 run. FoamPilot is a standalone Python package.

The runtime sequence is:

1. validate the public task;
2. discover the local OpenFOAM environment;
3. retrieve public knowledge dynamically from the task text;
4. make one model call for one complete CaseBundle;
5. validate typed file paths and commands for safety and resource limits;
6. write the declared files into an empty attempt directory;
7. statically inspect the case and execute native commands in the Runner;
8. apply evaluator-owned public checks;
9. if permitted by the attempt budget, request one evidence-scoped repair;
10. freeze the run manifest.

The `ExecutionPlan` v2 schema contains only:

```text
files[]    = {path, content}
commands[] = {step_id, executable, args, mpi_ranks, timeout_seconds}
```

Solver choice, dictionary structure, numerical methods, initialization, and
post-processing remain Agent decisions. Deterministic code does not review
their CFD strategy before execution.

## Install and preflight

```bash
git clone git@github.com:zhaohaoran-suanhai/FoamPilot.git
cd FoamPilot
python -m pip install -e ".[codex,test]"
foampilot preflight --json
```

The local profile expects:

- `/home/edwin/workplace/OpenFOAM-10`;
- `/usr/local/bin/bwrap`;
- `/home/edwin/feal-venv-py312/bin/python`.

Bubblewrap may be blocked inside an already-restricted development sandbox.
That is an environment block, not an OpenFOAM capability result.

## Validate, plan, solve, and report

```bash
foampilot validate examples/tasks/non-tutorial-side-driven-box.yaml --json

foampilot plan examples/tasks/non-tutorial-side-driven-box.yaml \
  --output /tmp/side-driven-plan.json \
  --model-name gpt-5.6-sol \
  --json

foampilot solve examples/tasks/non-tutorial-side-driven-box.yaml \
  --run-root /tmp/foampilot-native-runs \
  --model-name gpt-5.6-sol \
  --json

foampilot report /tmp/foampilot-native-runs/RUN_DIR --json
```

`plan` and the initial phase of `solve` each use one model call for the whole
bundle. `solve` returns zero only for `PUBLIC_VALIDATION_PASS`; an environment
block returns 3 and execution or validation failure returns 4.

## Artifact layout

Each run contains:

```text
task.yaml
environment.json
agent-context.json
execution-plan.json
model-configuration.json
summary.json
artifact-manifest.json
attempt-01/
  execution-plan.json
  generation-trace.json
  static-inspection.json
  run-result.json
  public-validation.json
  case/
    ... generated OpenFOAM files ...
    .foampilot/logs/
```

A failed attempt is never overwritten. A repaired attempt is materialized
again from the revised plan. Safe repair may add a missing generated
dictionary, but it cannot traverse outside the case, overwrite a public asset,
reference a protected path, introduce a new command step, or bypass the
resource policy.

## Evaluation boundary

The evaluator owns `public_checks`; `TaskSpec.agent_payload()` omits them and
all protected paths. The Agent receives only the public physical request,
required outputs, acceptance language, environment inventory, dynamically
retrieved public knowledge, and the authoring Skill.

Official target cases and golden results remain evaluator-only. A formal
benchmark may compare frozen outputs with private references after the run,
but that comparison must not influence generation or repair.

See [Qualification](qualification.md) for the role-aware 15-case protocol and
reporting boundary.
