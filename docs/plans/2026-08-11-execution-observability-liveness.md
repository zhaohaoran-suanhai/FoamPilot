# Core Execution Observability and Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every long model and OpenFOAM operation expose truthful, structured liveness and progress through the CLI and run artifacts without depending on Desktop.

**Architecture:** Add a Qt-independent `foampilot.activity` package containing strict activity events, a thread-safe reporter, JSONL/CLI sinks, and a supervised subprocess runner. ModelGateway and PlanRunner emit through that contract; NativeAgent binds run persistence; CLI renders activity on stderr while preserving final stdout JSON.

**Tech Stack:** Python 3.12, Pydantic v2, `subprocess.Popen`, append-only JSONL, pytest.

## Global Constraints

- Keep `NativeAgent.solve()` as the only canonical solve path.
- Keep `WorkflowEvent` as the low-frequency business timeline; never write heartbeat events to `workflow-events.jsonl`.
- Never expose model prompt, response body, credentials, environment values, or hidden chain-of-thought.
- `--json` stdout remains one final JSON document; progress is emitted only on stderr.
- Do not add Qt imports or new mandatory dependencies to core.
- Activity failure is reported as `OBSERVABILITY_DEGRADED`; it must not fabricate a CFD failure.
- Use TDD for every production behavior and commit each reviewable task on the current `main` branch explicitly authorized by the user.

---

### Task 1: Activity event model, reporter, and sinks

**Files:**
- Create: `src/foampilot/activity/models.py`
- Create: `src/foampilot/activity/reporter.py`
- Create: `src/foampilot/activity/sinks.py`
- Create: `src/foampilot/activity/__init__.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces: `ActivityEvent`, `ActivityKind`, `ActivityState`, `ActivitySource`, `ActivityReporter`, `JsonlActivitySink`, `PlainActivitySink`.
- `ActivityReporter.emit(...) -> ActivityEvent` assigns a contiguous thread-safe sequence and calls listeners.
- `ActivityReporter.bind_run(run_id, path)` adds the run JSONL sink without resetting operation ID or sequence.

- [ ] **Step 1: Write failing strict-model and sequencing tests**

```python
def test_activity_reporter_assigns_contiguous_sequence(tmp_path):
    seen = []
    reporter = ActivityReporter(operation_id="op-1", listeners=[seen.append])
    first = reporter.emit(
        kind="stage", state="started", source="model", stage="generation"
    )
    second = reporter.emit(
        kind="heartbeat", state="alive", source="model", elapsed_seconds=5.0
    )
    assert [first.sequence, second.sequence] == [1, 2]
    assert [event.sequence for event in seen] == [1, 2]


def test_bind_run_persists_jsonl_without_resetting_sequence(tmp_path):
    reporter = ActivityReporter(operation_id="op-1")
    reporter.emit(kind="stage", state="started", source="workflow")
    reporter.bind_run("run-1", tmp_path / "activity-events.jsonl")
    event = reporter.emit(kind="heartbeat", state="alive", source="runner")
    stored = ActivityEvent.model_validate_json(
        (tmp_path / "activity-events.jsonl").read_text().strip()
    )
    assert event.sequence == 2
    assert stored.run_id == "run-1"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_activity.py`

Expected: collection fails because `foampilot.activity` does not exist.

- [ ] **Step 3: Implement the strict contract**

```python
class ActivityEvent(StrictModel):
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    operation_id: str
    run_id: str | None = None
    kind: ActivityKind
    state: ActivityState
    source: ActivitySource
    occurred_at: datetime
    elapsed_seconds: float = Field(default=0, ge=0)
    deadline_seconds: float | None = Field(default=None, gt=0)
    attempt: int | None = Field(default=None, ge=1)
    stage: str | None = None
    step_id: str | None = None
    pid: int | None = Field(default=None, ge=1)
    detail_code: str | None = None
    message: str = ""
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    evidence_path: str | None = None
    evidence_offset: int | None = Field(default=None, ge=0)
```

Implement reporter locking, bounded/sanitized message handling, append+flush JSONL, and a plain stderr renderer that never includes model content.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_activity.py`

Expected: all activity tests pass.

Commit: `git commit -m "feat: add core activity event contract"`

### Task 2: Supervised subprocess runner

**Files:**
- Create: `src/foampilot/activity/process.py`
- Modify: `src/foampilot/activity/__init__.py`
- Test: `tests/test_supervised_process.py`

**Interfaces:**
- Consumes: `ActivityReporter` from Task 1.
- Produces: `SupervisedProcessResult` and `run_supervised_process(...)`.
- The function accepts fixed argv, timeout, optional input, file or PIPE streams, activity context, `popen_factory`, heartbeat interval, and monotonic/UTC clocks.

- [ ] **Step 1: Write failing real-child tests**

```python
def test_silent_child_emits_heartbeat_before_completion():
    seen = []
    result = run_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(0.12)"],
        timeout_seconds=1,
        heartbeat_seconds=0.03,
        reporter=ActivityReporter(operation_id="op", listeners=[seen.append]),
        source="model",
        stage="generation",
    )
    assert result.returncode == 0
    assert any(event.kind == "heartbeat" for event in seen)


def test_child_timeout_is_reported_and_reaped():
    seen = []
    result = run_supervised_process(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        timeout_seconds=0.08,
        heartbeat_seconds=0.02,
        reporter=ActivityReporter(operation_id="op", listeners=[seen.append]),
        source="runner",
        stage="solve",
    )
    assert result.timed_out
    assert result.returncode is None
    assert seen[-1].state == "timed_out"
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_supervised_process.py`

Expected: import fails because the supervised runner is missing.

- [ ] **Step 3: Implement polling and timeout**

```python
@dataclass(frozen=True)
class SupervisedProcessResult:
    returncode: int | None
    stdout: str | None
    stderr: str | None
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float
    timed_out: bool
    pid: int


def run_supervised_process(...):
    process = popen_factory(argv, shell=False, text=True, **options)
    while True:
        remaining = timeout_seconds - (monotonic() - started_mono)
        try:
            stdout, stderr = process.communicate(
                input=pending_input,
                timeout=max(min(heartbeat_seconds, remaining), 0.001),
            )
            break
        except subprocess.TimeoutExpired:
            pending_input = None
            if remaining <= heartbeat_seconds:
                process.kill()
                process.communicate()
                timed_out = True
                break
            reporter.emit(kind="heartbeat", state="alive", ...)
```

Use `communicate()` retries to avoid pipe deadlock. Always reap the child after timeout. Task 2 of the next specification will replace direct-child kill with verified process-group cancellation.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_supervised_process.py tests/test_activity.py`

Expected: all tests pass and no child remains.

Commit: `git commit -m "feat: supervise long-running subprocess activity"`

### Task 3: Model activity without prompt or response leakage

**Files:**
- Modify: `src/foampilot/models/backend.py`
- Modify: `src/foampilot/models/command_backend.py`
- Modify: `src/foampilot/models/openai_compatible.py`
- Modify: `src/foampilot/models/gateway.py`
- Modify: `src/foampilot/models/registry.py`
- Test: `tests/test_command_backend.py`
- Test: `tests/test_model_gateway.py`
- Test: `tests/test_model_gateway_failover.py`

**Interfaces:**
- Consumes: `ActivityReporter`, `run_supervised_process`.
- Changes `ModelBackend.exchange(..., activity: ActivityReporter | None = None)` with a default-compatible optional keyword.
- `ModelGateway` accepts `activity_reporter` and passes it to each transport attempt.

- [ ] **Step 1: Write failing model activity and redaction tests**

```python
def test_gateway_reports_transport_identity_without_response_body(...):
    seen = []
    reporter = ActivityReporter(operation_id="op", listeners=[seen.append])
    gateway = _gateway(backend, clock, activity_reporter=reporter)
    gateway.generate_structured(request, Output, budget=budget, trace=trace)
    assert any(event.state == "started" and event.source == "model" for event in seen)
    assert any(event.state == "completed" for event in seen)
    assert all("secret response" not in event.message for event in seen)


def test_command_backend_silent_process_emits_heartbeat(...):
    response = backend.exchange(request, timeout_seconds=1, activity=reporter)
    assert response.output_text == expected_json
    assert any(event.kind == "heartbeat" for event in seen)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_command_backend.py tests/test_model_gateway.py tests/test_model_gateway_failover.py`

Expected: optional activity interface and events are absent.

- [ ] **Step 3: Integrate supervised command and gateway attempts**

Pass the optional reporter through the protocol. CommandBackend uses the supervised runner with captured output; OpenAI-compatible backend accepts the keyword but keeps its existing bounded HTTP transport. Gateway emits attempt started/completed/failed with backend/model/purpose and counts only, never response text.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_command_backend.py tests/test_openai_compatible_backend.py tests/test_model_gateway.py tests/test_model_gateway_failover.py`

Expected: all focused model tests pass.

Commit: `git commit -m "feat: report model request liveness"`

### Task 4: OpenFOAM step lifecycle and incremental metrics

**Files:**
- Modify: `src/foampilot/runtime/plan_runner.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/desktop/telemetry.py`
- Test: `tests/test_plan_runner.py`
- Test: `tests/test_native_agent_state_machine.py`
- Test: `tests/test_desktop_telemetry.py`

**Interfaces:**
- PlanRunner accepts `workflow_event_listener` and `activity_reporter` callbacks.
- It emits command start before process launch, heartbeat/log offset while running, and command complete/fail immediately after result creation.
- The existing residual parser gains an incremental line-oriented entry point reused by core and Desktop.

- [ ] **Step 1: Write failing event-order and metric tests**

```python
def test_runner_emits_step_started_before_executor_returns(...):
    observed = []
    runner = PlanRunner(..., workflow_event_listener=observed.append)
    runner.run(...)
    assert observed[0].stage == WorkflowStage.OPENFOAM_STEP_STARTED
    assert observed[0].state == WorkflowEventState.STARTED


def test_runner_reports_real_residual_without_zero_fallback(...):
    events = []
    runner = PlanRunner(..., activity_reporter=reporter(events))
    runner.run(...)
    metrics = [event.metrics for event in events if event.kind == "metric"]
    assert {item["field"] for item in metrics} == {"Ux"}
    assert metrics[0]["initial_residual"] == pytest.approx(0.12)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_plan_runner.py tests/test_native_agent_state_machine.py tests/test_desktop_telemetry.py`

Expected: PlanRunner has no lifecycle callbacks and NativeAgent still replays events after return.

- [ ] **Step 3: Move lifecycle ownership into PlanRunner**

PlanRunner records/forwards start just before `run_supervised_process`, monitors log size for progress, parses only new complete lines, and emits completion immediately. NativeAgent passes its `WorkflowStore` callback and removes the post-return replay loop. Preserve the existing `PlanRunResult` contract and evidence paths.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_plan_runner.py tests/test_native_agent_state_machine.py tests/test_desktop_telemetry.py`

Expected: all focused runner/state/telemetry tests pass.

Commit: `git commit -m "feat: stream OpenFOAM step activity"`

### Task 5: Bind run persistence and CLI progress

**Files:**
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/desktop/job_controller.py`
- Modify: `tests/test_native_agent_cli.py`
- Modify: `tests/test_desktop_job_controller.py`
- Create: `tests/test_cli_progress.py`

**Interfaces:**
- Long-running parsers accept `--progress auto|plain|jsonl|none`.
- `NativeAgent` binds `activity-events.jsonl` immediately after creating the run.
- Desktop launches long jobs with `--progress=jsonl` and treats each stderr JSON line as structured activity while preserving raw diagnostics for invalid lines.

- [ ] **Step 1: Write failing CLI compatibility tests**

```python
def test_json_progress_keeps_final_stdout_clean(capsys, ...):
    exit_code = main(["solve", str(task), "--run-root", str(root),
                      "--json", "--progress", "jsonl"])
    captured = capsys.readouterr()
    assert exit_code == 0
    json.loads(captured.out)
    assert all(ActivityEvent.model_validate_json(line)
               for line in captured.err.splitlines() if line.strip())


def test_none_progress_still_persists_run_activity(...):
    outcome = agent.solve(task)
    assert (outcome.run_dir / "activity-events.jsonl").is_file()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_cli_progress.py tests/test_native_agent_cli.py tests/test_desktop_job_controller.py`

Expected: parser rejects `--progress` and no activity artifact exists.

- [ ] **Step 3: Implement parser, sinks, and Desktop structured output**

Add the option only to long operations, default `auto`, render to stderr, and keep `_emit()` final stdout unchanged. Bind the JSONL sink when the run is created. Desktop requests JSONL and emits a dedicated activity signal without using messages as solver truth.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests/test_cli_progress.py tests/test_native_agent_cli.py tests/test_desktop_job_controller.py`

Expected: all focused tests pass.

Commit: `git commit -m "feat: expose structured CLI progress"`

### Task 6: Full verification and task-one report

**Files:**
- Modify: `docs/design/execution-observability-liveness-design.md`
- Create: `docs/reports/2026-08-11-execution-observability-liveness.md`

**Interfaces:**
- Consumes all previous tasks.
- Produces auditable deterministic and local-real-gate evidence before Task 2 begins.

- [ ] **Step 1: Run deterministic gates**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests`

Expected: all tests pass; environment-dependent tests may remain explicitly skipped.

- [ ] **Step 2: Run package and whitespace gates**

Run: `git diff --check`

Run: `/home/edwin/feal-venv-py312/bin/python -m build`

Expected: no whitespace errors; wheel and sdist build successfully.

- [ ] **Step 3: Run local Foundation v10 gate**

Run preflight/model doctor, then one smallest existing non-tutorial TaskSpec through `foampilot solve --progress jsonl`. Verify command-start ordering, heartbeat or real metrics, clean final JSON, `PUBLIC_VALIDATION_PASS`, and manifest integrity. If external model availability fails, record the exact backend blocker and retain deterministic gate status without fabricating a pass.

- [ ] **Step 4: Record evidence and commit**

Mark the design implemented only when all required deterministic and available local gates have evidence. The report separates test pass, solver completion, public validation, and qualification.

Commit: `git commit -m "docs: report execution observability verification"`
