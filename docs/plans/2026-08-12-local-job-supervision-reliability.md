# Local Job Supervision and Desktop Reliability Implementation Plan

> **Execution rule:** implement serially on the user-authorized current branch, using TDD and a commit after each reviewable task.

**Goal:** Move long local work out of the Desktop process so jobs survive window closure, can be rediscovered, expose durable identity/heartbeat, and can be cancelled without leaving owned model/OpenFOAM/MPI descendants.

**Architecture:** Add a Qt-independent `foampilot.jobs` package with strict receipts, atomic status, Linux process identity, writer locking, cancellation control, and one detached worker per job. The worker invokes the existing CLI application service in-process with an injected `ActivityReporter`; the reporter is also the cancellation token passed to supervised external commands. Desktop submits/attaches through durable job files and only observes them with a timer. No shell, daemon, alternate solver path, or system service is introduced.

**Tech stack:** Python 3.12, Pydantic v2, `fcntl`, `/proc`, `subprocess.Popen(start_new_session=True)`, append-only JSONL, PySide6, pytest/pytest-qt.

## Global constraints

- Keep `NativeAgent.solve()` as the only solve path.
- Job state is operational state; it never replaces workflow/native/public-validation state.
- Fixed argv only; no shell and no secret-bearing job receipt.
- Only the writer-lock owner mutates `job-status.json`.
- PID operations require PID + process start token + boot ID match.
- Cancellation is successful only after the owned process group has exited.
- Closing Desktop never implicitly cancels a job.
- Preserve existing direct CLI behavior and stdout JSON contract.

### Task 1: Strict job receipt, status, identity and atomic store

**Files:** create `src/foampilot/jobs/models.py`, `identity.py`, `store.py`, `__init__.py`; create `tests/test_job_store.py`.

- [x] Write RED tests for strict model validation, atomic create/read/update, monotonic revision, symlink/path escape rejection, cancel request idempotence, writer lock exclusion and `/proc` start-token matching.
- [x] Implement `JobSpec`, `JobStatus`, `JobState`, `ProcessIdentity`, `CancelRequest` and `LocalJobStore`.
- [x] Hash declared project inputs and prohibit secret-shaped argv/unknown operations.
- [x] Run `tests/test_job_store.py` and commit `feat: add durable local job contract`.

### Task 2: Cooperative process-group cancellation

**Files:** modify `src/foampilot/activity/models.py`, `reporter.py`, `process.py`; modify model and runtime integration; modify workflow/native summary models; create/modify cancellation tests.

- [x] Write RED tests for SIGTERM cancellation, SIGKILL escalation, descendant cleanup, cancellation during model retry and OpenFOAM step cancellation.
- [x] Add `OperationCancelled`, reporter cancellation callback, `ActivityState.CANCELLED`, and `SupervisedProcessResult.cancelled`.
- [x] Poll cancellation independently of heartbeat frequency; signal only the identity-owned process group, reap it, and emit a cancelled terminal event.
- [x] Propagate cancellation through CommandBackend/ModelGateway/PlanRunner and finalize solve as `WorkflowState.CANCELLED`, never automatic repair.
- [x] Run focused model/runtime/native tests and commit `feat: cancel owned execution groups`.

### Task 3: Detached one-job worker

**Files:** create `src/foampilot/jobs/worker.py`; modify `src/foampilot/cli/main.py`; create `tests/test_job_worker.py` and `tests/test_job_cli.py`.

- [x] Write RED fake-operation tests proving Desktop parent exit does not end the worker, status heartbeat advances, ActivityEvent updates child identity, terminal output is durable, and cancel is idempotent.
- [x] Add internal `worker run JOB_ROOT` and `job cancel/status` CLI endpoints.
- [x] Inject one worker-owned reporter into the existing CLI service rather than spawning a nested CLI; redirect final stdout/stderr to job logs.
- [x] Hold writer lock for worker lifetime and atomically transition `SUBMITTED -> STARTING -> RUNNING -> terminal`.
- [x] Run focused worker/CLI tests and commit `feat: run detached local jobs`.

### Task 4: Desktop submit, attach and cancel

**Files:** modify `desktop/job_controller.py`, `workspace.py`, `main_window.py`; modify Desktop tests.

- [x] Write RED Qt tests for detached submit, close while running, startup discovery/attach, stale heartbeat display, explicit cancel, and single terminal state under cancel/complete race.
- [x] Make long draft/solve/resume operations create JobSpec and launch the detached worker; retain direct QProcess only for bounded preflight/validate/compile helpers.
- [x] Poll durable status/events, expose `current_job`, `request_cancel()` and activity signals, and never rely on a live QProcess object for job truth.
- [x] Add Cancel action and remove close-event blocking. On workspace open, scan only controlled `runs/job-*` and attach to the newest active job.
- [x] Run Desktop tests and commit `feat: reconnect desktop jobs`.

### Task 5: Incremental active-run reads and bounded UI work

**Files:** create `desktop/cursors.py`; modify `desktop/repository.py`, `main_window.py`; create cursor/performance tests.

- [x] Write RED tests for JSONL partial tails, truncation/rotation, incremental log residuals, unchanged manifest cache, bounded samples and projection equivalence.
- [x] Implement byte-offset/inode cursors independent of Qt and cache finalized manifest verification.
- [x] Replace repeated full workflow/log reads for active runs with incremental projections; preserve last good snapshot and surface `DESKTOP_REFRESH_DEGRADED` on read failure.
- [x] Run synthetic large-run and Desktop responsiveness gates; commit `perf: make desktop run refresh incremental`.

### Task 6: Verification and report

**Files:** update design status; create `docs/reports/2026-08-12-local-job-supervision-reliability.md`.

- [x] Run the full deterministic suite with offscreen Qt, `git diff --check`, wheel/sdist build and package import checks.
- [x] Run fake detached-worker close/reopen/cancel gates and inspect that no owned descendant remains.
- [x] Run the available Foundation v10 frozen-plan job gate; test Desktop close/reattach and cancellation where the current host allows it.
- [x] Record deterministic, solver, public-validation and unavailable external-model evidence separately; commit `docs: report local job supervision verification`.
