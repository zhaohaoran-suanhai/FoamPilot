# FoamPilot Contract-First Architecture Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this roadmap plan-by-plan. Execute the five linked plans strictly in order.

**Goal:** Migrate FoamPilot from one coupled model-authored execution plan to a contract-first pipeline with deterministic asset facts, staged model reasoning, a thin coordinator, single-source run evidence, and independent observations.

**Architecture:** Keep the existing Foundation OpenFOAM 10 Runner, sandbox policy, artifact store, job supervision, and immutable lineage. Introduce new contracts behind the current CLI, switch the canonical solve path at the end of each plan, and remove the replaced implementation before advancing so no permanent second solve path exists.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML, pytest 8, PySide6-Essentials 6, Foundation OpenFOAM 10.

## Global Constraints

- Target only Foundation OpenFOAM 10 in the first implementation.
- Preserve typed argv, executable allowlists, sandbox/audited-host policy, immutable attempts, cancellation, and lineage verification.
- The model may propose decisions and uncertainty but may never assign its own release confidence.
- High-impact facts require a concrete value from verified user text, public asset facts, deterministic policy, or per-field user confirmation.
- Never provide a generic continue/accept-all control for unresolved high-impact facts.
- The Case Author writes one coherent native case bundle, not one model request per dictionary.
- A model-authored file may not overwrite an input asset bundle.
- Only the Evidence Extractor may interpret native command output or solver logs.
- The Coordinator may not contain mesh-format, solver-family, physics-family, or log-pattern decisions.
- First-party extensions are enabled; future third-party entry points remain disabled until explicitly allowlisted.
- Keep old runs reportable read-only; do not admit them to the new strict-resume path.
- Do not introduce a universal OpenFOAM dictionary renderer or a second public solve state machine.
- Use `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider` for deterministic gates.

---

## Plan Order

1. [Phase 1: Asset Bundles and Authoritative Mesh Facts](2026-08-12-contract-first-phase-1-assets-mesh-facts.md)
2. [Phase 2: Simulation Intent, Case Design, and Risk Gate](2026-08-12-contract-first-phase-2-intent-design-risk.md)
3. [Phase 3: Case Authoring, Plan Compilation, and Repair Envelope](2026-08-12-contract-first-phase-3-author-plan-repair.md)
4. [Phase 4: Thin Coordinator, Run Facts, Failure Reports, and Projection](2026-08-12-contract-first-phase-4-coordinator-evidence.md)
5. [Phase 5: Observation Planning, Post-processing, and Acceptance](2026-08-12-contract-first-phase-5-observations.md)

## Frozen Cross-Plan Interfaces

The plans may add fields but must not rename these published types after their introducing plan:

```python
AssetBundle
InputMeshFacts
ExecutedMeshFacts
SimulationIntent
ResolvedRequirements
CaseDesignProposal
CaseDesign
RiskDecision
ObservationPlan
CaseBundle
ExecutionPlan
RunFacts
DerivedMetrics
ResultReport
FailureReport
WorkflowProjection
```

Every serialized contract includes `schema_version`, uses `extra="forbid"`, and has a canonical JSON SHA256 recorded in the run.

## Phase Gates

| Phase | Canonical deliverable | Required real gate |
| --- | --- | --- |
| 1 | Directory assets and `polyMesh` facts work before authoring | Synthetic provided polyMesh: stage → inspect → canonical `checkMesh` |
| 2 | Concrete per-field confirmation controls CaseDesign release | The provided-mesh porous prompt reaches either frozen design or a precise pending-question state without manual YAML repair |
| 3 | Case Author cannot choose commands or alter frozen design | Frozen design → case bundle → compiled plan → Foundation v10 solver start |
| 4 | Coordinator is domain-free and all consumers use `RunFacts` | CLI/Desktop agree on stage, residuals, and a forced numerical failure report |
| 5 | Observation and acceptance are independent extensions | At least flow/continuity/pressure/region metrics on a small Foundation v10 case |

## Release Gate After Every Phase

Run:

```bash
git diff --check
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: no whitespace errors and the complete deterministic suite passes.

For package-data changes, also run:

```bash
/home/edwin/feal-venv-py312/bin/python -m build
/home/edwin/feal-venv-py312/bin/python -m zipfile -l dist/foampilot-*.whl
```

Expected: wheel and sdist build; every new first-party descriptor or data file appears in the wheel.

## Completion Definition

The roadmap is complete only when all five plans pass, the old coupled authoring path and duplicate log parsers are deleted, the full test suite passes, and the cross-scenario matrix in the approved design has current evidence. A class existing without its real or replay gate does not satisfy a phase.
