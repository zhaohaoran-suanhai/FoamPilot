# FoamPilot Interactive Desktop IDE v1 设计

状态：已确认，进入实施。本文把已经完成的 Desktop A 只读 Run Inspector 扩展为可从界面创建任务、启动规范求解并实时观察公开 Agent 上下文与 OpenFOAM residual 的 Desktop B 最小闭环。

## 1. 目标与验收场景

首个可用闭环是：

```text
选择一个显式工程目录
-> 输入自然语言，或粘贴已有 TaskSpec YAML
-> 查看并确认 TaskDraft 的事实、假设和问题
-> 编译/验证规范 TaskSpec
-> 由 QProcess 启动 foampilot solve
-> 自动绑定本次独占 run
-> 实时查看 workflow、公开知识引用、Skills、日志和 residual
-> 求解结束后继续使用 Desktop A 的文件、报告和 manifest 检查
```

验收时，用户不需要先在终端运行 `foampilot solve`，也不需要为了完整的自然语言请求手写 TaskSpec YAML。高级用户仍可直接粘贴 TaskSpec YAML。

## 2. 边界

- `NativeAgent.solve()` 保持唯一求解主链；桌面端只通过固定 program/args 的 QProcess 调用现有 CLI。
- TaskBuilder 继续经过 `TaskDraft -> DraftReview -> TaskCompiler -> TaskSpec`，不得从自然语言直接运行 OpenFOAM。
- “知识引用”只显示模型实际收到的公开结构化上下文：slot、Knowledge ID、标题、类型、source locator/hash 和 Skill 名称；不显示或推测隐藏 chain-of-thought。
- residual 曲线来自正在增长的 OpenFOAM stdout log；解析不到时显示“数据不可用/等待数据”，不能显示零曲线。
- Desktop A 的 artifact、workflow 和 manifest 仍是业务状态真相源。QProcess 的运行状态只标为 IDE job 状态，不能替代 solver/public validation 结论。
- 本阶段不实现取消、strict resume、人工 case revision、三维场显示或 ParaView 内嵌。

## 3. 架构

```text
FoamPilotMainWindow
  |-- DesktopWorkspace       显式工程目录和输入文件版本
  |-- DesktopJobController   固定 argv 的异步 CLI/QProcess
  |-- RunRepository          公开 artifact 投影
  |-- Telemetry parser       OpenFOAM log -> residual samples
  `-- Qt views               task/context/residual/artifact/log

DesktopJobController
  -> python -m foampilot.cli.main task draft/validate-draft/compile
  -> python -m foampilot.cli.main validate
  -> python -m foampilot.cli.main solve
  -> canonical NativeAgent.solve()
```

每次求解使用独占 job root：

```text
PROJECT/runs/job-<timestamp>-<id>/run-<timestamp>-<id>/
```

CLI 启动后，`ArtifactStore.create_run()` 会立即在该独占 job root 下创建唯一 `run-*`。桌面端只在这个目录内发现 run，因此不需要从全局批目录猜测进程属于哪个 run，也不需要修改 core 的 run 命名或状态机。

## 4. 工程目录与任务入口

`DesktopWorkspace` 只接受用户显式选择的真实目录，拒绝符号链接，并在其中维护：

```text
requests/
drafts/
tasks/
runs/
```

写入使用同目录临时文件加 `os.replace()`，每次 draft、TaskSpec 和 job root 使用新版本名，不覆盖既有求解输入。

任务页提供两条入口：

1. 自然语言：保存 request，异步调用 `task draft`，显示 TaskDraft；用户在表格中确认模型推断、填写 blocking/confirmable 问题，再调用确定性 validate/compile。
2. 高级 TaskSpec：用户直接粘贴 YAML，桌面端先调用 `foampilot validate`，通过后才允许 solve。

TaskDraft YAML 和 TaskSpec YAML 保留为可检查的高级视图，但普通闭环使用事实/问题表格和按钮完成。

## 5. Job 与实时刷新

`DesktopJobController`：

- program 固定为当前 `sys.executable`；prefix 固定为 `-m foampilot.cli.main`；
- arguments 使用 QProcess 的 program/argument 分离接口，不构造 shell string；
- 转发 stdout/stderr 到“任务/进程日志”；
- 只允许已登记的 FoamPilot 子命令；
- solve 时轮询独占 job root，发现唯一 `run-*` 后发出 `run_discovered`；
- 不根据 exit code 宣称 CFD 成功，退出后仍从 summary/workflow/manifest 渲染结论。

主窗口在 job 运行期间定时替换完整 `RunSnapshot`。刷新失败保留上一个有效 snapshot，并把 IDE 读取错误与 CFD primary failure 分开显示。

## 6. 公开知识与 Skill 视图

`RunRepository` 读取：

- 根目录 `agent-context.json`；
- 根目录或 attempt 下的 `repair-agent-context.json`；
- `capability-profile.json`；
- 对应 workflow event。

Knowledge ID 通过当前正式 corpus 补充标题、类型和 source locator；artifact 中的 source SHA256 始终作为本次运行证据显示。如果当前安装找不到历史 ID，仍显示 ID/hash 并标记 metadata unavailable。

界面按 author/repair、attempt、slot 分组，Skill 单独列出。它回答“这次生成或修复引用了什么公开知识”，不回答“模型内部如何逐步思考”。

## 7. residual 数据契约与图表

新增 Qt 无关模型：

```text
ResidualSample
  attempt
  source_log
  sequence
  simulation_time | null
  iteration | null
  field
  initial_residual
  final_residual
  solver_iterations
```

解析器按日志顺序关联最近的 `Time =` 或 `Iteration =`，支持 Foundation v10 常见：

```text
Solving for Ux, Initial residual = ..., Final residual = ..., No Iterations ...
```

曲线默认绘制 `initial_residual` 的 `log10`，按 field 分色；旁边的表格显示每个 field 的最新 initial/final、solver iterations 和时间/迭代。数据为空、超限或日志损坏时保留原始日志入口并明确提示。

首版使用 Qt Essentials 可用的自绘 QWidget，不增加 PySide6 Addons、matplotlib、PyVista 或 VTK 强依赖。

## 8. 主窗口信息架构

```text
Toolbar: 工程目录 | 环境检查 | 生成草稿 | 确认并编译 | 开始求解 | 打开 Run | 刷新

Central tabs
  任务        request、事实/问题、TaskDraft、TaskSpec、诊断
  知识上下文  capability、Knowledge slots、Skills
  残差监控    residual plot、latest values
  产物        overview、文件、验证/报告

Left dock: 当前工程/run 文件树
Right dock: IDE job、workflow、native、manifest、failure
Bottom dock: workflow timeline、OpenFOAM log、CLI stdout/stderr
```

按钮按状态启用：没有工程不能生成；没有可验证 TaskSpec 不能求解；已有 job 运行时不能启动第二个 job。Desktop v1 不提供 kill/cancel 按钮。

## 9. 批目录错误修复

`RunRepository.open()` 必须区分：

- 具体 active/finalized `run-*`；
- 含多个 `run-*` 子目录的 collection/batch root；
- 普通错误目录。

打开 collection 时抛出带 child list 的专用错误。GUI 提供具体 child run 选择；不能再把 batch root 渲染成 `Task: not available` 和重复文件树。

## 10. 错误与安全

- 稳定区分 `DESKTOP_WORKSPACE_INVALID`、`DESKTOP_JOB_BUSY`、`DESKTOP_PROCESS_FAILED`、`RUN_COLLECTION_SELECTED`、`RUN_OPEN_FAILED` 与 core 返回的 backend/environment/case/solver code。
- 不读取工程/run root 外文件，不跟随符号链接，不接受 shell、重定向或命令替换。
- 不在 UI 或 job receipt 中保存 secret；backend 凭据继续只从继承环境读取。
- active run 没有 manifest 时显示 pending；finalized manifest invalid 时仍可查看安全文件，但不得显示 verified。

## 11. 测试与完成证据

按 TDD 分层验证：

1. 纯 Python：workspace 路径/原子版本、draft confirmation、batch root、context projection、residual time association。
2. Qt：QProcess fixed argv、run discovery、按钮状态、自然语言到 TaskSpec 状态转换、知识/残差渲染、active run 自动刷新。
3. 全量：现有 593 项基线、Qt offscreen、`git diff --check`。
4. 真实 gate：使用一个完整 TaskSpec 从 GUI controller 启动 canonical solve；至少证明 run 创建、workflow/context/log 实时可见。若模型或 OpenFOAM 外部条件使求解未通过，必须按真实 failure 分类报告，不能用手工成功 artifact 替代。

## 12. 明确延期

- cancel/terminate/process-group 固化语义；
- strict resume 和 rerun-with-changes UI；
- asset 拖放、surface/patch 交互映射；
- VTK/PyVista 三维几何、mesh 和 field；
- ParaView 启动；
- 人工修改 case 与 user_revision；
- 远程服务器、HPC 和多用户。

这些延期不影响本阶段“从界面创建/验证任务、启动真实求解、查看公开知识引用和 residual”的最小可用闭环。
