# Official-six qualification

The `official-six-v1` suite checks whether FoamPilot can author and solve six
representative Foundation OpenFOAM v10 cases without reading their target
tutorials:

- transient laminar cavity;
- potential flow around a circular obstacle;
- steady RANS sudden-expansion channel;
- two-phase column collapse;
- compressible shock tube;
- turbulent buoyant cavity.

## Isolation

Each public TaskSpec describes geometry, physics, resources, outputs, and
public acceptance requirements. The model receives no validation YAML or
reference JSON. The qualification layer reads those assets only after a
native run has passed its public checks and its artifact manifest verifies.

The repository contains compact derived reference metrics, not official
tutorial case directories or solver time trees.

## Execution

```bash
foampilot qualify official-six \
  --run-root /tmp/foampilot-official-six \
  --workers 2 \
  --model-name gpt-5.6-sol \
  --json
```

At most two non-buoyant cases run concurrently. The large buoyant case runs
exclusively. Each case retains its TaskSpec-defined attempt and MPI budget.

## Verdicts

- `PASS`: native public validation, manifest verification, and every required
  external metric pass;
- `FAIL_AGENT`: authoring, execution, public validation, manifest, or physics
  metric failure;
- `BLOCKED_ENVIRONMENT`: OpenFOAM, sandbox, model transport, or another
  external dependency is unavailable;
- `INVALID_QUALIFICATION`: required evaluator evidence is missing.

Solver completion alone is not a qualification pass.

## Current evidence

The last preserved run before the FoamPilot extraction showed that all six
cases eventually completed their solver after bounded repair. Its strict
physics report still contained failures. That report is historical evidence,
not a claim that a fresh stochastic six-case run will reproduce the same
verdicts.

The standalone extraction is first gated by deterministic tests, wheel
installation, host preflight, and two non-tutorial real solves. A fresh
official-six invocation is a separate qualification run.

The standalone real-case gate reached 2/2 `PUBLIC_VALIDATION_PASS` on
2026-07-29. This is recorded separately because it validates the generic
authoring and repair loop without claiming an official-six result. See the
[standalone gate report](reports/2026-07-29-standalone-real-gate.md).
