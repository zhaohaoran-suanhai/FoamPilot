# FoamPilot Stage A acceptance

## Outcome

Stage A upgrades FoamPilot's existing native OpenFOAM path without adding a
renderer, MCP, multi-agent graph, or per-case adapter. The implemented path
now has:

- a one-exchange provider boundary;
- typed provider failure classification;
- monotonic request/stage/total deadlines and bounded retries;
- a shared, thread-safe qualification circuit breaker;
- per-run model budgets and redacted attempt traces;
- ordered workflow events and exclusive checkpoints;
- RunSummary v2 with separate workflow, native failure, and terminal blocker;
- immutable parent/child continuation for generation and repair;
- compatibility fingerprints, per-stage continuation limits, and a
  seven-transport lineage limit;
- lineage-aware qualification model metrics;
- six bounded frozen artifact replay classes.

Capability routing, CaseManifest, semantic family contracts, and scoped
RepairPatch are deliberately outside Stage A.

## Deterministic gates

The directed Stage A tests cover:

1. overload recovery and persistent overload;
2. rate-limit deadline handling;
3. auth and permission non-retry;
4. SSE interruption retry bounds;
5. schema-invalid non-retry;
6. response closure on stream timeout;
7. stage and total deadline enforcement;
8. lineage transport reservation before HTTP;
9. circuit open, concurrent half-open, success, and re-open;
10. qualification-wide breaker reuse;
11. generation and repair continuation;
12. immutable parent manifest and strict compatibility rejection;
13. continuation and lineage budget exhaustion;
14. v2 dual-failure reporting and lineage metric accumulation;
15. frozen single-region, MPI, include, buoyant, multi-region, and
    known-failure artifact replay.

The first full deterministic run after implementation reported:

```text
260 passed, 2 skipped
```

This count excluded `tests/test_runtime.py` so the nested development sandbox
could not create a false OpenFOAM failure. The same runtime test was then run
with host namespace permission and reported:

```text
4 passed
```

The final host-permitted repository-wide run reported:

```text
269 passed, 3 skipped
```

The three skips are the explicit opt-in real OpenFOAM continuation gate and
two real-model/OpenFOAM task variants. They are not presented as passes. The
side-driven real continuation and real-model variants were run separately as
described below.

## Real model and OpenFOAM gate

The public non-tutorial side-driven enclosure completed through the canonical
path:

```text
Codex OAuth ModelGateway
-> one structured case bundle
-> materialize
-> static inspection
-> blockMesh
-> checkMesh
-> icoFoam
-> evaluator-owned public checks
-> immutable manifest
```

Evidence:

- run:
  `/home/edwin/workplace/FoamPilot-runs/stage-a-final-real-20260731/run-20260731T004221617417Z-7d99b9ed`;
- workflow state: `COMPLETED`;
- native status: `PUBLIC_VALIDATION_PASS`;
- logical requests / transport attempts: `1 / 1`;
- model time: `65.27 s`;
- workflow events / transport traces: `15 / 1`;
- artifact manifest SHA256:
  `3ea42ee43f14677ad5489a1c96f9c32fb0b5388d49023db20d7c3303fc5e649f`.

The durable run was produced directly by `foampilot solve`. The final opt-in
pytest gate for the same task reported `2 passed, 3 deselected` because the
selection also includes its contract test. This is one CFD solve per gate,
not two solves hidden behind the pytest count.

## Solver failure, provider blocker, and resume gate

A fixed public provider response introduced one controlled omission of
`div(phi,U)` into the same non-tutorial case. The real Runner completed mesh
generation and `checkMesh`, entered `icoFoam`, and failed at the target solver.
The repair provider then returned persistent overload.

Parent evidence:

- run:
  `/home/edwin/workplace/FoamPilot-runs/stage-a-final-continuation-gate-20260731/test_solver_failure_provider_d0/runs/run-20260731T004459198370Z-fdbc750d`;
- workflow state: `DEFERRED`;
- native status / primary failure: `SOLVER_FAILED / solver`;
- terminal blocker: `PROVIDER_OVERLOADED / provider`;
- resume stage: `MODEL_REPAIR_STARTED`;
- manifest SHA256:
  `a3e709d433b29615646416a660ede7b6cf5957ba7a61f1dbfc25dad75a7ef131`.

The child reused only frozen public failure evidence, restored the omitted
scheme, reran the canonical native path, and passed:

- run:
  `/home/edwin/workplace/FoamPilot-runs/stage-a-final-continuation-gate-20260731/test_solver_failure_provider_d0/runs/run-20260731T004500671953Z-4bd5084b`;
- workflow state: `COMPLETED`;
- native status: `PUBLIC_VALIDATION_PASS`;
- child manifest SHA256:
  `497ddc9e682d5820a3a213c1a471a01a846c547534f6941c7b4ed5cca427576e`.

The test re-verified the parent manifest and compared its bytes before and
after continuation. The durable gate reported `1 passed in 3.26 s`; subsequent
`foampilot report --json` checks returned no manifest integrity issues for
the parent or child.

## Wheel gate

The source tree built without dependency isolation into:

```text
/tmp/foampilot-stage-a-wheel/foampilot-0.1.0-py3-none-any.whl
```

- wheel size: `281229` bytes;
- wheel SHA256:
  `0e4c9ad48cdd6a4598a6e4ed9b0c6ccc8b0639ae8b68b8bf7d37aa7a3455f19b`;
- isolated target:
  `/tmp/foampilot-stage-a-site`;
- installed import exposed the `resume` command and
  `ResumeCompatibility`;
- installed-wheel host preflight returned `PASS` for Python, OpenFOAM root,
  bashrc, tutorial-root inventory, bubblewrap, networkless namespace launch,
  and `icoFoam`.

## Claim boundary

These gates demonstrate that the Stage A provider/workflow architecture is
retry-bounded, auditable, recoverable, and connected to real Foundation
OpenFOAM v10 execution. They do not demonstrate:

- full 15-case stochastic requalification;
- support for other OpenFOAM distributions or releases;
- Stage B routing or semantic-manifest behavior;
- elimination of model-authored dictionary or physics errors;
- production service availability.
