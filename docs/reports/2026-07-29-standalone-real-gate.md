# Standalone real-case gate

Date: 2026-07-29

## Scope

This gate verifies the independently built and installed FoamPilot wheel
against two cases authored from public requirements and empty case
directories. Neither run reads or copies a target OpenFOAM tutorial.

This is a runtime and public-validation gate. It is not an official-six
qualification result and does not establish universal case-generation
reliability.

## Environment

- Python 3.12;
- Foundation OpenFOAM v10;
- networkless bubblewrap execution;
- model `gpt-5.6-sol`;
- serial native OpenFOAM commands;
- installed `foampilot` 0.1.0 wheel.

The final host preflight passed Python, OpenFOAM root and bashrc, tutorial-root
inventory, bubblewrap launch, and `icoFoam` discovery.

## Results

| Task | Attempts | Final status | Key evidence |
| --- | ---: | --- | --- |
| Non-tutorial side-driven laminar enclosure | 1 | `PUBLIC_VALIDATION_PASS` | `Mesh OK`, normal `End`, time 1.0 s, finite fields, cumulative continuity error `-3.267627635e-20` |
| Non-tutorial two-phase column collapse | 2 | `PUBLIC_VALIDATION_PASS` | `Mesh OK`, `setFields` success, normal `End`, time 0.1 s, phase bounds and phase-volume conservation pass |

The two-phase initial attempt completed but its all-time
`alpha.water` maximum was `1.000001475`, above the public limit
`1.000001`. The repair model received the public evidence plus the same
dynamic public knowledge and workflow Skill used for initial authoring. It
changed only `system/controlDict`, reducing `maxDeltaT` from `2.5e-4` s to
`1e-4` s.

The repaired run observed:

- all-time `alpha.water` minimum: `-9.252963917e-09`;
- all-time `alpha.water` maximum: `1.000000515`;
- normalized phase-volume drift: `3.428571428908079e-08`;
- 1001 extrema/integral samples;
- all ten public checks passed.

Both frozen run manifests verified with no issues.

## Engineering lesson

The first extracted two-phase run spent its repair budget on a missing
Foundation v10 `interFoam` viscous-divergence scheme. Adding that exact public
solver contract prevented the startup failure. A later run exposed that
authoring knowledge was not forwarded to the repair call; without it, the
model changed a linear-solver tolerance instead of the time-step family.

Forwarding the already selected public knowledge and Skill completed the
existing repair loop. No model reviewer, deterministic case renderer, or
additional mechanical gate was introduced.

## Evidence boundary

The deterministic suite, wheel installation, preflight, solver completion,
public validation, and official-six qualification are separate gates. The
current result establishes the first five for the scoped cases. A fresh
official-six run remains a separate action.
