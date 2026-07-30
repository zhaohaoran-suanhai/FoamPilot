# Controlled-learning 15-case qualification

## Outcome

The frozen `controlled-learning-15-v1` baseline produced 11 strict
qualification passes and four failures. All 15 model-authored cases entered
their requested Foundation OpenFOAM v10 solver. Fourteen reached
`PUBLIC_VALIDATION_PASS`; the CHT case failed during its solver run.

After evidence-scoped corrections, targeted reruns passed all four affected
families:

- buoyant cavity: `PASS`, first attempt;
- CHT cooling cylinder: `PASS`, second attempt;
- multiphase capillary rise: `PASS`, first attempt;
- porous angled duct: `PASS`, first attempt.

These are separate targeted reruns. They demonstrate that each correction
works on its affected case, but they are not reported as a fresh, single
15/15 stochastic suite run.

## Frozen baseline

The baseline used `gpt-5.6-sol`, two ordinary workers, exclusive scheduling
for large cases, and wheel SHA256
`ef87bb70a141cc8af11349068842739df6d15cb8db205f606ebc2d712f5cab6b`.

| Role | Case | Strict result | Native result | Attempts |
| --- | --- | --- | --- | ---: |
| regression | laminar cavity | PASS | public pass | 1 |
| regression | potential cylinder | PASS | public pass | 2 |
| regression | RANS pitzDaily | PASS | public pass | 2 |
| regression | multiphase dam break | PASS | public pass | 1 |
| regression | compressible shock tube | PASS | public pass | 1 |
| regression | buoyant cavity | FAIL | public pass | 2 |
| development | scalar transport pitzDaily | PASS | public pass | 1 |
| development | laminar planar Poiseuille | PASS | public pass | 1 |
| development | porous angled duct | FAIL | public pass | 1 |
| development | compressible blocked channel | PASS | public pass | 2 |
| development | CHT cooling cylinder | FAIL | solver failed | 2 |
| development | SRF rotor | PASS | public pass | 2 |
| holdout | MHD Hartmann | PASS | public pass | 1 |
| holdout | multiphase capillary rise | FAIL | public pass | 1 |
| holdout | solid plate hole | PASS | public pass | 1 |

The frozen JSON report is
`/tmp/foampilot-controlled-learning-15-candidate9-20260730/controlled-learning-15-v1-report.json`.
Runtime evidence is intentionally outside the Git repository.

## Failure analysis and corrections

### Buoyant cavity

The original wall-heat-balance threshold was audited against a fresh,
evaluator-owned run of the exact Foundation v10 tutorial. At iteration 1000,
the official case itself had a normalized hot/cold wall imbalance of about
0.463. The previous upper bound of 0.1 therefore rejected the reference
behavior.

The evaluator contract was corrected to a public absolute upper bound of 0.5.
The targeted rerun passed with wall imbalance 0.4850, normalized profile
error 0.00358, and mean-Nusselt relative error 0.0547. This was an evaluator
correction backed by the official source result, not a relaxed threshold
chosen to fit the Agent output.

### CHT cooling cylinder

The baseline exposed incomplete multi-region thermophysical and stability
guidance. The solver-family contract now describes region dictionaries,
coupled interface fields, complete turbulence fields, dimensioned thermo
entries, and the distinction between a time-step cap and a requested time
step. The targeted run entered `chtMultiRegionFoam`; its bounded repair
reduced an excessive diffusion-number cap and reached 20 s.

Strict qualification passed with zero measured interface mismatch and
temperature-profile relative error 0.0572.

### Multiphase capillary rise

The generated VOF case completed but did not conserve the requested liquid
inventory closely enough. The public interFoam guidance was sharpened around
Foundation v10 alpha transport schemes, boundedness, and conservative
initialization without adding a task template.

The targeted rerun passed on its first attempt. Liquid-volume relative error
was 0.00373 and interface-height relative error was zero.

### Porous angled duct

Two independent issues were found. First, turbulent porous cases require
positive internal `k` and `epsilon`; zero initialization caused solver
failure. Second, the public geometry wording did not uniquely specify the
projected upstream inlet plane and permitted a materially different duct.

The solver-family contract now covers positive turbulence initialization,
wall functions, porous SIMPLE controls, and compatible numerics. The public
TaskSpec now states the previously ambiguous inlet geometry explicitly. The
targeted rerun passed in one attempt with flow imbalance 0.00118 and
pressure-drop relative error 0.0499.

## Additional unseen solver-family gate

After the 15-case work, a temporary natural-language TaskSpec requested a
`shallowWaterFoam` square-bed-bump case. It was not added as a permanent
qualification fixture.

The first blind run reached `blockMesh` and `setFields` but exposed missing
`PIMPLE` and `constant/gravitationalProperties` contracts. A small public
solver-family knowledge entry was added. The next run reached 100 s, which
then revealed an error in the temporary evaluator request: Foundation v10
reads `h0` as a static const input and does not automatically write
`100/h0`. The task was corrected to validate the initialized `0/h0` field.

The final gate passed after one log-driven repair to add the required
dimensioned value names in `gravitationalProperties`:

```text
g       g       [0 1 -2 0 0 0 0] (0 0 -9.81);
Omega   Omega   [0 0 -1 0 0 0 0] (0 0 7.292e-5);
```

`shallowWaterFoam` ended normally at 100 s with mean Courant number about
0.200 and maximum gravity-wave Courant number about 0.626. All ten public
checks passed. The run is preserved at
`/tmp/foampilot-holdout-shallow-water-candidate14-20260730/run-20260730T052302630290Z-4c69b2ff`.

## What changed and what did not

The supported workflow remains:

```text
public TaskSpec
-> dynamic public knowledge and one authoring Skill
-> complete model-authored case and typed commands
-> lightweight safety and consistency inspection
-> networkless OpenFOAM execution
-> evaluator-owned checks
-> at most one evidence-scoped repair
-> immutable artifacts
```

No MCP, RAG database, official case renderer, per-case generation template,
or mandatory model-review stage was introduced. Changes were limited to
role-aware suite assets, focused physics extractors, lightweight pre-solve
diagnostics, and generalized Foundation v10 knowledge.

## Evidence boundary

- The deterministic test suite and wheel checks establish software/package
  consistency.
- A solver start establishes that the workflow produced a readable case, not
  that the physics is correct.
- `PUBLIC_VALIDATION_PASS` establishes the task-visible checks only.
- Strict qualification adds evaluator-owned physics comparisons.
- The targeted corrected results do not replace a future fresh 15-case run.
- Model generation remains stochastic; reproducibility means the repository
  contains the workflow, public contracts, and evidence needed to rerun the
  process, not that every future model call will emit identical dictionaries.
