# Controlled qualification

The `controlled-learning-15-v1` suite checks whether FoamPilot can author and
solve 15 representative Foundation OpenFOAM v10 cases without reading their
target tutorials.

The six regression cases cover transient laminar cavity flow, potential flow
around a cylinder, steady RANS sudden expansion, two-phase column collapse,
a compressible shock tube, and a turbulent buoyant cavity.

The six development cases cover scalar transport, laminar Maxwell flow,
porous RANS flow, compressible blocked-channel flow, conjugate heat transfer,
and a single-reference-frame rotor.

The three frozen holdouts cover magnetohydrodynamic Hartmann flow, capillary
rise, and linear-elastic solid displacement around a plate hole. A case role
controls how evidence may be used; it does not change the solve path.

## Isolation

Each public TaskSpec describes geometry, physics, resources, outputs, and
public acceptance requirements. The model receives no validation YAML or
reference JSON. The qualification layer reads those assets only after a
native run has passed its public checks and its artifact manifest verifies.

The repository contains compact derived reference metrics, not official
tutorial case directories or solver time trees.

## Execution

```bash
foampilot qualify suite \
  --suite-file \
    src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root /tmp/foampilot-controlled-learning-15 \
  --workers 2 \
  --model-name gpt-5.6-sol \
  --json
```

At most two ordinary cases run concurrently. Cases marked `exclusive`, such
as the large buoyant and CHT cases, run alone. Each case retains its
TaskSpec-defined attempt, wall-time, memory, and MPI budget. The compatible
`foampilot qualify official-six` wrapper runs only the six regression cases.

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

The frozen 2026-07-30 full-suite baseline produced 11 strict passes and four
failures. Every case entered its requested solver; 14 reached public
validation, while one CHT case failed in the solver. Targeted reruns after
small, generalized knowledge and evaluator corrections passed the four
affected families. These targeted results demonstrate the corrections but do
not constitute a single fresh 15/15 stochastic rerun.

See the
[controlled-learning 15-case report](reports/2026-07-30-controlled-learning-15.md)
for case-level evidence, failure analysis, and the exact claim boundary.

The earlier standalone real-case gate reached 2/2
`PUBLIC_VALIDATION_PASS` on 2026-07-29. It validates the installed-wheel
authoring and repair loop, not strict 15-case physics qualification. See the
[standalone gate report](reports/2026-07-29-standalone-real-gate.md).
