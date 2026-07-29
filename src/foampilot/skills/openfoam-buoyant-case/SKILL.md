---
name: openfoam-buoyant-case
description: Use when an Agent authors, repairs, or validates a Foundation OpenFOAM v10 steady buoyantFoam case with p_rgh, turbulence wall functions, and wall heat transfer.
---

# Work with buoyantFoam cases

## Solver semantics

`p` is thermodynamic pressure. `p_rgh = p - rho*g*h` is the
hydrostatic-reduced pressure solved by the buoyant pressure equation.
`pRefCell` and `pRefValue` fix the `p_rgh` gauge; they do not replace the
thermodynamic operating pressure used by the perfect-gas model.

## Required workflow

1. Establish the operating pressure, gravity datum, `p` initial field,
   `p_rgh` initial field, and `pRefCell`/`pRefValue` together. Reject a mixture
   of absolute-pressure and reduced-pressure reference semantics.
2. Use `steadyState` temporal discretization for a steady task. Treat
   `controlDict` time as iteration bookkeeping and configure residual control
   for `p_rgh`, `U`, enthalpy, and turbulence fields.
3. Select turbulence boundary conditions as a coherent Foundation v10 set:
   - `kqRWallFunction` for `k`;
   - `omegaWallFunction` for `omega`;
   - a compatible `nut` wall function;
   - a compressible `alphat` wall function with the declared turbulent
     Prandtl number and required value entry.
4. After `End`, compare initial and terminal windows of equation initial
   residuals. Require every declared field to decrease and finish below the
   public family threshold.
5. Check terminal local and cumulative continuity errors. Continue or
   stabilize the solve when the local error remains excessive even if the
   signed cumulative error cancels.
6. Run Foundation v10 `wallHeatFlux` on a temporary copy of the completed
   case. Use its integrated `Q`, which comes from the active
   `thermophysicalTransportModel`; do not reconstruct heat flow only from
   molecular conductivity and first-cell temperature.
7. Check `abs(Q_hot + Q_cold) / max(abs(Q_hot), abs(Q_cold))` against the
   declared public tolerance. Feed failed public checks back to repair without
   revealing any golden result.

## Evidence contract

Report:

- pressure-field and reference semantics;
- wall-function types and turbulent Prandtl number;
- per-field first/last residual-window medians and ratios;
- terminal local and cumulative continuity errors;
- hot/cold integrated `wallHeatFlux` values and normalized imbalance;
- separate execution and public-physics verdicts.

## Boundaries

- Never infer convergence from a fixed iteration count or `End`.
- Never accept cancellation in cumulative continuity as proof that local
  continuity is small.
- Never use target-case heat rates, profiles, Nusselt values, or a private
  golden to select numerical settings.
- Do not mutate a preserved attempt merely to post-process it.

## Common mistakes

| Mistake | Required correction |
| --- | --- |
| Treat `pRefValue` as absolute operating pressure | Keep thermodynamic and reduced-pressure roles separate |
| Mix wall functions copied from unrelated turbulence setups | Validate one coherent Foundation v10 set |
| Check only the final iteration number | Check residual trend and continuity |
| Estimate turbulent wall heat from molecular `k*dT/dn` | Use the active transport model through `wallHeatFlux` |
