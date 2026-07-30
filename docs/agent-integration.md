# Agent integration

## Stable boundary

FoamPilot is a framework-neutral execution boundary. An external Agent may
call the CLI or Python API, but the toolkit does not depend on an external
agent framework.

The external Agent must not run OpenFOAM directly, patch an immutable attempt,
read an official target tutorial, inspect private golden data, or assign a
formal benchmark PASS.

## Canonical native loop

1. Load a public `TaskSpec`.
2. Discover Foundation OpenFOAM v10 and installed native executables.
3. Dynamically retrieve bounded public knowledge from the task text.
4. Ask the model once for all generated files and typed commands.
5. Apply deterministic path, command, resource, and protected-data policy.
6. Materialize and statically inspect an empty case directory.
7. Execute the typed commands through the sandboxed Runner.
8. Apply evaluator-owned public checks and preserve the evidence.
9. Permit only the configured evidence-scoped repair budget.

No model reviewer sits between steps 4 and 5. There is no per-file model loop,
preselected knowledge-ID allowlist, CaseSpec resolution, or renderer in the
canonical path.

### Conservative pre-solve gate

Pre-solve inspection blocks only mechanically certain defects, such as an
explicitly missing boundary patch or a known-incompatible Foundation v10
dictionary construct. If includes, substitutions, or other dynamic syntax make
the result uncertain, inspection records an advisory and lets OpenFOAM decide.

A blocking static defect consumes the same evidence-scoped repair budget as a
runtime failure; it does not terminate the task before the Agent can correct
its files. During execution, an explicit `Failed N mesh checks` message from
`checkMesh` stops later solver commands even when `checkMesh` returns zero.
Ambiguous or unfamiliar log text is preserved as evidence and is not treated
as a new hard-coded failure.

## Machine-readable commands

```bash
foampilot validate TASK.yaml --json
foampilot plan TASK.yaml --output PLAN.json --model-name MODEL --json
foampilot solve TASK.yaml --run-root RUN_ROOT --model-name MODEL --json
foampilot inspect TASK.yaml PLAN.json CASE_DIR --json
foampilot report RUN_DIR --json

foampilot knowledge validate src/foampilot/knowledge/openfoam10 --json
foampilot knowledge search src/foampilot/knowledge/openfoam10 "QUERY" \
  --formal --limit 8 --json
foampilot skill validate \
  src/foampilot/skills/openfoam-author-native-case --json

foampilot improve analyze RUN_DIR \
  --qualification-report BASELINE.json \
  --candidate-id CANDIDATE \
  --lesson "GENERAL LESSON" \
  --target knowledge \
  --output IMPROVEMENTS/candidate.yaml
foampilot improve compare BASELINE.json CURRENT.json \
  --candidate IMPROVEMENTS/candidate.yaml \
  --output IMPROVEMENTS/promotion.json \
  --json
```

Exit codes are 0 for pass, 2 for invalid CLI input, 3 for an environment
block, 4 for execution or public-validation failure, and 5 for an unexpected
internal error.

## Python boundary

The main integration types are:

- `TaskSpec`;
- `ExecutionPlan`, `GeneratedFile`, and `NativeCommand`;
- `NativeAgent`;
- `RuntimeConfig` and `PlanRunner`;
- `PublicValidationReport`;
- `ArtifactStore`.

`NativeAgent.solve()` owns the full state machine. Adapters should preserve its
JSON outcome and artifact paths rather than reimplementing generation,
execution, validation, or repair.

## Retrieval and leakage

Formal retrieval excludes development-only entries. The toolkit records the
selected knowledge IDs and source hashes for provenance, but the task does not
choose those IDs.

Only general public material may enter the Agent prompt. Current target
tutorial paths, private validators, golden values, and source mappings remain
outside the Agent boundary.

## Offline improvement boundary

The improvement commands are a developer workflow over frozen evidence, not
part of `NativeAgent.solve()`:

```text
frozen solve/qualification
-> improve analyze
-> developer applies one candidate change
-> rerun qualification
-> improve compare
-> explicit promotion decision
```

There is no automatic promotion. The analyzer verifies the artifact manifest
and requires a matching qualification result before it can hash an optional
official example. Official examples are unavailable during blind authoring and
repair. They may only be examined afterward to extract general principles;
their paths, complete dictionaries, target-specific geometry, golden values,
and evaluator tolerances never enter the model context.

Learning candidates and promotion reports are written exclusively to
developer-selected paths beside run roots. They are not written into immutable
runs and do not become package knowledge or Skills without explicit review and
approval.

## Single execution path

The earlier provider/CaseSpec/renderer and Agent-authored `Allrun` paths are
not part of FoamPilot. Integrations use the native loop above; there is no
legacy fallback or compatibility command surface.

See [Qualification](qualification.md) for the current evaluation boundary.
