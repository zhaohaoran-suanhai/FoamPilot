# FoamPilot Desktop A Run Inspector 实施与验证报告

日期：2026-08-07
状态：Desktop A 已完成实现与本机 gate；Desktop B/C/D 未实施

计划：[Desktop A Run Inspector 实施计划](../plans/2026-08-06-desktop-a-run-inspector.md)
使用说明：[Desktop A：只读 Run Inspector](../desktop-ide.md)

## 1. 结论

FoamPilot 已增加一个可选、只读的 PySide6/Qt Widgets 工作台：

```text
显式 run 路径或本机最近 run
  -> RunRepository 安全读取
  -> 不可变 RunSnapshot
  -> QMainWindow / Dock / 只读文本视图
```

Desktop A 可以显示 run 文件、workflow 时间线、case、OpenFOAM 日志、公开验证/报告 JSON、
RunSummary 和 artifact manifest 状态。它没有调用 `NativeAgent`、Runner 或 OpenFOAM，不会修改
run，也没有建立第二套工作流状态。

## 2. 实现范围

- `foampilot.desktop` 包的顶层导入不加载 Qt；
- `RunRepository` 拒绝 run 外路径、绝对路径、`..`、符号链接、未登记文件和超限文件；
- `RunSnapshot`、文件项和时间线项是 Qt 无关的冻结 Pydantic view model；
- `foampilot desktop [--open-run PATH]` 仅在显式选择桌面命令后加载 PySide6；
- 主窗口提供“打开 Run”和“刷新”、分类文件树、概览、文件、报告、状态和时间线区域；
- 全部 artifact 文本控件只读；
- verified、invalid 和 pending manifest 分开呈现，invalid 不阻止读取安全现有文件；
- QSettings 只保存最近 run、窗口 geometry 和 Dock state；
- 最近 run 已删除时只显示非模态提示；
- 已有 `QApplication` 时窗口保持存活，关闭后释放；普通 CLI 入口创建并运行自己的事件循环。

## 3. 确定性验证

最终全量命令：

```bash
QT_QPA_PLATFORM=offscreen \
  /home/edwin/feal-venv-py312/bin/python \
  -m pytest -q -p no:cacheprovider
```

结果：

```text
593 passed, 8 skipped in 20.55s
```

Desktop 聚焦组覆盖：

- finalized、active、manifest mismatch 和 malformed workflow run；
- run、控制产物和内部文件 symlink；
- 路径逃逸、未登记文件和显示大小限制；
- CLI 参数转发、可选依赖错误与核心包 Qt 隔离；
- verified/invalid/pending UI、只读文件/日志、刷新和错误对话框；
- QSettings 恢复、缺失最近 run 和允许键集合；
- 已有 `QApplication` 的窗口生命周期；
- opt-in 真实 run gate。

测试环境：

```text
Python: 3.12
PySide6: 6.11.1
pytest-qt: 4.5.0
Qt platform for automation: offscreen
```

## 4. 真实 Run Gate

使用既有规范 FoamPilot run：

```text
/tmp/foampilot-pimple-volume-fraction-fix-20260806-v3/
  run-20260806T145627612349Z-e0fba6fc
```

显式 gate：

```bash
QT_QPA_PLATFORM=offscreen \
FOAMPILOT_DESKTOP_REAL_RUN=/tmp/foampilot-pimple-volume-fraction-fix-20260806-v3/run-20260806T145627612349Z-e0fba6fc \
  /home/edwin/feal-venv-py312/bin/python -m pytest -q \
  tests/test_desktop_main_window.py::test_real_run_inspector_gate
```

结果为 `1 passed in 0.13s`。界面从真实产物读取并显示：

```text
task_id = pimple-blocked-channel
workflow_state = COMPLETED
native_status = PUBLIC_VALIDATION_PASS
manifest_state = verified
timeline contains solve-pimplefoam
```

视觉烟测以 1280×820 offscreen 窗口打开同一 run，读取 212 个 artifact 和 19 条 workflow
事件；状态栏、右侧状态区、分类文件树、报告区和底部 timeline/log 区均完成渲染。该检查没有
修改真实 run。

## 5. Wheel Gate

构建命令：

```bash
/home/edwin/feal-venv-py312/bin/python -m pip wheel . \
  --no-deps --no-build-isolation --wheel-dir WHEEL_DIR
```

产物：

```text
foampilot-0.1.0-py3-none-any.whl
SHA256 79e3a6b51c2188178e53932ebc8db6482c854cdf6d0038cceaf7d00832d030fa
```

wheel 已确认包含 `desktop/__init__.py`、`application.py`、`main_window.py`、`repository.py` 和
`viewmodels.py`，并声明 `desktop`、`desktop-test` 两组可选依赖。将 wheel 安装到独立临时目标后，
同一真实 run 显示 `PUBLIC_VALIDATION_PASS`、`Manifest: verified` 和 19 条时间线事件。

## 6. 证据边界与已知限制

本报告证明 Desktop A 的只读安装、启动、run 解析、Qt 展示、恢复和真实产物读取可用。它不证明：

- Desktop B 的 TaskBuilder/TaskSpec 编辑与求解启动；
- Desktop C 的几何、网格、场结果可视化或 ParaView 集成；
- Desktop D 的 case 编辑、人工 revision 和导出；
- Windows/macOS、远程服务器、HPC、多用户或产品级部署；
- `PUBLIC_VALIDATION_PASS` 等价于 qualification、工程精度或生产适用性。

Desktop A 仍然只消费 FoamPilot core 的不可变产物；`NativeAgent.solve()` 保持唯一求解主链。
