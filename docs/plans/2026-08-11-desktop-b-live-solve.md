# FoamPilot Desktop B Live Solve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan inline, task by task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not use subagents. Do not commit unless the user explicitly requests it.

**Goal:** 把 Desktop A 扩展成可从自然语言或 TaskSpec 创建任务、通过规范 CLI 启动真实求解，并实时显示公开 Knowledge/Skill 引用和 OpenFOAM residual 的可用桌面 IDE。

**Architecture:** Qt 只负责工程输入、固定 argv 的 QProcess 和 artifact 投影。每次 solve 使用独占 job root，`RunRepository` 从 canonical run 读取 workflow/context/log/summary，Qt 不建立第二套 CFD 状态。

**Tech Stack:** Python 3.12、Pydantic v2、PyYAML、PySide6 Essentials/Qt Widgets、QProcess、pytest、pytest-qt、现有 TaskBuilder、ArtifactStore、WorkflowEvent 和 Foundation OpenFOAM v10 log contract。

## Global Constraints

- `NativeAgent.solve()` 是唯一求解主链；UI 不直接调用 Runner/OpenFOAM。
- QProcess program 固定为当前 Python，prefix 固定为 `-m foampilot.cli.main`，禁止 shell string。
- 只显示公开结构化 Knowledge/Skill 引用，不显示或推测隐藏 chain-of-thought。
- residual 缺失时显示 unavailable/等待数据，不能画零曲线。
- Desktop core 继续不依赖 Qt；PySide6 保持 optional extra。
- 只访问用户显式工程目录和 run；拒绝符号链接与路径逃逸。
- 运行状态、workflow、native、public validation 和 manifest verdict 分开显示。
- 当前未提交 Desktop A 改动属于用户工作；在当前 checkout 内增量修改，不创建 worktree，不 commit/push。

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/foampilot/desktop/workspace.py` | 安全工程目录、版本化输入和 TaskDraft 确认 |
| `src/foampilot/desktop/telemetry.py` | Qt 无关的 residual series 解析 |
| `src/foampilot/desktop/job_controller.py` | 固定 argv QProcess、输出、独占 run 发现 |
| `src/foampilot/desktop/residual_plot.py` | PySide6 Essentials 自绘 residual 曲线 |
| `src/foampilot/desktop/viewmodels.py` | context、skill、residual view models |
| `src/foampilot/desktop/repository.py` | batch root、context metadata 和 telemetry 投影 |
| `src/foampilot/desktop/main_window.py` | 任务/context/residual/artifact 页面与命令状态机 |
| `src/foampilot/desktop/application.py` | 启动与窗口生命周期 |
| `tests/test_desktop_workspace.py` | workspace 与 draft confirmation |
| `tests/test_desktop_telemetry.py` | residual parser |
| `tests/test_desktop_job_controller.py` | QProcess 与 run discovery |
| `tests/test_desktop_repository.py` | batch/context/telemetry repository |
| `tests/test_desktop_main_window.py` | 新 UI、命令流和 live refresh |
| `docs/desktop-ide.md` | 新用户操作说明 |
| `docs/reports/2026-08-11-desktop-b-live-solve.md` | 完成证据和限制 |

---

### Task 1: Reject Batch Roots and Project Public Context

**Files:**
- Modify: `src/foampilot/desktop/viewmodels.py`
- Modify: `src/foampilot/desktop/repository.py`
- Modify: `tests/test_desktop_repository.py`

**Interfaces:**
- Produces: `RunCollectionError(RunOpenError).children: tuple[Path, ...]`
- Produces: `KnowledgeReference`, `SkillReference`, and new `RunSnapshot.context_references/skill_references` fields.

- [ ] **Step 1: Write failing batch-root and context tests**

```python
with pytest.raises(RunCollectionError) as captured:
    repository.open(batch_root)
assert captured.value.children == (run_a.resolve(), run_b.resolve())

snapshot = repository.open(active_run)
assert snapshot.context_references[0].entry_id == "of10.ico.contract"
assert snapshot.context_references[0].slot == "solver_family_contract"
assert snapshot.skill_references[0].name == "openfoam-author-native-case"
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_desktop_repository.py`

Expected: missing `RunCollectionError` and context fields.

- [ ] **Step 3: Implement immutable context projections**

```python
class KnowledgeReference(FrozenModel):
    stage: Literal["author", "repair"]
    attempt: int | None
    slot: str
    entry_id: str
    title: str | None
    knowledge_type: str | None
    source_locator: str | None
    source_sha256: str | None

class SkillReference(FrozenModel):
    stage: Literal["author", "repair"]
    attempt: int | None
    name: str
```

Load root and attempt `*agent-context.json` files, enrich IDs from the formal package corpus, retain artifact hashes, and add malformed context to warnings.

- [ ] **Step 4: Implement collection detection**

Before normal run rendering, if the selected directory has no direct run control artifact and contains real non-symlink `run-*` children, raise `RunCollectionError` with the sorted resolved children.

- [ ] **Step 5: Run repository tests**

Expected: existing safety tests and new batch/context tests pass.

---

### Task 2: Parse Residual Series With Time Association

**Files:**
- Create: `src/foampilot/desktop/telemetry.py`
- Modify: `src/foampilot/desktop/viewmodels.py`
- Modify: `src/foampilot/desktop/repository.py`
- Create: `tests/test_desktop_telemetry.py`
- Modify: `tests/test_desktop_repository.py`

**Interfaces:**
- Produces: `parse_residual_series(text, *, attempt, source_log) -> tuple[ResidualSample, ...]`
- `ResidualSample` fields match the approved design contract.

- [ ] **Step 1: Write a failing parser test**

```python
samples = parse_residual_series(
    "Time = 0.1\n"
    "smoothSolver: Solving for Ux, Initial residual = 0.2, "
    "Final residual = 0.01, No Iterations 2\n"
    "Time = 0.2\n"
    "GAMG: Solving for p, Initial residual = 0.1, "
    "Final residual = 0.001, No Iterations 3\n",
    attempt=1,
    source_log="attempt-01/case/.foampilot/logs/solve.stdout.log",
)
assert [(s.field, s.simulation_time) for s in samples] == [
    ("Ux", 0.1), ("p", 0.2)
]
```

- [ ] **Step 2: Confirm RED**

Run the new test file; expect import failure.

- [ ] **Step 3: Implement the line parser**

Track the latest `Time =` or `Iteration =`, parse scientific notation, ignore non-positive/non-finite residuals, and retain log order with a monotonic sequence.

- [ ] **Step 4: Add repository telemetry projection**

Parse safe `*.stdout.log` files under attempt log directories with a bounded byte budget, attach attempt/source log, cap the display projection to the latest 5,000 samples, and warn when a log is skipped for size or malformed content.

- [ ] **Step 5: Verify parser and repository tests**

Expected: time association, repeated fields, empty logs, malformed lines, attempt extraction and cap behavior pass.

---

### Task 3: Safe Desktop Workspace and Draft Confirmation

**Files:**
- Create: `src/foampilot/desktop/workspace.py`
- Create: `tests/test_desktop_workspace.py`

**Interfaces:**
- Produces: `DesktopWorkspace.open(path) -> DesktopWorkspace`
- Produces: `save_request(text)`, `save_draft(text)`, `save_task(text)`, `create_job_root()`.
- Produces: `confirm_task_draft(text, answers) -> str`.

- [ ] **Step 1: Write failing workspace tests**

```python
workspace = DesktopWorkspace.open(tmp_path / "project")
assert workspace.save_request("求解方腔流").name == "request-001.md"
assert workspace.save_request("再次求解").name == "request-002.md"
assert workspace.create_job_root().parent == workspace.runs_dir
```

Also reject a symlink root and blank request.

- [ ] **Step 2: Write a failing confirmation test**

Given one model-inference fact and one blocking question, confirm the inference, add a `user_confirmation` fact from the YAML-parsed answer, clear questions, set status to `confirmed`, and verify `validate_task_draft(...).can_compile` when the fixture is otherwise complete.

- [ ] **Step 3: Confirm RED**

Run `tests/test_desktop_workspace.py`; expect missing module.

- [ ] **Step 4: Implement safe versioned writes**

Use explicit subdirectories, same-directory `NamedTemporaryFile`, `fsync`, and `os.replace`. Reject root/subdirectory symlinks and never resolve a relative write outside the workspace.

- [ ] **Step 5: Implement deterministic confirmation**

Parse answers with `yaml.safe_load`; convert confirmed model inference to `user_confirmation`, preserve evidence, upsert question answers by exact fact path, and validate the resulting `TaskDraft` before serializing YAML.

- [ ] **Step 6: Verify workspace tests**

Expected: versioning, atomic path boundary, answer parsing and TaskDraft state tests pass.

---

### Task 4: Fixed-Argv QProcess Controller

**Files:**
- Create: `src/foampilot/desktop/job_controller.py`
- Create: `tests/test_desktop_job_controller.py`

**Interfaces:**
- Produces: `DesktopJobController.start_cli(arguments, *, run_root=None) -> None`
- Signals: `job_started(str)`, `output_received(str, str)`, `run_discovered(Path)`, `job_finished(int, str)`, `job_error(str, str)`.

- [ ] **Step 1: Write failing Qt tests**

Inject `sys.executable` with an empty prefix for a fake test process. Assert stdout/stderr delivery, busy rejection, exit code delivery, and discovery of exactly one `run-*` child inside the supplied unique root.

- [ ] **Step 2: Confirm RED**

Run offscreen `tests/test_desktop_job_controller.py`; expect missing controller.

- [ ] **Step 3: Implement fixed program/args**

Default to:

```python
program = sys.executable
prefix_args = ("-m", "foampilot.cli.main")
```

Allow only registered top-level commands, pass each argument separately, use separate stdout/stderr channels, and never expose a shell-command API.

- [ ] **Step 4: Implement run discovery**

Use a short QTimer while a solve is active, reject symlink children, emit once for the sole `run-*`, and emit a deterministic error if multiple runs appear in an allegedly unique root.

- [ ] **Step 5: Verify QProcess tests**

Expected: process, stream, busy, failure and discovery tests pass without a real OpenFOAM run.

---

### Task 5: Residual Plot and Live Context Views

**Files:**
- Create: `src/foampilot/desktop/residual_plot.py`
- Modify: `src/foampilot/desktop/main_window.py`
- Modify: `tests/test_desktop_main_window.py`

**Interfaces:**
- Produces: `ResidualPlot.set_samples(samples)` and inspectable `sample_count/fields` properties.

- [ ] **Step 1: Write failing widget/render tests**

Open an active run containing `agent-context.json` and residual log. Assert the knowledge tree contains the ID/slot/Skill, the residual plot fields include `Ux` and `p`, and the latest-value table renders numerical values.

- [ ] **Step 2: Confirm RED**

Run the focused main-window tests; expect missing tabs/widgets.

- [ ] **Step 3: Implement the self-painted plot**

Use QWidget/QPainter from PySide6 Essentials, log10 y scale, deterministic field palette, axes/legend, and explicit no-data text. Do not add QtCharts/Addons or matplotlib.

- [ ] **Step 4: Add central Knowledge and Residual tabs**

Render immutable snapshot projections only. Show author/repair/attempt/slot/ID/title/source/hash and Skills; show latest initial/final/iterations/time values per field.

- [ ] **Step 5: Verify widget tests**

Expected: context, residual, no-data and malformed-context states pass offscreen.

---

### Task 6: Natural-Language and Advanced Task Workflow UI

**Files:**
- Modify: `src/foampilot/desktop/main_window.py`
- Modify: `src/foampilot/desktop/application.py`
- Modify: `tests/test_desktop_main_window.py`

**Interfaces:**
- Consumes: `DesktopWorkspace`, `DesktopJobController`, TaskDraft YAML and TaskSpec YAML.
- Produces user actions: `选择工程目录`, `生成草稿`, `应用回答并确认`, `验证并编译`, `开始求解`.

- [ ] **Step 1: Write failing button-state tests**

Assert no workspace disables generation; a nonblank request enables it; loaded draft populates fact/question rows; confirmed draft enables compile; nonblank TaskSpec enables solve; an active job disables all start actions.

- [ ] **Step 2: Write failing command-argv tests**

Inject a recording controller and assert exact arguments for:

```text
task draft --request-file ... --output ... --backend auto --json
task validate-draft ... --json
task compile ... --output ... --json
validate ... --json
solve ... --run-root UNIQUE_JOB_ROOT --backend auto --json
```

- [ ] **Step 3: Confirm RED**

Run the focused tests; expect missing task page/actions.

- [ ] **Step 4: Build the task page**

Add request editor, fact/question table with editable answer cells, TaskDraft and TaskSpec YAML advanced views, diagnostics, workspace label and deterministic action states.

- [ ] **Step 5: Implement the command state machine**

Draft exit `0` or `4` loads the produced draft; confirmation uses the pure helper; validate-draft success chains compile; validate TaskSpec success chains solve; every transition records purpose and expected output path. Unexpected output/missing file is `DESKTOP_PROCESS_FAILED`, not a CFD failure.

- [ ] **Step 6: Verify task workflow tests**

Expected: natural-language, incomplete-draft, direct TaskSpec and command-error paths pass.

---

### Task 7: Live Run Binding, Collection Selection, and Auto Refresh

**Files:**
- Modify: `src/foampilot/desktop/main_window.py`
- Modify: `tests/test_desktop_main_window.py`

**Interfaces:**
- Consumes: `run_discovered`, `RunCollectionError.children`, and immutable `RunSnapshot`.

- [ ] **Step 1: Write failing live-refresh tests**

Emit a discovered active run, append workflow/context/residual data, trigger the refresh timer, and assert the timeline/context/curve update without reopening through a file dialog.

- [ ] **Step 2: Write failing collection-selection test**

Mock `QInputDialog.getItem` for a batch root and assert the chosen concrete child is opened; cancel leaves the previous snapshot unchanged and shows guidance.

- [ ] **Step 3: Confirm RED**

Run focused main-window tests.

- [ ] **Step 4: Implement live binding**

On discovery, open the concrete run and start periodic refresh while the job is running. Stop periodic refresh after process exit and perform one final reload. Keep IDE job status separate from workflow/native labels.

- [ ] **Step 5: Implement batch child selection**

Catch only `RunCollectionError`, display sorted child names with `QInputDialog`, and call `open_run()` on the chosen resolved child. Other `RunOpenError` remains `RUN_OPEN_FAILED`.

- [ ] **Step 6: Verify all Desktop Qt tests**

Expected: existing Desktop A tests plus task/context/residual/live tests pass offscreen.

---

### Task 8: Documentation and Verification Gates

**Files:**
- Modify: `README.md`
- Modify: `docs/design/desktop-ide-design.md`
- Modify: `docs/desktop-ide.md`
- Create: `docs/reports/2026-08-11-desktop-b-live-solve.md`

- [ ] **Step 1: Update user documentation**

Document the exact click path, project directory layout, natural-language and TaskSpec entry, public-context boundary, residual interpretation, batch-run selection, optional Qt dependency and current non-goals.

- [ ] **Step 2: Run focused deterministic gates**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_desktop_workspace.py \
  tests/test_desktop_telemetry.py \
  tests/test_desktop_repository.py

QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_desktop_job_controller.py \
  tests/test_desktop_main_window.py \
  tests/test_desktop_cli.py
```

- [ ] **Step 3: Run full regression and static gates**

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests
git diff --check
```

- [ ] **Step 4: Run a real controller gate**

Use a complete, public TaskSpec and the actual `DesktopJobController`/CLI to create a unique job root. Record whether it reaches run creation, `CONTEXT_READY`, target solver start, normal completion and public validation separately. Do not replace a real failure with a synthetic success run.

- [ ] **Step 5: Capture an offscreen visual smoke image**

Render a representative 1440x900 window containing task/context/residual/run data and inspect the resulting image for clipping, unreadable layout or misleading empty states.

- [ ] **Step 6: Record evidence without committing**

Write exact test counts, versions, commands, run path, workflow/native/manifest states, context IDs, residual fields and known limits to the report. Leave all changes uncommitted.

---

## Plan Self-Review

- **Spec coverage:** 双输入、TaskDraft 确认、固定 argv solve、独占 run 绑定、Knowledge/Skill、residual、批目录修复、live refresh、文档和真实 gate 均有对应任务。
- **No alternate truth:** QProcess 只表示 IDE job；所有 CFD 结论仍来自 workflow/summary/manifest。
- **No placeholders:** 每个任务给出文件、接口、RED/GREEN 命令和预期结果；没有待定实现。
- **Type consistency:** `RunSnapshot` 统一携带 context/residual；`DesktopJobController.run_discovered(Path)` 是 live run 的唯一自动绑定入口；`DesktopWorkspace` 是任务输入的唯一写入边界。
- **Scope:** cancel、resume、VTK/ParaView 和 case revision 明确延期，不阻塞本次最小闭环。
