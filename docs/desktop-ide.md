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
   再为本次作业创建唯一 `runs/job-*` 目录并启动可分离的本机 worker；worker 内部仍调用规范
   `solve`。

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
- “残差监控”：读取 evidence 层写入的 `metrics.jsonl`，以 `log10` 曲线实时刷新，并列出
  各场最新的 initial/final residual 和时间步；Desktop 不再自行解析 solver stdout；
- “结果/验收”：读取规范 `DerivedMetrics`/`ResultReport`，展示观测范围、最新值、单位、
  AVAILABLE/PARTIAL/UNAVAILABLE、显式条件与 PASS/FAIL/INCOMPLETE/NOT_REQUESTED；界面不重算
  指标或 verdict；
- “产物”：最终 `RunSummary`、选中文件、结果/qualification 报告；
- 左侧：当前 run 内经过安全路径检查的 case、日志、报告和 workflow 文件；
- 底部：排序后的 workflow 时间线、OpenFOAM 日志以及 TaskBuilder/solve 进程输出；
- 右侧：IDE Job、当前阶段、Workflow、Native、Qualification、Manifest，以及配置来源、
  OpenFOAM root/version、requested isolation、actual backend、risk、sandbox probe 与 fallback
  warning。host 会明确标为“未隔离”，不能显示成与 bubblewrap 等价的成功。

残差下降只是数值证据之一。求解器出现正常 `End` 不自动证明充分收敛、网格无关或工程适用。

## 取消、关闭和重新连接

长任务不再由窗口进程持有。生成草稿或求解开始后，可以正常关闭 Desktop，worker 会继续写入
工程内的 `runs/job-*`。再次打开同一个工程时，Desktop 会优先发现最新的活动或异常中断 job，
校验 worker 进程身份并重新连接事件、日志和 run；没有待处理任务时回到最近的已固化 job。
关闭窗口不会隐式取消任务。

点击“取消任务”只会创建幂等的控制请求。worker 在安全点停止启动新阶段，并对自己拥有且身份
匹配的模型/OpenFOAM/MPI 进程组先发送 `SIGTERM`、超时后发送 `SIGKILL`；确认进程组退出并
固化 partial artifacts 后才显示 `CANCELLED`。`UNRESPONSIVE` 仅表示心跳过期，不能等同于
求解失败或取消完成。

取消能力存在一个明确的后端边界：默认 Codex CLI 后端和 OpenFOAM/MPI 都是本机受监督进程，
可以执行上述 TERM/KILL 升级；非流式 OpenAI-compatible HTTP 后端只能在请求前后检查取消，
标准库传输进行中不会主动中止远端请求，因此取消最多延迟到收到响应或达到该请求的有限
timeout。界面会显示这一差异，不能把“已提交取消请求”解释为所有后端都已立即停止。

若 worker 已消失但受监督的子进程仍存在，界面只允许“终止孤儿进程”，不会接管未知状态继续
workflow。worker 和子进程都消失后，“固化中断”会把现有 partial run 写成中立的
`INTERRUPTED` 终态并生成 manifest；完成固化后才能以可信 parent 执行“完整重跑”。

已固化 run 只有在 summary 明确声明可重试、manifest 与兼容性指纹有效，而且恢复点是模型生成
或模型修复时，才启用“恢复模型生成”或“恢复模型修复”。“完整重跑”始终创建新 run，并记录
parent manifest 与 `rerun_same_input`/`rerun_with_changes` lineage。两者都不会原地修改 parent。
界面持续显示“OpenFOAM continuation 当前不支持”，因此这些按钮不表示从任意时间目录断点续算。

## 状态解释

- `IDE Job`：持久化 worker 的运行、取消或终态，以及心跳健康度；
- `Workflow`：规范 Agent 工作流是完成、失败还是暂缓；
- `Native`：OpenFOAM 执行与显式 acceptance 达到的状态；
- `Qualification`：只有独立 qualification 证据时才成立，普通 run 显示
  `not available`；
- `Manifest`：固化 run 文件是否与 `artifact-manifest.json` 一致。

`RUN_COMPLETED` 或 `ResultReport.PASS` 不等于严格 qualification `PASS`。active run 的 manifest 显示
`pending` 属正常现象；run 固化后才进行完整性判定。

## 打开已有 Run

“打开 Run”既接受具体 `run-*`，也能识别包含多个 run 的 batch/job root。选择后者时，IDE
会列出直接子 run，必须选定一个具体 run，避免把批次目录误当成单次仿真。IDE 重启时会尝试
恢复上次成功打开的 run；路径不存在只给出非阻断提示。

## 安全与当前边界

- 命令由固定 Python executable、CLI 子命令白名单和参数数组组成，不使用 shell 字符串；
- 每次长任务使用唯一 job root、不可变 receipt、原子 status、heartbeat 和进程身份，只绑定其中
  发现的唯一 `run-*`；
- run 文件查看拒绝符号链接、绝对路径、`..`、未登记文件和超过显示上限的文件；
- QSettings 仅保存上次 run 和窗口布局，不写入 run；
- Desktop 只展示 run 中公开、规范的 `runtime-config.json`、
  `execution-risk-report.json`、`execution-policy.json` 等证据和模型实际收到的 Knowledge/Skill；
  不展示隐藏思维过程。audited host 与 bubblewrap 不具有相同安全性；
- 活跃 run 的时间线通过 byte cursor 增量读取；状态、残差、问题、失败报告和可信链接统一由
  `WorkflowProjection` 提供，文件树扫描节流，manifest 验证缓存，重型 projection 在 Qt
  后台线程执行。刷新失败保留上一次画面并显示
  `DESKTOP_REFRESH_DEGRADED`；

Desktop 与 CLI 的 `foampilot progress RUN_DIR --json` 使用同一 `WorkflowProjection`。求解
日志只由 evidence 层解析一次并形成 `RunFacts`；失败界面展示 `FailureReport` 中的观察、确认
原因、hypothesis、repair 原因和建议动作，不展示隐藏思维过程。
- 当前 Desktop 尚未提供人工 repair、case revision、OpenFOAM 时间目录 continuation、三维
  VTK/PyVista 视图、ParaView 启动、远程 HPC、多用户和权限管理。

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
