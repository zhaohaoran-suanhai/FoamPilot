# Solver-family Skills and public self-checks

## Purpose

The package retains portable Skills for Foundation OpenFOAM v10
`rhoCentralFoam` and `buoyantFoam`. They describe reusable solver semantics
and public acceptance checks without target tutorials or private golden data.

Validate them with the shared scenario suite:

```bash
foampilot skill validate src/foampilot/skills/openfoam-rhocentral-case
foampilot skill validate src/foampilot/skills/openfoam-buoyant-case
```

## Exact shock-tube audit

Example using the public pilot inputs:

```bash
foampilot audit shock-tube \
  --left-pressure 100000 \
  --left-temperature 348.432 \
  --right-pressure 10000 \
  --right-temperature 278.746 \
  --molecular-weight 28.96 \
  --cp 1004.5 \
  --time 0.007 \
  --json
```

The self-check additionally reads `deltaT`, `adjustTimeStep`, `maxCo`, and
`maxDeltaT`, parses both Foundation Courant log formats, and checks detected
rarefaction, contact, and shock positions in cell-width units.

## True wall-heat audit

Run on any completed compatible case:

```bash
foampilot audit wall-heat-flux CASE_DIR \
  --openfoam-root /home/edwin/workplace/OpenFOAM-10 \
  --hot-patch HOT_PATCH \
  --cold-patch COLD_PATCH \
  --json
```

The command copies the case to a temporary directory and invokes the case
application with `-postProcess -func wallHeatFlux -latestTime`. This constructs
the thermophysical transport model that the generic `postProcess` utility does
not construct for `buoyantFoam`. The source case is not modified.

For steady buoyant results, the public report combines:

- first/last-window equation initial-residual medians;
- terminal local and cumulative continuity errors;
- hot/cold integrated transport-model `Q`;
- normalized wall-energy imbalance.

Missing evidence fails closed. A normal `End` line remains an execution result,
not a public physics verdict.

## Agent integration

An optional Agent adapter may call these checks after solver completion. A
failed check should enter the same evidence-preserving repair loop as a solver
error. The callback contract receives only:

- the public task;
- the Agent workspace;
- the current solver run and log.

It must not receive a private validation model or golden manifest.
