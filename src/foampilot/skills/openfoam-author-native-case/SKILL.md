---
name: openfoam-author-native-case
description: Use when an Agent must turn a public Foundation OpenFOAM v10 TaskSpec into native case dictionaries, direct typed commands, public run evidence, and bounded evidence-scoped repairs.
---

# Author and verify a native OpenFOAM case

## Core principle

Own the CFD choices and file bytes, while letting the runtime own command
policy and the evaluator own private truth. Start from an empty case. Never
read a protected tutorial, target case, golden result, or private validator.

## Produce the execution plan

Return one complete `ExecutionPlan` containing all files and commands:

1. Select the application from the stated physics and installed commands.
2. Declare every required native file in dependency order.
3. Declare direct commands in phase order: mesh, check, initialize, solve,
   and reconstruct when parallel. Add post-processing only when the public
   task explicitly requires that command, not merely a derived measurement.
   Use argv only—no shell, `Allrun`, redirection, command substitution, or
   absolute host paths.
   The runtime already changes directory to `/case`. Do not add `-case case`
   or another `-case` argument. Keep generated files, dependencies, and
   requested-output paths relative to that root (`1/U`, not `case/1/U`).
   The Runner owns MPI launchers: set the solver as `executable`, set
   `mpi_ranks`, and never emit `mpirun` or `orterun`.
4. Keep the total step timeouts and MPI ranks inside the TaskSpec budget. The
   sum of all command timeouts must be no greater than `max_wall_seconds`;
   reserve mesh, initialization, decomposition, and reconstruction time first,
   then assign only the remaining budget to the solver.
5. Map every required output to solver logs or written fields that the
   evaluator can inspect after the solve. Require mesh quality, normal
   completion, requested final time, finite fields, and the relevant
   conservation or physical invariant.
   Bind `finite_fields` directly to the solve step; the validator checks its
   log for non-finite markers. Do not invent an unavailable post-processing
   function merely to prove finiteness.
6. Use only the public physical inputs and do not invent missing values.

## Author native files

- Give every OpenFOAM dictionary a valid `FoamFile` header.
- Make `system/controlDict` application match the execution plan.
- Cover every mesh patch in every field.
- For a two-dimensional extrusion, use one suppressed-direction cell and
  matching `empty` mesh and field patches.
- For a multi-block mesh, ensure every shared face has identical subdivisions
  in both tangential directions and grading-compatible point locations. Define
  named subdivision variables for integer cell counts in each topological edge
  family and reuse them on every adjacent block instead of repeating unrelated
  integer literals. For a 2D hex extruded from lower z to higher z, order its
  four lower vertices counter-clockwise in x-y and map the four upper vertices
  in the same order; verify this handedness for every block, not only the first.
  OpenFOAM dictionary substitution is not arithmetic: never write `-$name` for
  a negative coordinate. Use a literal negative scalar or define a separate
  negative scalar and substitute that complete token.
  Before emitting `blockMeshDict`, check the full adjacency graph, including
  local block-axis direction and grading orientation; do not repair one
  reported block pair while leaving another non-conformal shared face. At a
  fluid-solid interface, match tangential subdivisions across the interface
  independently of unrelated upstream, downstream, or radial cell counts.
  Adjacent blocks must reuse the exact same vertex labels on their shared face;
  coincident coordinates assigned new labels create disconnected topology.
  Foundation v10 `blockMesh` has no `-merge-points` repair option: correct the
  vertex and face topology instead of inventing a command flag. Account for
  every exterior face in a named boundary patch. An unintended `defaultFaces`
  patch or a one-dimensional `checkMesh` classification is evidence of missing
  exterior faces, not a mesh-quality warning to ignore.
- When geometry rotates or bends, transform the full local frame and
  cross-section, including its face normals and porous or outlet axes. Moving
  only the centerline while leaving the cross-section in global axes changes
  the modeled area and resistance.
- Keep constraint patch types consistent: mesh type `symmetryPlane` requires field type `symmetryPlane`, not `symmetry`. When appropriate, `#includeEtc "caseDicts/setConstraintTypes"` may derive Foundation v10 field constraints from the mesh.
- For a regional initial condition, declare its dictionary and a native
  initialization command after mesh checks and before the solver.
- Keep optional diagnostics outside the required solve plan. Do not add
  sampling, extrema, conservation, or convergence function objects merely to
  create evaluator evidence; written fields and solver logs are sufficient.
- When the public task explicitly requires all-time log evidence, use the
  exact dynamically retrieved Foundation v10 diagnostic recipe and record it
  at the required cadence.
- For ordinary output-time measurements, let the evaluator inspect written fields instead of adding function objects.
- Return all complete files in the same CaseBundle. Keep their patch, field,
  and dictionary dependencies internally consistent.

## Preserve VOF boundedness

When a two-fluid VOF task has a public phase-fraction tolerance:

1. Treat `maxCo` and `maxAlphaCo` as ceilings rather than accuracy targets.
   For a strict bound, configure both strictly below the TaskSpec's allowed
   maxima and choose a compatible `maxDeltaT`; copying the allowed maxima
   leaves no stability headroom.
2. Declare the Foundation v10 alpha controls explicitly, including
   `nAlphaCorr`, `nAlphaSubCycles`, `MULESCorr`, and limiter settings when
   applicable. No setting is proof of boundedness without observed evidence.
3. Let the evaluator derive extrema and phase-volume history from written
   fields after a successful solve.
4. If a completed finite solve violates a bound, keep mesh, physics,
   boundaries, and initialization fixed. First test one smaller
   time-step/interface-Courant family. If the failure persists, test one alpha
   correction, sub-cycling, or limiter family.
5. Rerun the full interval and require boundedness and conservation together.
   Never weaken a public threshold to manufacture a pass.

## Evaluate and repair

Run only the safety-validated typed plan. A zero exit code proves execution, not
physics. Apply public checks in order and stop on the earliest failed layer.

If repair is allowed, state: evidence, one cause, one minimal safe generated
file or existing typed-command change, expected check, and one stable control.
A repair may add a safe generated case file when the failure proves that a
required dictionary is missing. Preserve the failed attempt. Do not repair
environment failures, repeat an unchanged fingerprint, change public assets,
or bundle unrelated hypotheses.

## Output contract

Return the execution plan, generated-file hashes, static inspection, per-step
logs, public checks, repair decision when present, scoped status, and immutable
artifact directory. `PUBLIC_VALIDATION_PASS` never implies a private or formal
golden pass.
