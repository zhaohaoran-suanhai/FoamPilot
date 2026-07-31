# FoamPilot Stage B acceptance

## Outcome

Stage B keeps the single-Agent native OpenFOAM path and adds the smallest
semantic layer needed before authoring and execution:

- evidence-based `CapabilityProfile` routing with system-owned confidence;
- at most one public knowledge entry per semantic slot, explicit activation
  terms for opt-in cross-solver guidance, and at most two routed Skills;
- region-aware `CaseManifest` inside canonical ExecutionPlan v3;
- `stage` on each typed command as the only command-stage representation;
- one narrow safe MPI launcher normalizer before plan policy;
- provenance-bearing generic semantic checks and a minimal `icoFoam` family
  contract;
- v3 capability/context/normalization checkpoints in canonical solve/resume;
- a narrow, non-exported frozen-v2 reader with six reviewed and hashed v3
  manifest overlays.

The Agent still writes every native OpenFOAM file. Stage B adds no renderer,
MCP, multi-agent graph, per-case adapter, or tutorial access.

## Deterministic gates

The final source-tree suite, run with the host permissions required by the
real bubblewrap preflight, reported:

```text
315 passed, 3 skipped
```

The three skips are explicit opt-in real integrations: one real
OpenFOAM continuation test and two real-model/OpenFOAM task variants.

Focused evidence includes:

- route confidence, incomplete requests, installed solver checks, ambiguous
  model suggestions, implicit ordinary single-phase flow, and physical
  family filtering;
- bounded slot selection, missing slots instead of irrelevant fill,
  solver-agnostic activation terms, conditional parallel/repair slots, Skill
  routing, leakage filtering, and whole-entry pruning;
- single- and multi-region manifest identities;
- positive and negative MPI normalization shapes;
- solver/application/field/patch/stage/MPI/family semantic rules with
  Foundation v10 provenance;
- canonical solve, repair, strict resume, and capability/context checkpoint
  behavior;
- six frozen replay kinds: single region, MPI, include, buoyant, multi-region
  CHT, and known failure.

The replay gate initially found that the old `include-success` fake-runner
fixture lacked a runnable `icoFoam` case. It was corrected to retain the
include behavior while adding a complete public case; no semantic exemption
was introduced for the historical false success.

## Real OpenFOAM gates

The deterministic provider-overload continuation gate used the real
bubblewrap Runner and Foundation OpenFOAM v10:

```text
1 passed in 3.02 s
```

It verified a real target-solver failure, independent provider blocker,
immutable parent, strict repair continuation, and final
`PUBLIC_VALIDATION_PASS`.

A real Codex OAuth model then authored a Stage B v3 non-tutorial side-driven
enclosure:

- run:
  `/home/edwin/workplace/FoamPilot-runs/stage-b-minimal-real-20260731-v4/run-20260731T015041519109Z-5216faea`;
- route: `icoFoam`, `incompressible-laminar`, medium confidence;
- all five base knowledge slots populated;
- one general native-authoring Skill selected;
- workflow/native result: `COMPLETED / PUBLIC_VALIDATION_PASS`;
- artifact verification issues: none.

The first inspection of that run exposed one Stage B rule defect: a field
declared `created_by=solver` at a future time was incorrectly required before
execution. The repair model worked around it by adding the future field, and
the second attempt passed. The rule was then corrected so only `author` and
`public_asset` fields must pre-exist; `mesh`, `initialize`, and `solver`
fields retain path/region checks without a false pre-existence requirement.
A dedicated regression test and all six frozen replays pass with the corrected
rule. The two-attempt run is therefore evidence for model-to-v3 integration
and real execution, not evidence that the original rule was already healthy.

Two earlier run roots are deliberately excluded from Stage B acceptance:

- `stage-b-minimal-real-20260731-v1` used a `--target` console script that
  imported the older globally installed package and emitted v2 artifacts;
- `stage-b-minimal-real-20260731-v2` and `v3` correctly used Stage B but
  recorded model manifest schema failures before the single-region manifest
  prompt contract was clarified.

## Serial official-six gate

The final source was exercised once on all six regression tasks, serially
between tasks while retaining each task's declared MPI budget:

- run root:
  `/home/edwin/workplace/FoamPilot-runs/stage-b-official-six-final-20260731-v2`;
- report:
  `official-six-v1-report.json` and `official-six-v1-report.md`;
- result counts: `4 PASS`, `2 FAIL_AGENT`, `0 DEFERRED_PROVIDER`,
  `0 BLOCKED_ENVIRONMENT`;
- generation, native execution, mesh generation, and `checkMesh`: `6/6`;
- target solver started and ended normally: `6/6`;
- public validation: `5/6`;
- external physics qualification: `4/6`;
- model transport: eight logical requests and nine transport attempts;
- recorded model/OpenFOAM time: `917.08 s / 51.44 s`.

One RANS authoring transport failed with
`PROVIDER_NETWORK_UNAVAILABLE`; the shared ModelGateway retried the same
logical request and received a valid response. The task continued without a
provider deferral. This is direct evidence for the Stage A retry boundary
under the Stage B workflow.

The two qualification failures occurred after successful target-solver
completion:

- `rans-pitzdaily` passed public validation, but its external pressure-change
  error was `0.1153` against a `0.10` limit; the downstream velocity and flow
  balance checks passed.
- `multiphase-dam-break` completed `blockMesh`, `checkMesh`, `setFields`, and
  `interFoam` through `1 s` twice. Its volume drift was below `0.001`, but the
  worst `alpha.water` minimum remained about `-4.80e-6` against the public
  lower limit `-1e-6`.

These failures measure model-authored numerical quality. They are not
provider, routing, pre-solve, mesh-entry, or solver-entry failures. All six
run manifests verify without issue.

## Wheel gate

The final wheel is:

```text
/tmp/foampilot-stage-b-final-20260731/foampilot-0.1.0-py3-none-any.whl
sha256 6b2faba8ed377ac1959153be8cf379bce0c1a6d2ec9567182231e640f96c0f44
```

It was installed into an isolated target, imported from that target with
canonical `ExecutionPlan.schema_version == 3`, and inspected for packaged
knowledge, Skills, manifests, context, and qualification assets. Its
host-permitted preflight passed all checks, including the networkless
bubblewrap launch and `icoFoam` discovery.

## Claim boundary

The evidence demonstrates that Stage B is integrated into the canonical
solve/resume path, is replay-safe across bounded historical artifact classes,
can recover a transient provider-network failure, and sends all six
regression tasks through their target Foundation OpenFOAM v10 solvers.

It does not demonstrate:

- elimination of model-authored dictionary or physics errors;
- qualification of every registered solver family;
- support for other OpenFOAM distributions or versions;
- a fresh 15-case qualification pass;
- production service availability.

The next architecture stage may therefore focus on scoped repair and
root-cause routing. The two remaining official-six misses should remain
solver-family/numerical-quality evidence; they do not justify adding
per-case workflow code or widening deterministic blocking inspection.
