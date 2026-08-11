# 阶段 4：FoamPilot Desktop IDE 规格

状态：设计已确认。Desktop A 只读 Run Inspector 和 Desktop B 交互式求解 v1 已完成实现与
真实 Foundation OpenFOAM v10 run gate。Desktop B v1 包含 TaskDraft/TaskSpec、环境检查、规范求解、
公开知识上下文和残差监控；Desktop C 的三维可视化与 Desktop D 的受控修改尚未实施。

## 1. 产品定位

`FoamPilot Desktop IDE` 是面向 Linux CAE/CFD 工作站的 Qt 桌面应用。它把前三阶段已经实现的
Knowledge/Skills、前处理和自然语言 TaskBuilder 组织成一个可观察、可控制、可扩展的本地工具：

```text
描述问题
-> 确认 TaskDraft、TaskSpec 和几何/网格意图
-> 启动规范 FoamPilot workflow
-> 观察网格、求解和 repair
-> 查看、比较和导出结果
-> 在明确 lineage 下进行恢复、重跑和受控修改
```

Desktop IDE 不是第二个 Agent，也不是新的求解器、工作流引擎、CAD 内核或 ParaView 替代品。

## 2. 目标用户与平台

首期目标用户是：

- 在 Linux 工作站上安装 Foundation OpenFOAM v10 的仿真工程师；
- 需要演示 Agent 从自然语言、几何到原生求解闭环的产品和技术人员；
- 需要检查 Agent 生成 case、日志、repair 和证据的研发人员。

首期平台限定为当前 FoamPilot 已验证的 Linux/OpenFOAM 环境。Windows/macOS 打包、远程服务器、
多用户、组织权限和集群调度属于后续独立工作，不能提前扩大 MVP。

## 3. 技术路线选择

### 3.1 首选：PySide6 + Qt Widgets

第一版采用：

```text
PySide6
+ Qt Widgets / QMainWindow / QDockWidget
+ QProcess
+ PyVistaQt 或 Qt/VTK bridge（可选安装）
+ 外部 ParaView
+ 现有 FoamPilot Python core
```

选择 Qt Widgets 而不是首期 QML 的原因：

- 更适合项目树、属性表、日志、Dock、文本编辑器和多文档布局；
- Python 可以直接复用 Pydantic 数据和 artifact parser；
- `QProcess` 能异步启动规范 CLI，不阻塞 UI 线程；
- VTK/PyVista 和本机 ParaView 的集成路径明确；
- 不需要 HTTP 服务、浏览器或 Node 工具链。

### 3.2 暂不采用的路线

- Qt/QML：适合后续视觉产品化，但首期数据绑定、编辑器和 VTK 集成成本更高；
- Web/Electron/Tauri：适合远程或跨平台入口，但不作为本地 OpenFOAM IDE 的首选；
- C++ Qt 前端 + Python RPC：跨语言边界过重，当前没有必要。

未来若增加 Web 入口，应复用同一个 application service 层，而不是改写核心 workflow。本阶段不
实现该入口，也不提前搭建 HTTP API。

## 4. 总体架构

```text
FoamPilot Desktop IDE
  PySide6 / Qt Widgets
        |
        v
Desktop Application Layer
  TaskController
  JobController
  RunRepository
  CaseWorkspace
  VisualizationBridge
        |
        v
FoamPilot Core
  TaskBuilder / NativeAgent / Runner / Evaluator
  WorkflowStore / ArtifactStore
        |
        v
OpenFOAM / Gmsh / MPI / ParaView
```

依赖方向必须是：

```text
desktop -> application services -> foampilot core
```

核心包不能反向依赖 Qt。未安装桌面依赖时，CLI 和 Python API 必须保持完整可用。

## 5. Desktop Application Layer

这一层解决桌面交互问题，不保存新的 CFD 真相。

### 5.1 `TaskController`

- 调用阶段 3 的 TaskExtractor、DraftValidator 和 TaskCompiler；
- 管理可编辑 TaskDraft；
- 显示 facts、assumptions、evidence 和 blocking questions；
- 编译并保存规范 TaskSpec；
- 不直接调用 OpenFOAM。

### 5.2 `JobController`

- 通过 `QProcess` 使用固定 program 和 args 启动 `foampilot solve/resume/report`；
- 不使用 shell command string；
- 记录 job receipt、PID、启动时间、TaskSpec hash 和 run root；
- 监视进程退出、超时和异常；
- 从 workflow events 读取业务状态，而不是根据进程是否存在猜测 solver 结果。

### 5.3 `RunRepository`

- 读取 run root、workflow events、summary、logs 和 artifact manifest；
- 验证路径和 manifest；
- 构造只读的 timeline、case tree、metrics 和 report view model；
- IDE 重启后从文件恢复状态；
- 不使用关系数据库替代 artifacts。

### 5.4 `CaseWorkspace`

- 展示 `0/`、`constant/`、`system/` 和 typed commands；
- 展示 attempt 之间的文件 diff；
- 标记 public asset、author、mesh/initializer/solver-created 来源；
- 首期不修改已固化 attempt；
- 后续只通过正式 `user_revision` 创建新 plan/new attempt。

### 5.5 `VisualizationBridge`

- 读取几何、网格和结果的轻量 metadata；
- 将可视化数据交给 PyVistaQt/VTK widget；
- 对大型结果提供降采样和异步加载；
- 通过固定 executable 打开 ParaView；
- 不负责判断 public validation 或 qualification。

## 6. UI 结构

第一版采用 `QMainWindow` 与可停靠面板：

```text
+----------------------------------------------------------------+
| 工具栏: 新建 打开 运行 Resume Rerun 报告 ParaView                |
+---------------+-------------------------------+----------------+
| Project Tree  | Central Workspace             | Properties     |
|               |                               |                |
| TaskDraft     | Natural-language task         | Geometry units |
| TaskSpec      | Geometry / mesh preview       | Patch mapping  |
| Geometry      | OpenFOAM dictionary viewer    | Mesh intent    |
| Case          | Attempt diff                  | Run resources  |
| Attempts      | Convergence / results         | Validation     |
| Results       |                               |                |
+---------------+-------------------------------+----------------+
| Workflow timeline / OpenFOAM log / error and recovery           |
+----------------------------------------------------------------+
```

布局可以持久化，但不得把用户本机绝对私有路径写入可交付配置。

## 7. 核心工作区

### 7.1 项目浏览器

按任务而不是按任意文件系统根目录组织：

- request 和 TaskDraft；
- TaskSpec；
- geometry/public assets；
- run 和 attempt；
- case files；
- mesh/report/results。

浏览范围只允许显式 workspace/run roots，禁止 `..`、符号链接逃逸和任意主目录浏览。

### 7.2 任务编辑器

- 自然语言输入；
- 结构化 facts/assumptions；
- blocking question 表单；
- TaskSpec YAML/表单双视图；
- schema、单位和 capability 错误定位；
- 编译前 diff。

表单和 YAML 始终编辑同一个内存模型，不能各自保存一份互相漂移的数据。

### 7.3 几何与网格工作区

- 导入阶段 2 支持的 STL/OBJ/Gmsh/OpenFOAM mesh；
- 显示 bounds、单位、surface、patch、region 和质量指标；
- 选择 surface 并映射 inlet/outlet/wall/interface 等物理角色；
- 显示 MeshIntent、cell budget 和 refinement；
- 异步加载 PyVista/VTK 数据，避免阻塞 UI 线程；
- 大型数据超出交互预算时退化为 metadata，并提示使用 ParaView。

第一版不提供 CAD 几何编辑和交互式网格节点拖动。

### 7.4 Case 与字典工作区

MVP 提供只读、高亮和 diff：

- OpenFOAM dictionary 结构和原始文本；
- manifest 与 typed commands；
- attempt 间变更；
- inspection issue 到文件位置的跳转；
- 文件 hash 和 artifact 状态。

可采用 `QPlainTextEdit` 和轻量 syntax highlighter。不得为了首期体验引入完整语言服务器。

### 7.5 运行和日志工作区

显示：

- task、environment、geometry probe、routing/context；
- generation、inspection、mesh/check、initialize；
- solve、postprocess、validation、repair、finalization；
- 当前 executable、attempt、开始时间、耗时和 timeout；
- stdout/stderr；
- residual、Courant、continuity、Diffusion number 和 time-step。

日志解析失败时显示原始日志并标记 `metric unavailable`，不能用空曲线表示零残差或成功。

### 7.6 结果工作区

明确区分：

- target solver started；
- solver normal completion；
- public validation；
- qualification；
- artifact manifest verification；
- 尚未证明的工程结论。

支持导出机器 JSON、中文 Markdown、case bundle 和 manifest。完整三维后处理通过 ParaView。

## 8. 长任务与线程边界

Qt 主线程只负责 UI。以下工作不能在主线程执行：

- 模型调用；
- OpenFOAM/Gmsh/MPI 命令；
- 大型 artifact scan；
- VTK 数据读取和转换；
- 日志全量解析。

进程边界：

```text
Qt UI
  -> QProcess(fixed program, args)
  -> foampilot CLI
  -> canonical NativeAgent workflow
```

`QThreadPool` 只用于受控的读取、解析和可视化准备；不能在后台线程偷偷执行第二条 solver 路径。

如果 IDE 关闭：

- 不向运行进程发送隐式 kill；
- job receipt 和 workflow artifact 保留；
- 下次启动从 run root 恢复；
- 若进程消失但没有终态，显示 `orphaned`，不得标记为 solver failed。

主动取消在 MVP 后实现。实现前必须定义 `CANCELLED` workflow 终态、受控进程组终止、OpenFOAM
子进程处理和 artifact 固化语义。

## 9. 人工修改与 lineage

MVP 允许编辑：

- TaskDraft；
- TaskSpec；
- GeometryInput；
- MeshIntent。

修改这些对象后运行属于 `rerun_with_changes`。

Agent 生成的 case 首期只读。后续 case 编辑必须走：

```text
已验证 parent plan/attempt
-> editable workspace copy
-> user changes + diff
-> schema/policy/semantic inspection
-> user_revision hash
-> new attempt
```

不得修改已经固化的 attempt，也不得让文本编辑器直接跳过 Runner。

## 10. 可视化策略

### 10.1 IDE 内部

适合直接显示：

- STL/OBJ surface；
- 中小规模 mesh；
- patch/region 着色；
- slice、bounding box 和简单 field preview；
- residual、Courant 和公开观测曲线。

PyVistaQt/VTK 作为 `foampilot[desktop-viz]` 可选依赖。没有该依赖时，IDE 仍能显示 metadata、
图表和 ParaView 入口。

### 10.2 ParaView

用于：

- 大型三维场；
- 时间序列；
- 高级 filter、streamline、contour 和 animation；
- 工程后处理工作流。

IDE 只对经过验证的 run 以固定 executable 和 case path 启动 ParaView，不接受任意 command。

## 11. 安装和启动

建议可选依赖：

```text
foampilot[desktop]      = PySide6 + desktop support
foampilot[desktop-viz]  = desktop + PyVistaQt/VTK integration
```

建议入口：

```bash
foampilot desktop
```

也可以提供独立 console script，但两者必须调用同一 entrypoint。桌面依赖不得进入最小 CLI wheel
的强制依赖。

## 12. 安全边界

- IDE 只访问用户显式打开的 workspace、task、asset 和 run roots；
- 所有相对路径 resolve 后必须仍位于允许 root；
- 禁止符号链接逃逸和任意本机文件浏览；
- `QProcess` 使用 program/args 分离接口，不接受 shell string；
- backend secret 继续只从环境变量读取，界面不显示实际值；
- logs 和导出执行 secret/protected-path scan；
- qualification private workspace 不出现在项目树；
- external viewer 只使用已登记 executable；
- Qt plugin path、recent files 和窗口状态不得包含到可交付 artifact。

## 13. 错误和恢复体验

每个错误显示：

```text
稳定英文 code
+ 中文 message
+ 中文 recovery
+ workflow stage
+ evidence/log/file location
+ 可用操作
```

IDE 不能合并：

- model backend failure；
- environment/tool failure；
- geometry/mesh failure；
- case generation/inspection failure；
- solver failure；
- public validation failure；
- qualification failure；
- IDE/job process failure。

IDE 自身崩溃或 worker 连接丢失不能覆盖已有 CFD primary failure。

## 14. 分步实现

### 14.1 Desktop A：只读 Run Inspector

- `QMainWindow`、project/run tree 和 Dock 布局；
- 打开已有 run；
- timeline、logs、case、diff、metrics、report 和 manifest verify；
- IDE 重启恢复；
- 不启动或修改任务。

### 14.2 Desktop B：任务创建与执行

- 已实现：TaskDraft/TaskSpec 编辑、高影响信息确认、preflight/model doctor；
- 已实现：`QProcess` 启动 canonical solve，使用唯一 job root 绑定具体 run；
- 已实现：实时 workflow/log、公开 Knowledge/Skill 引用和 solver residual 更新；
- v1 资产边界：TaskSpec 可以引用工程根目录内的公开资产，尚无拖放导入器。

### 14.3 Desktop C：几何、网格与结果可视化

- PyVistaQt/VTK 可选集成；
- patch/region 映射；
- mesh quality 和 convergence 图表；
- ParaView 入口；
- 大数据降级策略。

### 14.4 Desktop D：受控编辑

- TaskSpec 与 attempt diff；
- 正式 `user_revision`；
- 修改后重新 inspection 和新 attempt；
- 报告与 case bundle 导出。

只有 A 通过后才进入 B，B 通过后才进入 C。D 依赖正式的 revision/plan execution 契约，不能
通过直接写入 artifact 提前实现。

## 15. 测试策略

### 15.1 Qt 单元测试

采用 `pytest-qt` 和 offscreen Qt platform 验证：

- model/view binding；
- TaskDraft 与 TaskSpec 编辑一致性；
- project tree 路径约束；
- timeline 和失败分层；
- case viewer/diff；
- log 增量和曲线缺数据状态；
- report 和导出。

### 15.2 fake worker 集成测试

通过固定 fake executable 模拟：

- 成功 run；
- generation deferred；
- mesh failure；
- solver failure；
- repair 后成功；
- artifact 被修改；
- IDE 关闭后 worker 继续；
- IDE 重启时 run 恢复或标记 orphaned。

### 15.3 可视化测试

- 小型 STL/OBJ 和 mesh 加载；
- patch/region 选择；
- 大数据超限时退化而非冻结；
- 无 PyVistaQt 时的 metadata/ParaView fallback；
- GUI 对象释放后无残留 reader/worker。

### 15.4 真实桌面 gate

至少完成两个真实端到端桌面演示：

1. 参数化 `blockMesh` 单相算例，一次通过；
2. surface 或 Gmsh 几何算例，允许一次 scoped repair。

演示必须从自然语言任务编辑器开始，经过确认后的 TaskSpec，进入 canonical solve，在 Qt IDE 中
显示真实 OpenFOAM 日志、网格信息和最终 artifact verify，并能打开 ParaView。

## 16. 阶段验收

- 新用户不编辑 YAML 即可从桌面 IDE 完成一次真实求解；
- Qt IDE 与 CLI 对同一 run 返回相同 RunSummary、report 和 manifest verdict；
- UI 主线程在模型调用、mesh、solve 和大型结果加载期间保持响应；
- IDE 关闭和重启不破坏已开始的 worker 或 artifact；
- 不存在任意 shell、任意路径读取或 secret 回显；
- bubblewrap 不可用时展示 audited-host fallback，不出现 IDE 内部权限等待；
- solver failure 可定位到日志、attempt 和恢复建议；
- `PUBLIC_VALIDATION_PASS`、qualification `PASS` 和工程适用性保持分离；
- 未安装 desktop extras 时核心 CLI 和 Python API 完整可用；
- 不依赖 Web server、浏览器或 Node runtime。

## 17. 产物

- `foampilot[desktop]` 可选安装和桌面 entrypoint；
- PySide6/Qt Widgets 主窗口和 application services；
- Task、geometry、case、run、log、mesh、result 工作区；
- QProcess job control 和 run recovery；
- PyVistaQt/VTK 可选 bridge 与 ParaView 入口；
- Qt、worker、路径和 CLI/IDE 一致性测试；
- 两个真实 OpenFOAM 桌面演示 gate；
- Desktop IDE 使用说明与功能边界文档。

阶段 4 完成后，FoamPilot 的核心仍是独立 Python 工具包和 CLI；Desktop IDE 是正式但可选的
本地用户入口，不会反向控制或污染核心求解架构。
