# AGENTS.md

This file is the authoritative entrypoint for AI agents working in FoamPilot.

## Project boundary

FoamPilot is an independently installable Agent workflow for Foundation
OpenFOAM v10. The canonical runtime is the `foampilot` Python package and CLI.
Do not use Foam-Agent, LangGraph, FAISS, MCP, a Case renderer, or an `Allrun`
compatibility path.

OpenFOAM remains the numerical solver. FoamPilot owns TaskSpec validation,
dynamic public retrieval, model-authored case files, typed execution,
evaluator checks, one bounded repair, and immutable artifacts.

## Canonical commands

```bash
foampilot preflight --json
foampilot validate TASK.yaml --json
foampilot plan TASK.yaml --output PLAN.json --json
foampilot solve TASK.yaml --run-root RUNS --json
foampilot report RUN_DIR --json
foampilot qualify suite \
  --suite-file \
    src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root RUNS/controlled-learning-15 --workers 2 --json
foampilot improve analyze RUN_DIR --qualification-report REPORT.json \
  --candidate-id ID --lesson "LESSON" --target knowledge \
  --output IMPROVEMENTS/candidate.yaml
foampilot improve compare BASELINE.json CURRENT.json \
  --candidate IMPROVEMENTS/candidate.yaml \
  --output IMPROVEMENTS/promotion.json --json
```

For source-tree development, use `PYTHONPATH=src` or install the package
editable into the selected Python 3.12 environment.

## Authoring rules

- Translate the user's natural-language requirement into the smallest public
  TaskSpec that preserves geometry, physics, resources, outputs, and
  acceptance requirements.
- Start from an empty case unless the TaskSpec explicitly declares public
  assets.
- Use Foundation OpenFOAM v10 file names, dimensions, boundary conditions,
  solver syntax, and executable names.
- Retrieve public knowledge dynamically from the task. Do not preselect
  knowledge IDs in the task.
- Do not read or copy the target tutorial.
- Do not expose evaluator rules, derived references, protected paths, or
  golden data to the case-authoring or repair model.
- Return complete case-relative files and typed executable-plus-argument
  commands.
- Do not generate shell syntax, `Allrun`, `mpirun`, `orterun`, host files, or
  external paths. The Runner owns MPI launch and resource enforcement.
- Do not add optional function objects merely to manufacture evaluator
  evidence. Evaluators inspect solver logs and written fields.

## Controlled improvement

- Keep improvement offline and separate from `NativeAgent.solve()`.
- Official examples are unavailable during blind authoring and repair.
- Verify the immutable artifact manifest and matching frozen qualification
  result before examining an official example as a teacher reference.
- Extract only general solver-family principles; do not copy complete cases,
  target geometry, patch-specific parameters, golden values, tolerances, or
  official paths into Knowledge, Skills, or prompts.
- Write candidates and promotion reports beside run roots, never into
  finalized runs or package data.
- A passing comparison means eligible for review, with no automatic promotion.
  Change formal Knowledge, Skills, prompts, inspection, Runner, or evaluators
  only after explicit approval.

## Execution and repair

- Run `preflight` before a real solve.
- Preserve every attempt; never edit an immutable attempt in place.
- Classify environment, authoring, plan, static-inspection, solver, public
  validation, and qualification failures separately.
- Base repair only on the public report, failed command log, active plan,
  current generated files, dynamically selected public knowledge, and the
  public workflow Skill.
- Make one minimal repair within the TaskSpec attempt budget.
- Do not interpret a solver return code of zero as proof of convergence or
  physical correctness.
- Verify the artifact manifest before reporting a result.

## Verification

Before claiming a code change complete:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

When package data changes, also build and inspect a wheel. When runtime code
changes, run host `foampilot preflight` and the smallest relevant real
OpenFOAM gate.

State the evidence boundary explicitly: deterministic tests, solver
completion, public validation, and qualification are different claims.

## Repository safety

- Preserve unrelated user changes.
- Use explicit paths for generated and destructive operations.
- Do not commit or push unless the user explicitly asks.
- Do not create or change a remote unless the user explicitly authorizes it.
- Never add `.foampilot/` run trees, solver results, caches, credentials, or
  official tutorial copies to Git.
