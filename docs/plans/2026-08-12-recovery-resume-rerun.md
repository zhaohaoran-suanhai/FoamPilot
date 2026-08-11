# Recovery, Strict Resume and Rerun Implementation Plan

> **Execution rule:** implement serially on the user-authorized current branch, with RED tests before each behavior and a reviewable commit per task.

**Goal:** Give every interrupted or completed local job a deterministic diagnosis and expose only truthful recovery actions: attach, controlled orphan termination, recover-finalize, strict model-stage resume, or full rerun with lineage.

**Architecture:** Add a Qt-independent recovery service over `LocalJobStore`, process identity, workflow artifacts and manifest verification. Reconcile is read-only and returns a strict decision model. Mutation commands require the writer lock and a fresh reconcile. Interrupted finalization writes neutral workflow evidence and an immutable manifest. Strict resume keeps its existing generation/repair eligibility but supports a parent in a different job artifact root using cumulative continuation evidence. Rerun enters the canonical `NativeAgent.solve()` path with an explicit lineage input; no OpenFOAM continuation or second solver path is added.

**Tech stack:** Python 3.12, Pydantic v2, `fcntl`, `/proc`, append-only workflow JSONL, existing `ArtifactStore`/`NativeAgent`, PySide6, pytest/pytest-qt.

## Global constraints

- Never infer success from PID disappearance, heartbeat expiry or a partial time directory.
- Reconcile never signals a process or changes artifacts.
- Every signal requires full recorded process identity; unknown/mismatched identities are not killed.
- Recover-finalize runs only after the writer and owned child are confirmed absent and the writer lock is acquired.
- Parent run and attempts stay immutable; resume/rerun create child runs and manifested lineage.
- Strict resume remains limited to retryable generation/repair checkpoints.
- Rerun starts a complete canonical solve and never implies checkpoint reuse.
- OpenFOAM continuation remains explicitly unsupported.

### Task 1: Deterministic reconcile and orphan process control

**Files:** create `src/foampilot/jobs/recovery.py`; modify job models/store/exports; create `tests/test_job_recovery.py`.

- [x] Write RED decision-table tests for running, unresponsive, orphaned-active, orphaned-stopped, finalized, damaged evidence, PID reuse and held/free writer lock.
- [x] Implement strict `RecoveryState`, `RecoveryAction`, `RecoveryDecision` and read-only `reconcile_job()`.
- [x] Add non-mutating writer-lock inspection and bounded, identity-checked orphan process-group termination.
- [x] Add `job reconcile` and `job terminate-orphan` JSON CLI contracts.
- [x] Run focused jobs/CLI tests and commit `feat: reconcile local job recovery state`.

### Task 2: Neutral recover-finalize

**Files:** modify workflow/artifact models; extend `jobs/recovery.py`; modify CLI; create recovery-finalization tests.

- [ ] Write RED tests for worker/child absence, lock exclusion, idempotence, interruption evidence, event sequence, summary semantics and valid manifest.
- [ ] Add `WorkflowState.INTERRUPTED` and event state `interrupted`; do not add a native CFD status.
- [ ] Write `interruption.json`, append `RUN_FINALIZED/interrupted`, create `RunSummary` with workflow-domain blocker, `resume.allowed=false`, then finalize with `ArtifactStore`.
- [ ] Mark the operational job `INTERRUPTED`; repeated calls return the same verified result without rewriting it.
- [ ] Add `job recover-finalize` and commit `feat: finalize interrupted local jobs`.

### Task 3: Cross-job strict-resume lineage and explicit rerun

**Files:** extend `workflow/lineage.py`; modify `NativeAgent` and CLI; modify continuation tests; create rerun tests.

- [ ] Write RED tests proving external immutable parent roots work, cumulative transport/logical/execution budgets remain bounded, and existing compatibility rejection is unchanged.
- [ ] Replace same-artifact-root ancestry assumptions with manifested cumulative continuation evidence while validating the explicit parent with its own `ArtifactStore`.
- [ ] Add strict `LineageRecord` for `strict_resume`, `rerun_same_input` and `rerun_with_changes`; write it before child finalization.
- [ ] Add `foampilot rerun PARENT --run-root ... [--task ...]`; unchanged normative input is `rerun_same_input`, any proven/declared change is `rerun_with_changes` with categories and before/after hashes.
- [ ] Add `JobOperation.RERUN`, detached worker support and CLI/agent regression tests; commit `feat: add explicit rerun lineage`.

### Task 4: Desktop recovery action matrix

**Files:** modify `desktop/job_controller.py`, `main_window.py`, user docs and Desktop tests.

- [ ] Write RED Qt tests for each recovery state and allowed/forbidden operation set.
- [ ] Make startup discovery retain orphaned jobs instead of silently ignoring them and emit the deterministic reconcile decision.
- [ ] Add explicit actions for attach/cancel, terminate orphan, recover-finalize, strict model resume and complete rerun; disable each action with stable diagnostic text when ineligible.
- [ ] Label strict resume by the exact model stage and keep OpenFOAM continuation visibly unsupported.
- [ ] Run Desktop tests and commit `feat: expose truthful desktop recovery actions`.

### Task 5: Combined verification and report

**Files:** update design/plan status; create `docs/reports/2026-08-12-recovery-resume-rerun.md`; update changelog/user docs.

- [ ] Run the full offscreen deterministic suite, `git diff --check`, wheel/sdist and source-isolated wheel import.
- [ ] Run real Foundation v10 strict-resume/rerun gates available on this host and verify parent manifests remain unchanged.
- [ ] Audit every recovery action against the decision table and verify no OpenFOAM continuation claim or implementation exists.
- [ ] Record exact deterministic, real-solver and unavailable external-model/Desktop-click evidence; commit `docs: report recovery and rerun verification`.
