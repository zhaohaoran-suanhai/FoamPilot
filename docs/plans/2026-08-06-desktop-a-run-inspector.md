# FoamPilot Desktop A Run Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan inline, task by task. Do not use subagents. Do not commit unless the user explicitly requests it.

**Goal:** 实现一个可安装、可测试的 PySide6 只读桌面工作台，用于打开现有 FoamPilot run，并查看状态、timeline、case/log 文件、公开验证和 artifact manifest 结果。

**Architecture:** Desktop A 只在现有 artifacts 之上增加一层只读 application/UI 适配。纯 Python `RunRepository` 负责路径约束、artifact 解析和 view model；PySide6 `QMainWindow` 只消费这些 view model。界面不直接调用 `NativeAgent`、Runner 或 OpenFOAM，也不修改 run 产物。

**Tech Stack:** Python 3.12、Pydantic v2、PySide6/Qt Widgets、pytest、pytest-qt、现有 `ArtifactStore`、`WorkflowEvent` 和 `RunSummary`。

## Global Constraints

- 当前验证平台保持 Linux + Foundation OpenFOAM v10。
- `desktop -> application services -> foampilot core`；core 不得反向依赖 Qt。
- PySide6 必须是可选依赖；未安装 desktop extras 时，现有 CLI/Python API 必须继续可用。
- Desktop A 只读取用户显式打开的 run，不启动 solve/resume，不编辑 case，不实现 VTK/PyVistaQt。
- 只有 `ArtifactStore`、`WorkflowStore` 与已固化文件是 run 真相源；Qt 不建立第二套状态。
- 不使用 shell string，不读取 run root 外路径，不跟随符号链接。
- `PUBLIC_VALIDATION_PASS`、qualification PASS、manifest verified 和工程适用性必须分开显示。
- 实施期间不使用子代理，不自动提交或推送 Git。

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/foampilot/desktop/__init__.py` | Desktop 包边界，不导入 Qt |
| `src/foampilot/desktop/repository.py` | 安全打开 run、解析 summary/events/manifest/file tree |
| `src/foampilot/desktop/viewmodels.py` | 与 Qt 无关的只读 view model |
| `src/foampilot/desktop/main_window.py` | `QMainWindow`、Dock、选择与展示逻辑 |
| `src/foampilot/desktop/application.py` | `QApplication`、QSettings 和桌面启动入口 |
| `src/foampilot/cli/main.py` | 增加惰性 `foampilot desktop` 入口 |
| `pyproject.toml` | `desktop` 和 `desktop-test` 可选依赖 |
| `tests/test_desktop_repository.py` | 纯 Python run 解析、安全与不完整 run 测试 |
| `tests/test_desktop_cli.py` | 桌面依赖缺失和入口转发测试 |
| `tests/test_desktop_main_window.py` | pytest-qt offscreen 窗口与交互测试 |
| `docs/desktop-ide.md` | Desktop A 安装、启动、能力边界 |
| `docs/reports/2026-08-06-desktop-a-run-inspector.md` | 确定性、Qt 与真实 run gate 证据 |

---

### Task 1: Pure-Python Run Repository and View Models

**Files:**
- Create: `src/foampilot/desktop/__init__.py`
- Create: `src/foampilot/desktop/viewmodels.py`
- Create: `src/foampilot/desktop/repository.py`
- Create: `tests/test_desktop_repository.py`

**Interfaces:**
- Consumes: `ArtifactStore.read_summary(run_dir)`, `ArtifactStore(run_dir.parent).verify(run_dir)`, `WorkflowEvent.model_validate_json(line)`.
- Produces: `RunRepository.open(run_dir: Path) -> RunSnapshot`, `RunRepository.read_text(snapshot, relative_path, max_bytes=2_097_152) -> str`.

- [x] **Step 1: Write failing tests for a finalized run**

  Build a temporary run containing `summary.json`, `workflow-events.jsonl`, a small case log and a finalized `artifact-manifest.json`. Assert:

  ```python
  snapshot = RunRepository().open(run_dir)
  assert snapshot.summary is not None
  assert snapshot.summary.status == "PUBLIC_VALIDATION_PASS"
  assert snapshot.manifest_state == "verified"
  assert [item.sequence for item in snapshot.timeline] == [1, 2]
  assert "attempt-01/case/.foampilot/logs/solve.stdout.log" in {
      item.path for item in snapshot.files
  }
  ```

- [x] **Step 2: Run the focused test and confirm RED**

  Run:

  ```bash
  /home/edwin/feal-venv-py312/bin/python -m pytest -q tests/test_desktop_repository.py
  ```

  Expected: collection fails because `foampilot.desktop.repository` does not exist.

- [x] **Step 3: Define Qt-independent view models**

  Implement strict frozen models with these fields:

  ```python
  class RunFileView(BaseModel):
      model_config = ConfigDict(frozen=True)
      path: str
      bytes: int
      category: Literal["case", "log", "report", "workflow", "other"]

  class TimelineView(BaseModel):
      model_config = ConfigDict(frozen=True)
      sequence: int
      stage: str
      state: str
      attempt: int | None
      step_id: str | None
      detail: str

  class RunSnapshot(BaseModel):
      model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
      run_dir: Path
      summary: RunSummary | None
      timeline: tuple[TimelineView, ...]
      files: tuple[RunFileView, ...]
      manifest_state: Literal["verified", "invalid", "pending"]
      manifest_issues: tuple[str, ...]
      warnings: tuple[str, ...]
  ```

- [x] **Step 4: Implement safe repository loading**

  `RunRepository.open()` must:

  1. resolve an explicitly supplied directory and require it to be a real directory;
  2. reject the run directory itself when it is a symbolic link;
  3. load `summary.json` when present, otherwise return `summary=None` and `manifest_state="pending"`;
  4. parse valid workflow JSONL lines in sequence order and record malformed lines in `warnings`;
  5. use `ArtifactStore.verify()` only when the manifest exists;
  6. build the file list without following symbolic links;
  7. classify `.foampilot/logs/*`, `attempt-*/case/*`, JSON reports and workflow files deterministically.

  `read_text()` must reject absolute paths, `..`, symbolic links, paths outside `snapshot.run_dir`, unregistered files and files above the byte limit. Decode UTF-8 with replacement only after all checks pass.

- [x] **Step 5: Add security and in-progress-run tests**

  Cover:

  ```python
  with pytest.raises(RunOpenError, match="outside opened run"):
      repository.read_text(snapshot, "../secret")

  assert repository.open(active_run).summary is None
  assert repository.open(active_run).manifest_state == "pending"
  ```

  Also test a symlink inside the run, an oversized text file, malformed event JSON and a manifest hash mismatch.

- [x] **Step 6: Run repository tests**

  Run the focused test file and then:

  ```bash
  /home/edwin/feal-venv-py312/bin/python -m pytest -q tests/test_artifact_store.py tests/test_workflow_store.py tests/test_desktop_repository.py
  ```

  Expected: all selected tests pass and existing artifact behavior is unchanged.

---

### Task 2: Optional Desktop Dependency and CLI Entry Point

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/foampilot/cli/main.py`
- Create: `src/foampilot/desktop/application.py`
- Create: `tests/test_desktop_cli.py`

**Interfaces:**
- Consumes: `foampilot.cli.main(argv)` and the later `FoamPilotMainWindow` class.
- Produces: `launch(run_dir: Path | None = None) -> int` and `foampilot desktop [--open-run PATH]`.

- [x] **Step 1: Write failing CLI tests**

  Test both lazy import and argument forwarding:

  ```python
  def test_desktop_command_forwards_explicit_run(monkeypatch, tmp_path):
      seen = []
      monkeypatch.setattr(cli, "_desktop_launcher", lambda path: seen.append(path) or 0)
      assert cli.main(["desktop", "--open-run", str(tmp_path)]) == 0
      assert seen == [tmp_path]

  def test_desktop_command_reports_missing_optional_dependency(monkeypatch, capsys):
      monkeypatch.setattr(cli, "_desktop_launcher", lambda path: (_ for _ in ()).throw(
          DesktopDependencyError("PySide6 is not installed")
      ))
      assert cli.main(["desktop"]) == 3
      assert "请安装 foampilot[desktop]" in capsys.readouterr().err
  ```

- [x] **Step 2: Confirm RED**

  Run `tests/test_desktop_cli.py`; expect failure because the command and launcher do not exist.

- [x] **Step 3: Add optional dependencies**

  Add:

  ```toml
  [project.optional-dependencies]
  test = ["pytest>=8"]
  desktop = ["PySide6-Essentials>=6.7,<7"]
  desktop-test = ["PySide6-Essentials>=6.7,<7", "pytest-qt>=4.4,<5"]
  ```

  Keep PySide6 out of `[project].dependencies`; do not add PyVistaQt in Desktop A.

- [x] **Step 4: Add a lazy CLI command**

  Register:

  ```python
  desktop = subparsers.add_parser("desktop")
  desktop.add_argument("--open-run", type=Path)
  ```

  The handler imports `foampilot.desktop.application.launch` only after `desktop` is selected. Convert a missing PySide6 import into exit code 3 with a Chinese install instruction; do not catch unrelated import errors.

- [x] **Step 5: Implement application startup**

  `launch()` must reuse an existing `QApplication` or create one, set organization/application names to `FoamPilot`, construct `FoamPilotMainWindow`, optionally call `open_run()`, show it and execute the event loop. No Qt symbol may be imported by `foampilot.desktop.__init__`.

- [x] **Step 6: Verify CLI isolation**

  Run:

  ```bash
  /home/edwin/feal-venv-py312/bin/python -m pytest -q tests/test_desktop_cli.py tests/test_cli.py
  /home/edwin/feal-venv-py312/bin/foampilot --help
  ```

  Expected: CLI tests pass; help includes `desktop`; all non-desktop commands import without requiring PySide6.

---

### Task 3: Read-Only Qt Main Window

**Files:**
- Create: `src/foampilot/desktop/main_window.py`
- Create: `tests/test_desktop_main_window.py`

**Interfaces:**
- Consumes: `RunRepository`, `RunSnapshot`, `RunFileView`, `TimelineView`.
- Produces: `FoamPilotMainWindow.open_run(run_dir: Path) -> None`, `FoamPilotMainWindow.current_snapshot: RunSnapshot | None`.

- [x] **Step 1: Install the desktop test extra in the existing virtual environment**

  Run:

  ```bash
  /home/edwin/feal-venv-py312/bin/python -m pip install -e '.[desktop-test]'
  ```

  Verify imports:

  ```bash
  /home/edwin/feal-venv-py312/bin/python -c "import PySide6, pytestqt; print(PySide6.__version__)"
  ```

- [x] **Step 2: Write failing offscreen window tests**

  With `QT_QPA_PLATFORM=offscreen`, assert that opening a fake verified run produces:

  ```python
  window.open_run(run_dir)
  assert window.current_snapshot is not None
  assert window.windowTitle().startswith("FoamPilot")
  assert window.status_label.text() == "PUBLIC_VALIDATION_PASS"
  assert window.manifest_label.text() == "Manifest: verified"
  assert window.timeline_tree.topLevelItemCount() == 2
  ```

  Select a case/log item and assert its safe text appears in the central viewer. Assert all editors are read-only.

- [x] **Step 3: Build the minimal dock layout**

  Construct one `QMainWindow` with:

  - toolbar actions: `打开 Run` and `刷新` only;
  - left `QDockWidget`: run/category/file tree;
  - central `QTabWidget`: overview, file viewer, validation/report JSON;
  - right `QDockWidget`: task ID, workflow state, native status, attempt count, primary failure and terminal blocker;
  - bottom `QDockWidget`: ordered workflow timeline and selected log text.

  Use `QPlainTextEdit.setReadOnly(True)` for all artifact text. Do not add Run/Resume/Cancel/ParaView actions in Desktop A.

- [x] **Step 4: Bind snapshot data without duplicating state**

  `open_run()` calls `RunRepository.open()` exactly once and renders from the returned immutable snapshot. `刷新` reloads from disk and replaces the entire snapshot. UI labels must render separately:

  ```text
  Workflow: COMPLETED
  Native: PUBLIC_VALIDATION_PASS
  Qualification: not available
  Manifest: verified
  ```

  Never infer success from process state or the presence of a time directory.

- [x] **Step 5: Add deterministic error presentation**

  Map `RunOpenError` to a modal error containing stable code `RUN_OPEN_FAILED`, a Chinese message and the rejected path. Manifest problems remain visible as `Manifest: invalid`; they must not prevent the user from reading safe existing artifacts.

- [x] **Step 6: Run Qt tests offscreen**

  Run:

  ```bash
  QT_QPA_PLATFORM=offscreen /home/edwin/feal-venv-py312/bin/python -m pytest -q tests/test_desktop_main_window.py
  ```

  Expected: window construction, verified/invalid/pending states, file selection and refresh tests pass without displaying a real window.

---

### Task 4: Restart Recovery, Documentation, and Real-Run Gate

**Files:**
- Modify: `src/foampilot/desktop/application.py`
- Modify: `src/foampilot/desktop/main_window.py`
- Modify: `tests/test_desktop_main_window.py`
- Create: `docs/desktop-ide.md`
- Create: `docs/reports/2026-08-06-desktop-a-run-inspector.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `QSettings`, `FoamPilotMainWindow.open_run()` and the existing successful run artifacts.
- Produces: deterministic recent-run restoration and Desktop A user documentation.

- [x] **Step 1: Write failing settings/recovery tests**

  Isolate `QSettings` to a temporary INI file. Test that a successfully opened run stores its absolute path locally, a new window restores it when no `--open-run` is supplied, and a missing path is ignored with a non-fatal warning. Confirm settings files are outside the opened run and absent from its artifact manifest.

- [x] **Step 2: Implement local recovery**

  Store only:

  ```text
  desktop/last_run
  desktop/window_geometry
  desktop/window_state
  ```

  Restore the last run only when the caller did not supply `--open-run`. Do not copy QSettings into the repository, run directory, report export or case bundle.

- [x] **Step 3: Write Chinese user documentation**

  Document:

  ```bash
  python -m pip install -e '.[desktop]'
  foampilot desktop
  foampilot desktop --open-run /path/to/run-...
  ```

  State that Desktop A is read-only, does not start solve/resume, does not edit case files, and does not replace ParaView. Link the desktop guide from `README.md`.

- [x] **Step 4: Run a real-run offscreen gate**

  Open:

  ```text
  /tmp/foampilot-pimple-volume-fraction-fix-20260806-v3/
    run-20260806T145627612349Z-e0fba6fc
  ```

  Assert through a small test/smoke command that the IDE reports:

  ```text
  task_id = pimple-blocked-channel
  workflow_state = COMPLETED
  native_status = PUBLIC_VALIDATION_PASS
  manifest_state = verified
  timeline contains solve-pimplefoam
  ```

  If the `/tmp` run no longer exists, create a new real FoamPilot run through the canonical CLI; do not substitute a target tutorial or manually assembled success artifact for this gate.

- [x] **Step 5: Run full verification**

  Run:

  ```bash
  QT_QPA_PLATFORM=offscreen /home/edwin/feal-venv-py312/bin/python -m pytest -q
  git diff --check
  ```

  Expected: all deterministic tests pass, existing CLI tests remain green, and no whitespace errors exist.

- [x] **Step 6: Record evidence without committing**

  Write the exact test count, Qt/PySide versions, real run path, rendered states, manifest verdict and known Desktop A limitations to `docs/reports/2026-08-06-desktop-a-run-inspector.md`. Leave the worktree uncommitted for user review.

---

## Plan Self-Review

- **Spec coverage:** Desktop A requirements are covered: optional Qt dependency, read-only run opening, timeline/log/case/report display, manifest verification, restart restoration, path confinement, missing-dependency behavior and a real-run gate.
- **Deferred by design:** TaskBuilder editing, `QProcess` solve/resume, geometry/mesh visualization, ParaView launch, cancellation and `user_revision` remain Desktop B/C/D work.
- **No alternate truth source:** Every displayed business state comes from `RunSummary`, `WorkflowEvent` or artifact verification.
- **No placeholders:** Every task names concrete files, interfaces, tests, commands and expected outcomes.
- **Type consistency:** `RunRepository.open() -> RunSnapshot` and `FoamPilotMainWindow.open_run()` remain the only run-loading path throughout all tasks.
