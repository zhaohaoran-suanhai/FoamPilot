---
name: openfoam-rhocentral-case
description: Use when an Agent authors, repairs, or validates a Foundation OpenFOAM v10 rhoCentralFoam shock-tube or inviscid compressible transient case.
---

# Work with rhoCentralFoam cases

## Solver semantics

Treat `deltaT` as the initial time step and `maxDeltaT` as the upper bound for
adaptive growth. When `adjustTimeStep yes` is required, set a positive `maxCo`
and require `maxDeltaT > deltaT`. A copied `maxDeltaT` from another tutorial is
not evidence that it is appropriate for this mesh, thermodynamics, or end time.

## Required workflow

1. Derive density and heat-capacity ratio from the declared perfect-gas
   properties. Do not substitute remembered air constants when public inputs
   are available.
2. Align the initial discontinuity with a cell face and make the transverse
   directions geometrically one-dimensional.
3. Use a coherent Foundation v10 thermodynamics model and inviscid transport
   properties. Check every field and boundary against the mesh patch inventory.
4. Gate `controlDict` before solving:
   - `adjustTimeStep yes`;
   - requested positive `maxCo`;
   - `maxDeltaT` strictly greater than the initial `deltaT`;
   - enough write precision to represent the adaptive steps.
5. After a normal solver end, parse the actual Courant history. A value far
   below the target can indicate that `maxDeltaT` is still the active limiter;
   a value above the allowance is unsafe.
6. Compute the exact ideal-gas Riemann wave speeds and positions from the
   public initial states. Check rarefaction head, contact, and shock within the
   declared cell-width tolerance.
7. If any public check fails, return the check evidence to repair and change
   the smallest causally related input. A zero exit code is not a physics pass.

## Evidence contract

Report:

- `deltaT`, `maxDeltaT`, `maxCo`, and `adjustTimeStep`;
- observed peak Courant number and its target ratio;
- analytical and detected rarefaction, contact, and shock locations;
- spatial tolerance in metres and cell widths;
- a separate execution and public-physics verdict.

## Boundaries

- Never use a target tutorial, private validator, or golden wave position.
- Do not tune the time step until a private comparison passes.
- Do not accept mass conservation and smooth profiles as substitutes for wave
  propagation accuracy.
- Stop as not proven when Courant or detected-wave evidence is absent.

## Common mistakes

| Mistake | Required correction |
| --- | --- |
| `maxDeltaT` equals the initial `deltaT` | Give adaptive stepping room to grow and verify actual Co |
| Reuse another compressible tutorial's cap | Derive controls from this mesh and public state |
| Compare only final primitive profiles | Add exact public wave-position checks |
| Treat `End` as success | Separate execution completion from physics acceptance |
