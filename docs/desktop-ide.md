# FoamPilot Desktop IDE：交互式求解工作台

FoamPilot Desktop IDE 是面向 Linux CFD 工作站的可选 PySide6 界面。它可以从自然语言任务
或完整 `TaskSpec` 启动规范 `foampilot solve`，并在同一窗口实时显示工作流事件、模型实际
收到的公开 Knowledge/Skill 引用、OpenFOAM 残差、case 文件、日志、验证报告和 artifact
manifest。桌面层不建立第二套 Agent 或 CFD 状态机；求解仍由 `NativeAgent.solve()` 完成。

## 安装与启动

在 FoamPilot 仓库或源码包中安装桌面可选依赖：

```bash
python -m pip install -e '.[desktop]'
foampilot desktop
```

开发和 Qt 测试使用：

```bash
python -m pip install -e '.[desktop-test]'
```

也可以启动时直接检查一个已有的规范 run：

```bash
foampilot desktop --open-run /path/to/run-...
```

PySide6 不属于 FoamPilot 核心依赖。未安装 desktop extra 时，CLI 与 Python API 仍可使用。

## 第一次求解

1. 点击“工程目录”，选择或新建一个专用于本次工作的目录。IDE 会在其中维护
   `requests/`、`drafts/`、`tasks/` 和 `runs/`，不会修改仓库源码。
2. 在“自然语言任务描述”中写清几何及单位、介质物性、边界/初始条件、稳态或瞬态、结束
   条件和期望输出，然后点击“1. 生成 TaskDraft”。
3. 检查事实、可见假设和待确认问题。问题行可以直接填写；点击“2. 应用回答并确认”，再
   点击“3. 验证并编译 TaskSpec”。没有完成高影响确认时，IDE 不允许进入求解。
4. 检查编译后的 `TaskSpec`，点击“4. 开始规范求解”。IDE 会先执行确定性 `validate`，
   再为本次作业创建唯一 `runs/job-*` 目录并调用规范 `solve`。

TaskDraft 和 TaskSpec YAML 只作为高级视图保留；正常流程不要求手工编辑 YAML。已有完整
TaskSpec 的用户也可以直接粘贴到 TaskSpec 页并开始求解。TaskSpec 中的公开资产路径以所选
工程目录为根，因此资产应放在该目录内。

“环境检查”会依次执行 OpenFOAM preflight 和模型后端 doctor。建议第一次求解前先运行。
从命令行启动 Desktop 时可以传入与 `solve` 相同的显式 Runtime flags，例如：

```bash
foampilot desktop \
  --runtime-config ~/.config/foampilot/runtime.toml \
  --execution-isolation sandbox_preferred
```

Desktop 只把这些参数原样转发给规范 `preflight`/`solve`，不重新实现 resolver，也不会降低
isolation；TaskDraft 不接收 Runtime 参数。

## 运行中看什么

- “任务”：自然语言、澄清表、TaskDraft/TaskSpec 和阻断诊断；
- “知识上下文”：capability routing，以及模型实际收到的 Knowledge ID、标题、类型、来源、
  内容哈希和 Skill；只显示固化的公开上下文，不展示隐藏思维过程；
- “残差监控”：从当前 solver stdout 提取各场 initial residual，以 `log10` 曲线实时刷新，并
  列出各场最新的 initial/final residual、线性迭代次数和时间步；
- “产物”：最终 `RunSummary`、选中文件、公开验证/qualification 报告；
- 左侧：当前 run 内经过安全路径检查的 case、日志、报告和 workflow 文件；
- 底部：排序后的 workflow 时间线、OpenFOAM 日志以及 TaskBuilder/solve 进程输出；
- 右侧：IDE Job、当前阶段、Workflow、Native、Qualification、Manifest，以及配置来源、
  OpenFOAM root/version、requested isolation、actual backend、risk、sandbox probe 与 fallback
  warning。host 会明确标为“未隔离”，不能显示成与 bubblewrap 等价的成功。

残差下降只是数值证据之一。求解器出现正常 `End` 不自动证明充分收敛、网格无关或工程适用。

## 状态解释

- `IDE Job`：桌面启动的子进程是否仍在运行及其退出码；
- `Workflow`：规范 Agent 工作流是完成、失败还是暂缓；
- `Native`：OpenFOAM/公开验证达到的状态；
- `Qualification`：只有独立 qualification 证据时才成立，普通 run 显示
  `not available`；
- `Manifest`：固化 run 文件是否与 `artifact-manifest.json` 一致。

`PUBLIC_VALIDATION_PASS` 不等于严格 qualification `PASS`。active run 的 manifest 显示
`pending` 属正常现象；run 固化后才进行完整性判定。

## 打开已有 Run

“打开 Run”既接受具体 `run-*`，也能识别包含多个 run 的 batch/job root。选择后者时，IDE
会列出直接子 run，必须选定一个具体 run，避免把批次目录误当成单次仿真。IDE 重启时会尝试
恢复上次成功打开的 run；路径不存在只给出非阻断提示。

## 安全与当前边界

- 命令由固定 Python executable、CLI 子命令白名单和参数数组组成，不使用 shell 字符串；
- 每次求解使用唯一 job root，并只绑定其中发现的唯一 `run-*`；
- run 文件查看拒绝符号链接、绝对路径、`..`、未登记文件和超过显示上限的文件；
- QSettings 仅保存上次 run 和窗口布局，不写入 run；
- Desktop 只展示 run 中公开、规范的 `runtime-config.json`、
  `execution-risk-report.json`、`execution-policy.json` 等证据和模型实际收到的 Knowledge/Skill；
  不展示隐藏思维过程。audited host 与 bubblewrap 不具有相同安全性；
- 规范作业运行期间关闭窗口会被阻止，以免 Qt 销毁子进程；v1 尚无取消按钮；
- 当前没有 resume、人工 repair、case revision、三维 VTK/PyVista 视图、ParaView 启动、远程
  HPC、多用户和权限管理。

## Linux xcb 启动故障

如果 Qt 报告 `xcb-cursor0 or libxcb-cursor0 is needed`，说明系统缺少 Qt xcb 平台插件的
运行库。在 Ubuntu 上安装后重新启动：

```bash
sudo apt install libxcb-cursor0
```

这不是 FoamPilot 或 OpenFOAM 求解故障。无图形桌面的测试环境可使用
`QT_QPA_PLATFORM=offscreen`，日常桌面运行不要设置该变量。

## 开发验证

聚焦桌面测试：

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  python -m pytest -q -p no:cacheprovider tests/test_desktop_*.py
```

完整测试仍应覆盖核心包，确保 Qt 可选层没有改变 CLI、Agent 或 qualification 语义。
