# FoamPilot 现行架构与程序职责规范

状态：**现行架构基线，已冻结用于代码与测试减重**

日期：2026-08-13

冻结依据：项目所有者明确要求先以本文规范程序职责、能力边界和输入输出，再在新对话中执行
代码与测试减重。减重期间不得借“清理”改变本文定义的产品能力；确需改变职责或契约时，必须
先按第 10 节完成架构变更，而不是把变化混入等价重构。

适用范围：`src/foampilot/` 下的生产代码。测试、历史计划和阶段报告只能证明或解释本规范，
不能另行定义一条产品路径。

## 1. 文档用途

本文是 FoamPilot 架构、程序职责、输入输出和能力边界的减重准绳。后续重构可以移动、拆分、
合并或删除代码，但必须保持本文定义的外部契约、唯一权威和阶段边界。若实现与本文冲突，必须
先判断是实现缺陷还是架构需要变更；不得在普通“清理”中静默改变本文。

关键词含义：

- **必须**：违反即破坏现行架构；
- **不得**：禁止的依赖、权威或副作用；
- **可以**：兼容本文的实现选择，不构成稳定 API；
- **历史只读**：只允许展示旧产物，禁止重新进入 authoring、execution 或 qualification。

## 2. 产品和能力边界

FoamPilot 是面向本机 Foundation OpenFOAM 10 的自然语言 CFD Agent。它可以从公开自然语言和
公开资产建立 TaskSpec，也可以直接消费手写 TaskSpec；两种入口最终进入同一条求解状态机。

FoamPilot 负责：

- 公开任务和资产的严格校验；
- 网格/几何的确定性前处理和权威事实提取；
- 分阶段模型推理、来源协调和风险门禁；
- 基于冻结设计一次性编写完整 OpenFOAM case；
- 由系统确定性编译 typed command；
- 沙箱或显式 host policy 下的 OpenFOAM 执行；
- 单一证据提取、后处理和显式验收；
- 有限、受 envelope 约束的数值 repair；
- 本机 detached job、取消、恢复和不可变运行证据；
- Desktop 对同一 CLI/workflow 投影的交互。

FoamPilot 当前不宣称：

- 支持 Foundation 10 之外的 OpenFOAM 产品或版本；
- 任意远程/HPC 调度；
- 任意 OpenFOAM 时间目录的通用断点续算；
- 仅凭 solver 返回 0 就证明物理正确、工程可用或 qualification 通过；
- 已完成第二台干净 Ubuntu + Foundation v10 跨机门禁；
- Desktop 单元测试等同于真实 GUI 端到端门禁。

## 3. 不可破坏的架构不变量

1. **一条执行路径**：TaskBuilder 只产生 TaskSpec，不运行 OpenFOAM，也不建立第二套 Runner。
2. **一条状态机**：`NativeAgent` 使用 `workflow` 契约推进规范阶段；CLI、Desktop 和 job worker
   不复制求解状态机。
3. **输入权威分离**：网格拓扑来自程序，物理语义和任务目标来自用户，solver/模型/数值方案
   来自受 RiskGate 约束的工程设计。
4. **模型无执行权**：模型只返回结构化意图、设计、case 文件或 repair proposal；命令由系统
   编译，执行由 Runner 完成。
5. **冻结后单向流动**：下游只消费上游结构化产物，不重新解释原始提示词、网格或日志形成第二
   套事实。
6. **日志只解析一次**：OpenFOAM 原生日志只由 `evidence` 形成 `RunFacts`；其他模块读取事实。
7. **验收权威独立**：`RunAssessment` 不含用户阈值；只有已确认的 `AcceptanceCondition` 可以
   改变 `ResultReport.verdict`。
8. **不可变证据**：attempt、manifest、parent 和 continuation 不原地重写；恢复创建 child。
9. **失败分层**：观察、确认原因和推测原因分离；模型诊断永远不是 terminal authority。
10. **安全失败关闭**：来源不可信、资产冲突、路径逃逸、单位未知、能力缺失或风险不明时不得
    泛化放行。
11. **历史模型隔离**：TaskSpec v2、ExecutionPlan v3 和旧 public validation 只读，不回流。
12. **第一方扩展关闭注册**：能力、观测和 evidence extractor 使用代码内第一方 registry，
    不加载任意 entry point。

失败分层的固定表达为：**观察事实 ≠ 确认原因 ≠ 推测原因**。该表达在 FailureReport、Desktop
和 qualification 中必须保持同一含义。

## 4. 规范数据流

### 4.1 自然语言入口

```text
request text + declared public assets
-> AssetBundle manifest
-> PolyMeshTopologyFacts / public geometry metadata
-> TaskIngressContext
-> model-extracted draft candidates
-> AuthorityReconciler
-> TaskDraft v2
-> DraftReview
-> TaskCompiler
-> TaskSpec v3
```

TaskBuilder 只阻断必须由用户或资产提供的输入权威缺口，例如几何长度单位、资产声明、维度冲突
或损坏网格。solver、物性候选、边界数值、时间控制和工程容差属于后续设计，不应在 TaskBuilder
逐项重复追问。

原生 polyMesh 是目录资产。其 provided 路径为：

```text
OpenFOAMMeshBundle
-> hash/manifest + required-member validation
-> unit-independent PolyMeshTopologyFacts
-> explicit user length unit
-> geometry.mode=openfoam_mesh + mesh.strategy=provided
```

程序可以从网格确定点/面/单元、未缩放 bounds、patch、zone 和二维 empty 特征；不得从
polyMesh 猜测长度单位或区域物理语义。

### 4.2 设计、编写与执行

```text
TaskSpec v3 + authoritative public asset/mesh facts + EnvironmentSnapshot
-> CapabilityProfile
-> AgentContext
-> SimulationIntent
-> ResolvedRequirements
-> CaseDesignProposal
-> RiskDecision
   |- READY_TO_AUTHOR
   |  -> frozen CaseDesign
   |  -> AcceptancePlan + ObservationPlan
   |  -> CaseBundle + CaseManifest
   |  -> CaseVerifier
   |  -> ExecutionPlan v4
   |  -> materialized attempt
   |  -> PlanRunner
   `- RunFacts + RunAssessment
   |- CONFIRMATION_REQUIRED -> immutable confirmed child
   |- INFORMATION_REQUIRED -> normal deferred terminal
   `- CAPABILITY_UNAVAILABLE -> normal unsupported terminal
```

Case Author 生成文件但不能生成命令。PlanCompiler 消费冻结的第一方 mesh/solver contributor，
生成带 stage、argv、超时和资源约束的命令。Runner 不能修正设计或改写计划。

换言之，**Case Author 不生成命令**。高影响设计问题使用
`CONCRETE_CONFIRMATION_REQUIRED`：每个问题必须绑定字段、候选 ID 和完整 typed 候选值，
不存在 accept-all、continue-anyway 或泛化风险 override。模型不能自报 confidence 取得设计、
authoring 或执行放行；confidence 必须由程序根据已登记能力和权威证据计算。

### 4.3 观测、后处理与验收

```text
confirmed AcceptanceRequest
-> AcceptancePlan
-> ObservationPlan
-> system-owned collection configuration
-> native execution
-> RunFacts + declared structured outputs
-> DerivedMetrics
-> ResultReport
```

观测计划可以在求解前要求保存必要证据，但不能改变物理设计。没有显式阈值时可以产生指标，
不能据此制造 PASS。缺失已声明证据必须得到 `INCOMPLETE` 或明确 warning。

### 4.4 repair、恢复和 rerun

```text
failed RunFacts + FailureReport + frozen NumericalRepairEnvelope
-> bounded RepairScope
-> command-free RepairProposal
-> deterministic authorization
-> derived CaseDesign/CaseBundle
-> CaseVerifier + PlanCompiler
-> new immutable attempt
```

只有已分类的数值不稳定可以自动 repair。物理、solver、能力、网格或 envelope 外变化必须停止。
Strict resume 只恢复可重试的 generation/repair 中断并创建 child；任务、代码、模型、资产或策略
变化使用 rerun，不伪装为 resume。

`repair_policy.automatic_numerical_repair` 默认开启但可由用户关闭。关闭后数值不稳定也必须以
稳定原因终结，不能暗中恢复自动 repair；开启也不扩大 frozen envelope。

## 5. 核心数据契约与权威

| 契约 | 生产者 | 消费者 | 权威和持久化边界 |
|---|---|---|---|
| `PublicAsset` / `AssetBundle` | `tasks`、`assets` | preprocessing、agent | 路径、hash、成员和安装位置由程序验证 |
| `PolyMeshTopologyFacts` | `preprocessing.poly_mesh` | TaskBuilder、mesh probe | 单位无关，只含静态拓扑事实 |
| `TaskIngressContext` | `taskbuilder.context` | extraction model、reconciler | 冻结 Foundation 10 和压缩资产/拓扑事实 |
| `TaskDraft` | `taskbuilder.extraction` | validator、Desktop | 保留事实来源、问题和审计性推断，不执行 |
| `DraftReview` | `taskbuilder.validation` | compiler、Desktop、CLI | 输入权威门禁的唯一判定 |
| `TaskSpec v3` | compiler 或用户 | NativeAgent、qualification | 规范公开任务；v2 仅历史只读 |
| `EnvironmentSnapshot` | environment discovery | routing、runtime、resume | 本机 executable 的事实清单 |
| `GeometryFacts` / `InputMeshFacts` | preprocessing | routing、design、authoring | 程序探测事实；用户语义不在这里猜测 |
| `CapabilityProfile` | routing | context、simulation | confidence 由程序证据计算 |
| `AgentContext` | context | intent、design、author、repair | 有界公开 Knowledge/Skill，不含 evaluator 私有资产 |
| `SimulationIntent` | simulation intent stage | requirements、design | 模型解释后经来源协调，不获得用户权威 |
| `ResolvedRequirements` | deterministic resolver | design、RiskGate | 缺口、冲突和候选的结构化事实 |
| `CaseDesignProposal` | design model stage | RiskGate | 不含文件正文和命令 |
| `RiskDecision` / `CaseDesign` | deterministic RiskGate | confirmation、authoring | 唯一设计放行；CaseDesign 冻结后不可静默改变 |
| `AcceptancePlan` / `ObservationPlan` | acceptance/observation planner | authoring、postprocess | 用户条件与证据采集分离 |
| `CaseBundle` / `CaseManifest` | authoring | inspection、plan compiler | 完整文件集合；不含执行命令 |
| `ExecutionPlan v4` | plans compiler | materializer、Runner | 系统拥有的 typed commands；v3 只读 |
| `PlanRunResult` | runtime Runner | evidence、agent | 原始进程结果、日志位置和单调 elapsed |
| `RunFacts` / `RunAssessment` | evidence | repair、postprocess、UI、reports | 原生日志的唯一结构化解释，不含用户验收阈值 |
| `DerivedMetrics` | postprocessing | acceptance、results UI | 只来自 RunFacts/声明输出，保留 provenance |
| `ResultReport` | acceptance evaluator | report、UI、qualification | 只评价已确认条件 |
| `WorkflowEvent` / `RunSummary` | workflow/agent | CLI、Desktop、jobs | 规范阶段、失败层和终态，不承担 CFD 文本解析 |
| `ArtifactManifest` | artifacts | report、resume、recovery | 最终可信边界；读取结果前先校验 manifest |

## 6. 依赖方向和副作用规则

### 6.1 逻辑层级

```text
CLI / Desktop / Qualification / Jobs
                |
             NativeAgent
                |
Workflow + Simulation + Authoring + Plans + Repair + Acceptance
                |
Tasks + Assets + Preprocessing + Routing + Context + Inspection
                |
Environment + Runtime + Evidence + Artifacts + Model Gateway
                |
        filesystem / subprocess / OpenFOAM / model backend
```

该图表示控制和依赖方向，不表示底层包可以回调 UI。允许通过抽象接口注入 gateway、reporter、
registry、runner 或 store；不得 import Desktop/CLI 解决领域问题。

### 6.2 副作用所有权

| 副作用 | 唯一所有者 |
|---|---|
| 模型 transport | `models` backend + `ModelGateway` |
| public asset hash/staging | `tasks.io`、`assets` adapters |
| OpenFOAM 环境发现 | `environment`、`runtime.config` |
| case 文件持久化 | `agent.generation` / artifact store 内的受控写入 |
| typed native process | `runtime.PlanRunner` / `activity.run_supervised_process` |
| workflow event/checkpoint | `workflow.WorkflowStore` |
| job receipt/control/status | `jobs.LocalJobStore` / worker |
| 原生日志解释 | `evidence` |
| post-process 指标计算 | `postprocessing` |
| user acceptance verdict | `acceptance` |
| GUI process invocation | `desktop.DesktopJobController`，固定 CLI argv |

任何新实现若在第二处创建同类副作用，必须先修改本规范并说明为何不再需要唯一所有者。

## 7. package 级职责和边界

| package | 输入 | 输出 | 允许职责 | 禁止职责 |
|---|---|---|---|---|
| `acceptance` | confirmed request、DerivedMetrics | AcceptancePlan、ResultReport | 编译和确定性评价用户条件 | 解析 OpenFOAM 日志、猜阈值 |
| `activity` | 操作、进程流、取消信号 | ActivityEvent、进程结果 | 通用存活/取消/事件汇聚 | CFD 失败分类、workflow authority |
| `agent` | TaskSpec、runtime、gateway、stores | NativeAgentOutcome、run artifacts | 组合规范阶段并处理终结 | 建立另一数据模型或自行解析日志 |
| `artifacts` | run/attempt payload | manifest、summary、不可变路径 | 独占目录、hash、脱敏 | 解释 CFD 正确性 |
| `assets` | declared source path + digest | AssetBundle、StagedAsset | 第一方资产验证和原子 staging | 猜测资产语义、任意插件加载 |
| `authoring` | frozen CaseDesign、上下文、观测契约 | CaseBundle、CaseManifest | 一次模型调用生成完整 case | 生成/执行命令、改变设计 |
| `cli` | argv | JSON/人类输出、exit code | 适配公开命令到领域 API | 复制领域状态机和日志解析器 |
| `context` | TaskSpec、capability、公开 corpus/skills | AgentContext | 有界、槽位化检索 | 读取私有 evaluator 或目标 tutorial |
| `desktop` | workspace、job/run artifacts | GUI view、固定 CLI job | 调用同一 CLI、显示同一 projection | 内置第二套 solve、事实或日志解释器 |
| `environment` | RuntimeConfig/OpenFOAM root | EnvironmentSnapshot | 无 tutorial 的 executable 发现 | 运行求解、读用户 case |
| `evidence` | PlanRunResult、native logs | RunFacts、RunAssessment、metrics | 单次解析并冻结执行事实 | 用户阈值验收、repair 决策 |
| `extensions` | target、facts、registry | descriptor、plan fragments | 第一方能力和计划贡献 | 动态 entry point、模型命令 |
| `improvement` | immutable reports/runs | candidate、promotion report | 离线分析与比较 | 自动修改 Knowledge/Skill/生产代码 |
| `inspection` | CaseBundle、CaseDesign、plan | InspectionReport | 静态/跨文件一致性和安全检查 | 执行命令、物理 qualification |
| `jobs` | immutable JobSpec、control | durable status、recovery decision | 本机 detached worker、取消、孤儿恢复 | CFD 领域判断、修改 parent run |
| `knowledge` | packaged public YAML | corpus、matches、coverage | 来源明确的确定性检索 | 私有 evaluator、自动 promotion |
| `manifests` | authored declarations | CaseManifest/family contract | 轻量 region/field/patch 语义 | 重复完整 case 或命令 |
| `models` | ModelRequest、schema、budget | validated model result、trace | provider-neutral transport/retry/budget | OpenFOAM/任务领域判定、保存 secret |
| `observations` | requests、design、registry | ObservationPlan、系统采集片段 | 冻结证据需求、第一方采集配置 | 用户 verdict、任意模型脚本 |
| `performance` | immutable run/cache dependencies | timings、reuse/cache decision | 显式可审计复用与性能事实 | 静默改变任务或 qualification 条件 |
| `physics` | 已导出的公开场/数值 | 独立 audit metric | golden-free 专项物理审计 | 作为通用 solver 验收替代品 |
| `plans` | verified bundle、frozen design、contributors | ExecutionPlan v4、issues | 确定性 typed command 编译/校验 | 模型生成命令、执行命令 |
| `postprocessing` | RunFacts、声明输出 | DerivedMetrics | provenance-preserving metric derivation | 重新解释全量日志、决定 PASS |
| `preprocessing` | declared geometry/polyMesh、runner | geometry/mesh facts、quality | 模型前确定性事实和受控 checkMesh | 猜单位/物理角色、生成 case |
| `qualification` | frozen suite、private reference、NativeAgent | QualificationReport | 角色隔离的外部物理比较 | 绕过 canonical solve、泄露 reference |
| `repair` | envelope、proposal、facts/design | authorization、derived design | 确定性约束与最小变更 | 自动改物理/solver/mesh、生成命令 |
| `reporting` | RunFacts、failure、diagnostic | FailureReport | 观察/原因/hypothesis 分层 | 让模型 hypothesis 成为终态权威 |
| `routing` | TaskSpec facts、environment、knowledge | CapabilityProfile | evidence-first family routing | case authoring、模型 confidence 放行 |
| `runtime` | RuntimeConfig、validated plan、case | policy、PlanRunResult、preflight | sandbox/host policy、进程、预算和日志 | 改写 plan/case、repair、acceptance |
| `simulation` | TaskSpec、facts、capability、model | intent、requirements、design、risk | 分阶段工程推理与确定性放行 | 写文件、生成/执行命令 |
| `skills` | packaged Skill/scenario/evidence | validation result、portable guidance | Skill 结构与前向证据 | 私有 evaluator、运行状态机 |
| `taskbuilder` | request、public assets、ingress facts | TaskDraft、DraftReview、TaskSpec | 求解前输入权威编译 | 运行 OpenFOAM、提前替代 CaseDesigner |
| `tasks` | YAML/model payload、asset root | TaskSpec、staged public assets | 公开任务/几何/资源严格契约 | 模型推理、求解 |
| `validation` | historical validation JSON | legacy read-only model | 历史展示兼容 | 新运行验收或 authoring |
| `workflow` | stage outcomes、events、confirmation | state、projection、child continuation | 纯阶段推进、持久化和 UI 投影 | CFD 文本解析、领域事实重判 |

## 8. 文件级职责目录

以下覆盖全部生产 Python 文件。`输入` 指函数/模型消费的数据；`输出` 包括返回值和明确副作用；
`边界` 记录该文件不得承担的相邻职责。包内私有函数不是稳定 API，稳定导出以各包
`__init__.py` 为准。

### 8.1 根、acceptance、activity

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `foampilot/__init__.py` | 包身份和版本入口 | import | `__version__` 等包级标识 | 无领域行为 |
| `acceptance/__init__.py` | acceptance 公开导出 | import | 公共模型/compiler/evaluator | 不实现判定 |
| `acceptance/models.py` | 验收请求、条件、结果的冻结模型 | typed payload | Pydantic contracts | 无 I/O |
| `acceptance/compiler.py` | 把已确认 request 编译为条件 | requests、Task authority | AcceptancePlan | 不计算指标、不猜阈值 |
| `acceptance/evaluator.py` | 按 scope/time/operator 评价指标 | plan、DerivedMetrics | ResultReport | 不读取日志、不补证据 |
| `activity/__init__.py` | activity 公开导出 | import | event/reporter/process API | 无领域行为 |
| `activity/models.py` | Qt 无关的操作事件模型 | event payload | ActivityEvent | 无 I/O |
| `activity/reporter.py` | 线程安全事件创建、扇出和取消检查 | source、sink、cancel signal | emitted events / OperationCancelled | 不决定 workflow 终态 |
| `activity/process.py` | 可取消、可终止进程组的轮询执行 | fixed argv、cwd、reporter | SupervisedProcessResult、流事件 | 不解释 OpenFOAM |
| `activity/sinks.py` | JSONL、stderr、stream 事件 sink | ActivityEvent | durable/visible event output | 不改变 event 语义 |

### 8.2 agent、artifacts

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `agent/__init__.py` | Native Agent 公开导出 | import | `NativeAgent` 等 API | 不创建第二入口 |
| `agent/context.py` | 规范 capability-routed context 入口 | task、capability、corpus、skills | AgentContext | 不自行检索私有资产 |
| `agent/contract_stages.py` | 适配 observation/acceptance 生产阶段 | run dir、design、facts | PlanningContracts、PublicResults、artifacts | 不编排完整 solve |
| `agent/failure.py` | 确定性 native failure 分类 | plan、RunFacts、inspection | NativeFailureClassification | hypothesis 不冒充确认原因 |
| `agent/generation.py` | 原子 materialize 已验证 plan 文件 | ExecutionPlan、case dir | case files | 不生成或修正 plan |
| `agent/native_orchestrator.py` | 规范 plan/solve/resume/rerun 总编排 | TaskSpec、runtime、gateway、stores | NativeAgentOutcome、run tree | 不复制领域解析器；应保持薄协调目标 |
| `agent/repair.py` | repair 次数/指纹和模型 proposal 请求 | failure scope、gateway、budget | RepairProposal/stop decision | 不授权 proposal |
| `agent/repair_scope.py` | 为一次 repair 选择有界证据 | failed plan/facts/bundle | RepairScope | 不暴露整个 case/私有数据 |
| `agent/status.py` | 决策点的确定性、无泄漏状态快照 | workflow、facts、budget、capability | AgentStatusSnapshot/hash | 模型不能自报状态 |
| `artifacts/__init__.py` | artifact API 导出 | import | stores/models | 无存储实现 |
| `artifacts/models.py` | attempt/run/outcome 稳定摘要 | typed payload | AttemptSummary、RunSummary、NativeAgentOutcome | 不做持久化 |
| `artifacts/store.py` | 独占 run/attempt 和内容 manifest | root、artifact bytes/models | paths、SHA256 manifest | 不解释内容语义 |

### 8.3 assets、authoring、CLI

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `assets/__init__.py` | immutable asset 公开导出 | import | adapters/contracts | 无 staging 策略复制 |
| `assets/models.py` | bundle/member/staged asset 冻结契约 | member metadata | AssetBundle、manifest digest | 无 filesystem I/O |
| `assets/adapters.py` | 第一方 asset adapter protocol | declared asset/root | adapter interface | 不注册外部插件 |
| `assets/openfoam_mesh.py` | polyMesh 目录验证和原子 staging | directory PublicAsset、root | AssetBundle、StagedAsset | 不解析物理/单位，不运行 conversion |
| `assets/public_file.py` | 单个公开文件的 hash adapter | file PublicAsset、root | AssetBundle、StagedAsset | 不识别领域内容 |
| `authoring/__init__.py` | authoring 公开导出 | import | CaseBundle/author API | 无模型调用实现 |
| `authoring/models.py` | command-free case bundle contract | structured model output | CaseBundle | 禁止 command/shell 字段 |
| `authoring/case_author.py` | 冻结设计的一次完整 case author 调用 | CaseDesign、facts、context、observations | validated CaseBundle | 不改变设计、不执行命令 |
| `cli/__init__.py` | CLI package 标识 | import | entrypoint namespace | 无命令逻辑 |
| `cli/main.py` | argv 解析、领域 API 适配、输出码 | argv、config/files | JSON/human output、exit code | 不复制 solve/证据/验收逻辑 |

### 8.4 context、desktop、environment

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `context/__init__.py` | context 公开导出 | import | assembler/models API | 无检索逻辑 |
| `context/models.py` | 有界 Agent context 合同 | rendered slots、skills | AgentContext | 无 I/O |
| `context/slots.py` | 语义 slot 和剪枝优先级 | slot name | ContextSlot | 不做检索 |
| `context/skill_registry.py` | 选择通用/族级 Skill | family、packaged skills | names/content | 不运行 Skill、不读私有 Skill |
| `context/assembler.py` | 每槽至多一条公开知识的装配 | task、route、corpus、failure | AgentContext | 无无关 top-N、无 tutorial |
| `desktop/__init__.py` | 可选 Desktop 依赖边界 | import | dependency error / lazy API | core 不依赖 Qt |
| `desktop/application.py` | 显式启动 PySide6 应用 | argv/config | QApplication exit code | 只在用户请求时 import Qt |
| `desktop/cursors.py` | 增量读取追加日志的 byte cursor | path、offset | LineChunk | 不解析领域内容 |
| `desktop/job_controller.py` | 固定 argv 的 QProcess job 控制 | workspace/CLI args | process signals/status | 不内嵌 solve 状态机 |
| `desktop/main_window.py` | 交互工作台和动作布线 | workspace、controller、snapshot | rendered UI/user actions | 不解析 OpenFOAM 或另算 verdict |
| `desktop/repository.py` | 安全只读打开一个 run | explicit run path | RunSnapshot/file views | 不跟随逃逸 symlink、不修改 run |
| `desktop/residual_plot.py` | 最小残差绘图控件 | residual samples | Qt painting | 不计算残差 |
| `desktop/viewmodels.py` | Qt 无关不可变视图模型 | repository/projection data | RunSnapshot 等 | 无 I/O |
| `desktop/workspace.py` | 项目输入、draft 和确认文件管理 | project paths/user answers | exclusive workspace artifacts | 不提升模型推断权威 |
| `environment/__init__.py` | environment 公开导出 | import | discovery/models | 无探测副作用 |
| `environment/models.py` | executable/environment 事实模型 | discovered metadata | EnvironmentSnapshot | 无 I/O |
| `environment/discovery.py` | 在隔离 HOME/PATH 下发现 Foundation 10 | RuntimeConfig/root | EnvironmentSnapshot/help facts | 不读 tutorial、不运行 case |

### 8.5 evidence、extensions

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `evidence/__init__.py` | evidence 延迟公开导出 | import | facts/extractor/assessment API | 无解析实现 |
| `evidence/models.py` | 单 attempt 原始事实冻结合同 | parsed observations | RunFacts | 无用户阈值 |
| `evidence/extractors.py` | extractor protocol 和关闭 registry | target/family | selected extractor | 不加载 entry point |
| `evidence/openfoam10.py` | 一次解析 Foundation 10 计划日志 | ExecutionPlan、PlanRunResult | RunFacts | 不评价用户验收 |
| `evidence/assessment.py` | 与阈值无关的执行评估 | RunFacts、inspection | RunAssessment | 不产出 qualification PASS |
| `evidence/metrics.py` | 有界非权威 live metric 存储 | MetricPoint stream | metrics.jsonl/projection | 损坏不改 workflow 终态 |
| `evidence/telemetry.py` | 增量解析实时残差/Courant | log chunks | ResidualMetric 等 | 仅 telemetry，不替代最终 extractor |
| `extensions/__init__.py` | 扩展公共导出和 lazy API | import | descriptor/registry/planning | 不发现第三方插件 |
| `extensions/models.py` | capability descriptor 合同 | metadata | CapabilityDescriptor | 无执行 |
| `extensions/registry.py` | 第一方 descriptor/contributor 注册解析 | registrations、target | resolved capability | 拒绝重复/未知，不动态加载 |
| `extensions/planning.py` | 纯计划 contributor protocol | PlanContext | PlanFragment | 不执行命令 |
| `extensions/mesh/__init__.py` | mesh contributors 导出 | import | block/provided contributors | 无逻辑 |
| `extensions/mesh/block_mesh.py` | blockMesh 阶段贡献 | parametric mesh context | typed mesh fragment | 不写 case、不执行 |
| `extensions/mesh/openfoam_mesh.py` | provided mesh 阶段贡献 | staged polyMesh context | checkMesh/solver-ready fragment | 不重生成网格 |
| `extensions/solver/__init__.py` | solver contributors 导出 | import | Foundation contributors | 无逻辑 |
| `extensions/solver/foundation10.py` | serial/MPI Foundation 10 solver fragment | frozen solver context | typed solver commands | launcher 仍由 Runner 拥有 |

### 8.6 improvement、inspection、jobs

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `improvement/__init__.py` | offline improvement 公开导出 | import | analysis/io/promotion API | 无自动学习 |
| `improvement/models.py` | candidate/promotion 冻结模型 | public evidence refs | LearningCandidate、PromotionReport | 无生产写入 |
| `improvement/analysis.py` | 从不可变公开证据建候选 | run artifacts、optional official evidence | LearningCandidate | 不复制官方 case/私有值 |
| `improvement/io.py` | candidate YAML 读和独占写 | path/model | LearningCandidate/file | 不覆盖已有文件 |
| `improvement/promotion.py` | 比较两个冻结 qualification report | baseline/candidate reports | PromotionReport | 不自动 promotion |
| `inspection/__init__.py` | inspection 公开导出 | import | models/checkers | 无检查实现 |
| `inspection/models.py` | inspection issue/report 模型 | issue payload | InspectionReport | 无 I/O |
| `inspection/native_case.py` | 通用静态 case 安全/语法检查 | generated files/manifest | InspectionReport | 不运行 OpenFOAM |
| `inspection/semantic.py` | plan/case 高置信跨文件检查 | plan、case、family contract | semantic issues | 不做猜测性物理判断 |
| `inspection/design_conformance.py` | authored files 对 frozen design 的一致性 | CaseDesign、CaseBundle | conformance report | 不修改 bundle |
| `jobs/__init__.py` | local job 公开导出 | import | models/store/worker/recovery | 无 worker 启动 |
| `jobs/models.py` | job receipt/status/control/recovery 合同 | typed payload | JobSpec、JobStatus 等 | 无 I/O |
| `jobs/identity.py` | 防 PID reuse 的 Linux identity | pid/proc facts | ProcessIdentity/match | 不发送信号 |
| `jobs/store.py` | 原子 job receipt/status/control/writer lock | job root/models | durable JSON/locks | 不执行 solve |
| `jobs/worker.py` | 单 job detached writer-locked worker | immutable JobSpec | status/control/workflow outcome | 不重解释 CFD；状态写失败需显式终结 |
| `jobs/recovery.py` | job/run 对账、孤儿终止和 finalize | job status、process identity、run artifacts | RecoveryDecision/terminal evidence | 不猜领域成功、不修改有效 parent |

### 8.7 knowledge、manifests、models

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `knowledge/__init__.py` | Knowledge 公开导出 | import | contracts/IO/retrieval | 无自动检索副作用 |
| `knowledge/models.py` | 来源、适用性和泄漏字段合同 | YAML payload | KnowledgeEntry | 无 I/O |
| `knowledge/io.py` | corpus/manifest 加载验证 | packaged paths | entries/schema/manifest verdict | 不加载私有 evaluator |
| `knowledge/retrieval.py` | leakage-aware 确定性选择 | KnowledgeQuery、entries | ranked matches | 不进行 embedding/network |
| `knowledge/coverage.py` | solver family 覆盖矩阵 | corpus/families | CoverageReport | 不声称求解能力 |
| `manifests/__init__.py` | manifest 公开导出 | import | CaseManifest/contracts | 无验证实现 |
| `manifests/models.py` | region/field/patch/models 轻量声明 | authored metadata | CaseManifest | 不复制完整文件 |
| `manifests/family_contracts.py` | 带来源的 solver family 语义规则 | family name | FamilyContract | 未登记只 advisory |
| `manifests/validation.py` | 独立 manifest schema 校验 | manifest path/payload | validation result | 不检查完整 case |
| `models/__init__.py` | provider-neutral model API 导出 | import | gateway/backend/contracts | 无 transport |
| `models/base.py` | ModelRequest/context artifact 合同 | prompt metadata | typed request | 不含 backend secret |
| `models/backend.py` | backend protocol/health/response | ModelRequest | BackendResponse | 不做 retry policy |
| `models/budgets.py` | 单调时钟 stage/lineage 预算 | limits、usage | budget windows/ledger | 不依赖 UTC duration |
| `models/circuit_breaker.py` | 共享 backend 熔断状态 | backend key/outcomes | allow/defer/state | 不改变任务结果 |
| `models/command_backend.py` | 固定 argv 的已认证外部 runner | config、ModelRequest | BackendResponse | 不读 Codex credential 文件、不用 shell |
| `models/config.py` | 严格 YAML backend registry 加载 | config path | BackendRegistry | 拒绝 secret literal |
| `models/errors.py` | provider-neutral 错误分类 | failure metadata | BackendError/kind | 无 I/O |
| `models/gateway.py` | retry/failover/budget/schema 协调 | request、schema、registry、ledger | validated ModelResult/trace | 不做 CFD 领域判断 |
| `models/messages_zh.py` | 稳定 backend code 中文说明 | error code | message/recovery | 不改 code |
| `models/openai_compatible.py` | OpenAI-compatible HTTP transport | endpoint/model/env credential name | BackendResponse | 不保存 token/header |
| `models/registry.py` | backend 注册、选择、并发健康检查 | configs/backends | selection/doctor records | qualification 不自动换模型 |
| `models/schema.py` | Pydantic schema 降为模型兼容子集 | JSON schema | normalized schema | 不放宽业务模型 |
| `models/traces.py` | 无正文/secret 的 transport trace | attempts/outcomes | memory/JSONL trace | 不保存 prompt/response/header/env value |

### 8.8 observations、performance、physics

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `observations/__init__.py` | observation 延迟公开导出 | import | models/planner/compiler API | 无编译实现 |
| `observations/models.py` | scope/time/evidence plan 冻结合同 | request payload | ObservationPlan | 无 I/O |
| `observations/registry.py` | quantity/dimension 第一方 registry | quantity/target | descriptor/contract | 不动态加载插件 |
| `observations/planner.py` | 从 request/design 选择采集策略 | requests、CaseDesign、registry | ObservationPlan/warnings | 不评价条件 |
| `observations/openfoam10.py` | Foundation 10 系统 function object/postProcess 配置 | ObservationPlan、design | config fragments/typed commands | 模型不拥有采集命令 |
| `performance/__init__.py` | performance/reuse API 导出 | import | models/cache/reuse/reporting | 无缓存副作用 |
| `performance/models.py` | timing、model usage、reuse 合同 | measured facts | PerformanceSummary | 无测量实现 |
| `performance/derived_cache.py` | geometry/mesh 内容寻址缓存 | dependency files/hashes | cache key/lookup/materialized cache | 命中不替代当前 checkMesh |
| `performance/plan_reuse.py` | 显式 verified plan source 资格检查 | source run、current task/env | VerifiedPlanSource 或拒绝 | 拒绝不静默回退 generation |
| `performance/repair_reuse.py` | repair 后可跳过阶段的依赖判定 | prior attempt/change set | RepairReusePreparation | 依赖不明则完整重跑 |
| `performance/reporting.py` | 从不可变事件重建耗时 | run tree、traces、results | performance JSON | 不用估计替代证据 |
| `physics/__init__.py` | 专项物理 audit 导出 | import | audit helpers | 不作为通用 validation |
| `physics/shock_tube.py` | 理想气体 Riemann 解和波检测 | states/gamma/profiles | analytical solution/wave positions | golden-free，不运行 solver |
| `physics/wall_heat_flux.py` | wallHeatFlux 数据解析与平衡 audit | declared output/case runner | PatchHeatFlow/WallHeatBalance | 只处理显式专项 audit |

### 8.9 plans、postprocessing、preprocessing

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `plans/__init__.py` | plans 延迟公开导出 | import | v4 models/compiler/validation API | 无编译实现 |
| `plans/models.py` | GeneratedFile/NativeCommand/ExecutionPlan v4 | typed payload | strict plan models | 命令无 shell 字段 |
| `plans/command_stages.py` | utility executable 到阶段的确定性映射 | executable name | CommandStage | 不运行命令 |
| `plans/compiler.py` | 组合 verified bundle 和 contributors | design、bundle、fragments | ExecutionPlan v4 | 不调用模型 |
| `plans/input_normalizer.py` | 临时、无歧义 legacy/model input 规范化桥 | incoming plan shape | canonical IDs/fields | 不泛化修复语义错误 |
| `plans/normalizer.py` | 安全 MPI/utility metadata 规范化 | ExecutionPlan | NormalizationResult | 不改变 solver/文件 |
| `plans/validation.py` | argv/path/resource 安全 policy | ExecutionPlan | PlanIssue list | 不执行、不 repair |
| `plans/legacy.py` | manifest-bound v3 replay loader | historical run | LegacyExecutionPlanV3 | 禁止新执行 |
| `postprocessing/__init__.py` | derived metric API 导出 | import | engine/models/calculators | 无计算实现 |
| `postprocessing/models.py` | sample/series/metrics provenance 合同 | calculated samples | DerivedMetrics | 无 I/O |
| `postprocessing/engine.py` | 按 observation 隔离 calculator | RunFacts、declared outputs、plan | DerivedMetrics | 单项失败不污染其他项；不判 PASS |
| `postprocessing/openfoam10.py` | Foundation 10 residual/continuity/structured output calculators | RunFacts/files | MetricSeries | 核对 quantity/dimensions，不重扫任意日志 |
| `preprocessing/__init__.py` | preprocessing 公开导出 | import | facts/probe API | 无探测实现 |
| `preprocessing/models.py` | geometry/mesh/topology/quality 事实合同 | parsed observations | facts models | 区分 unscaled 与 unit-aware |
| `preprocessing/poly_mesh.py` | 有界静态解析 polyMesh | atomic bundle members | PolyMeshTopologyFacts/InputMeshFacts | 不猜单位/角色，不解析任意 include |
| `preprocessing/geometry_probe.py` | model 前的有界 surface/geometry 探测 | declared asset + explicit unit/roles | GeometryFacts | 不推断用户语义 |
| `preprocessing/mesh_probe.py` | staged provided mesh 的系统动态检查 | case、RuntimeConfig/Runner | ExecutedMeshFacts | 固定受控 checkMesh，不 author |
| `preprocessing/mesh_quality.py` | 从 RunFacts 建 mesh quality | RunFacts、TaskSpec thresholds | MeshQualityReport | 观测与阈值分栏，不重读日志 |

### 8.10 qualification、repair、reporting

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `qualification/__init__.py` | qualification 公开导出 | import | suites/runner/report API | 无运行实现 |
| `qualification/models.py` | suite/result/comparison/report 合同 | validation payload | QualificationReport | 无 I/O |
| `qualification/suites.py` | packaged suite manifest 加载 | suite path/name | QualificationSuite | 不运行任务 |
| `qualification/profiles.py` | evaluator-owned OpenFOAM field 采样 | case path/fields | arrays/profile data | 不进入 Agent prompt |
| `qualification/validators.py` | case-specific observable 和 reference 比较 | copied case output/private validation | metrics/comparisons | 只在 evaluator 边界运行 |
| `qualification/reporting.py` | classification、聚合、序列化 | NativeOutcome、metrics、metadata | JSON/Markdown report | 区分 NOT_RUN/FAIL/PASS |
| `qualification/runner.py` | suite 通过 NativeAgent 执行 | suite、gateway、runtime、private data | QualificationReport | 不绕过 canonical solve；固定 backend/model |
| `repair/__init__.py` | repair lazy 公开导出 | import | models/envelope/coordinator | 无授权实现 |
| `repair/models.py` | envelope/proposal/authorization/derived design 合同 | typed payload | repair contracts | proposal 无 command |
| `repair/envelope.py` | proposal 对 frozen envelope 的数值授权 | proposal、rules | RepairAuthorization | fail closed，不改设计 |
| `repair/coordinator.py` | repair 路由、授权和派生设计应用 | facts/design/proposal/policy | RepairDecision/derived design | 不执行命令、不放宽 envelope |
| `reporting/__init__.py` | reporting 公开导出 | import | failure/diagnostic API | 无报告实现 |
| `reporting/failure.py` | 分层构建 deterministic failure report | RunFacts、classification、repair state | FailureReport | 观察、确认、hypothesis 不混淆 |
| `reporting/model_diagnostic.py` | 可选追加非权威模型 hypothesis | sanitized FailureReport、gateway | ModelDiagnostic | 不改变 terminal code/verdict |

### 8.11 routing、runtime、simulation

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `routing/__init__.py` | routing 公开导出 | import | profile/router API | 无路由实现 |
| `routing/models.py` | route evidence/confidence/profile 合同 | typed payload | CapabilityProfile | 无 I/O |
| `routing/confidence.py` | 从证据状态计算 confidence | RouteEvidenceState | numeric/category confidence | 不接受模型自报 |
| `routing/registry.py` | 小型 solver-family consistency facts | solver name | SolverCapability | 不替代 extension registry |
| `routing/router.py` | task/env/knowledge evidence-first 路由 | TaskSpec、facts、EnvironmentSnapshot、corpus | CapabilityProfile | 不 author、不执行；模型只能辅助歧义候选 |
| `runtime/__init__.py` | runtime 公开导出 | import | config/policy/runner/models | 无执行实现 |
| `runtime/models.py` | runtime config/risk/policy/step/run 合同 | typed payload | runtime models | elapsed 使用单调来源 |
| `runtime/config.py` | CLI/TOML/env/有限发现合并 | overrides、env、config | RuntimeResolution | 不用固定用户路径；source HOME 一致 |
| `runtime/preflight.py` | 有效配置的结构化 readiness | RuntimeResolution/workspace | RuntimePreflightReport | 每个 blocking failure 有稳定 code |
| `runtime/policy.py` | 纯 sandbox/host backend 决策 | isolation、risk、probe | ExecutionPolicyDecision | 不启动进程 |
| `runtime/risk.py` | case/command 静态 host fallback 风险扫描 | case tree、plan | ExecutionRiskReport | 高风险 fail closed，不作为 sandbox 等价物 |
| `runtime/protection.py` | 合并模型不得访问的机器路径 | runtime/config/caller paths | protected path set | 不读取路径内容 |
| `runtime/sandbox.py` | 构造/探测 networkless bubblewrap | RuntimeConfig、mounts、case | SandboxLaunch/SandboxProbe | 不决定 CFD 设计 |
| `runtime/plan_runner.py` | 直接执行已验证 typed commands | ExecutionPlan、policy、case、reporter | PlanRunResult/logs | 不修正 argv/plan，不执行模型命令 |
| `simulation/__init__.py` | simulation lazy 公开导出 | import | intent/design/risk contracts | 无推理实现 |
| `simulation/provenance.py` | fact/candidate/uncertainty/confirmation 合同 | typed value+source | frozen provenance objects | 无 I/O |
| `simulation/io.py` | 规范 hash 和独占 JSON/YAML 写 | models/payload/path | digest/artifact | 不覆盖现存文件 |
| `simulation/intent.py` | intent-only 模型调用和来源协调 | TaskSpec/facts/context/gateway | SimulationIntent | 不选 solver files/commands，不提升模型 authority |
| `simulation/requirements.py` | 确定性完整性/冲突/候选解析 | intent、facts、capability | ResolvedRequirements | 不调用模型 |
| `simulation/design.py` | design-only 模型 stage 和能力协调 | requirements、context、registry/gateway | CaseDesignProposal | 不写文件/命令 |
| `simulation/risk_gate.py` | 四状态确定性 release gate 和冻结 | proposal、requirements、sources | RiskDecision/CaseDesign | 唯一 authoring 放行，无 continue-anyway |

### 8.12 skills、taskbuilder、tasks、validation、workflow

| 文件 | 职责 | 输入 | 输出 | 边界 |
|---|---|---|---|---|
| `skills/__init__.py` | Skill 公开导出 | import | models/validation | 不自动执行 Skill |
| `skills/models.py` | scenario/evidence/issue 合同 | YAML/evidence payload | Skill models | 无 I/O |
| `skills/validation.py` | Skill 结构和前向证据校验 | Skill root/scenario/evidence | validation issues | 不执行私有 evaluator |
| `taskbuilder/__init__.py` | NL request 编译公开 API | import | context/extract/validate/compile contracts | 内部拆分模块不承诺稳定 API |
| `taskbuilder/models.py` | draft/fact/question/review/compilation 合同 | typed payload | TaskDraft 等 | 无模型调用/I/O |
| `taskbuilder/context.py` | 模型前确定性 ingress facts | target、AssetBundle、topology | TaskIngressContext/agent payload | 冻结 Foundation 10、限制大小、不含 raw mesh |
| `taskbuilder/extraction.py` | 模型提取、权威协调和 Draft 组装的现行集中实现 | request、assets、context、gateway | TaskDraft | 不执行 OpenFOAM；减重后应成为薄编排入口 |
| `taskbuilder/projection.py` | validator/compiler 共用 authority 投影 | TaskDraft facts | compilable map/effective geometry | 不独立重判来源 |
| `taskbuilder/validation.py` | 输入权威的确定性 DraftReview | TaskDraft | DraftReview | 不阻断设计拥有的 solver/物性/时间候选 |
| `taskbuilder/compiler.py` | confirmed review 到唯一 TaskSpec v3 | DraftReview | TaskCompilation/TaskSpec | 排除未确认模型事实；不 author |
| `taskbuilder/messages_zh.py` | 稳定 TaskBuilder code 中文恢复文案 | code | message/recovery | 不改变 code/判定 |
| `tasks/__init__.py` | 公开任务合同导出 | import | TaskSpec/geometry/asset IO | 无校验复制 |
| `tasks/models.py` | Foundation 10 TaskSpec v3 规范合同 | YAML/model payload | TaskSpec | v2 不可进入 authoring |
| `tasks/geometry.py` | geometry/mesh intent 严格合同 | geometry payload | GeometryInput/MeshIntent | 不探测资产 |
| `tasks/io.py` | TaskSpec 加载、public asset hash/staging/snapshot | task path、asset root | TaskSpec/staged assets | 拒绝 symlink/路径逃逸，不猜内容 |
| `tasks/legacy.py` | 历史 TaskSpec v2 run adapter | manifested run artifact | LegacyTaskSpecV2 | 只读，禁止 author/resume/qualification |
| `validation/__init__.py` | 历史 validation compatibility 导出 | import | legacy models | 新代码不得依赖为验收 |
| `validation/legacy.py` | 旧 public-validation JSON 模型 | historical artifact | LegacyPublicValidationReport | 只读，不产生新状态 |
| `workflow/__init__.py` | workflow 公共状态/持久化/projection 导出 | import | workflow API | lazy import 防循环依赖 |
| `workflow/models.py` | stage/event/failure/resume 合同 | typed payload | workflow models | 无 I/O |
| `workflow/events.py` | append-only event 合同 | event payload | WorkflowEvent | 无存储 |
| `workflow/store.py` | fsync event 和独占 checkpoint | run dir/events/models | durable workflow artifacts | checkpoint 不替换，单 writer |
| `workflow/services.py` | coordinator 消费的 stage service protocol | WorkflowContext | StageOutcome/artifacts | 领域逻辑留在 service |
| `workflow/coordinator.py` | 声明式阶段顺序和终结 | stage services/context/store | CoordinatorOutcome | 不读取日志、不解释 CFD |
| `workflow/confirmation.py` | 逐字段确认和不可变 child | manifested parent、exact answers | ConfirmationRecords/child | 不 accept-all、不启动 OpenFOAM |
| `workflow/lineage.py` | resume/rerun 指纹、预算和 parent 验证 | parent run、current runtime/task/code | continuation/rerun records | 不重开 parent；不忽略不兼容 |
| `workflow/projection.py` | CLI/Desktop 共用的只读真实投影 | manifested workflow/facts/results/metrics | WorkflowProjection | metrics 损坏只 warning，不改终态 |

## 9. 减重等价性规则

任何代码或测试删除必须提交一份可核对的映射：

1. 被删符号/测试对应本文哪一个职责；
2. 删除后该职责由哪个唯一文件承担；
3. 输入和输出 contract 是否完全相同；
4. side effect owner 是否仍唯一；
5. 错误码、失败关闭和 provenance 是否保持；
6. 哪些聚焦测试证明行为等价；
7. 哪些全量/发行物/真实草稿门禁证明没有跨层回归。

允许的减重：

- 把集中实现按本文职责拆成内部模块；
- 合并完全相同的纯 helper；
- 用共享 fixture/参数化减少测试构造重复；
- 删除不存在调用方、没有独立 contract、没有 compatibility 责任的 dead code；
- 将历史文档退出当前权威阅读路径。

禁止以减重名义进行：

- 删除不同风险语义的回归测试；
- 在两层同时保留旧实现和新实现；
- 用宽泛 fallback 保持表面兼容；
- 合并来源协调、设计决策、case authoring、命令编译和执行；
- 让 CLI/Desktop/job worker 直接解释领域证据；
- 让模型获得资产权威、confidence、命令或验收 authority；
- 将 `NOT_RUN`、`INCOMPLETE` 或 deferred 状态改写为 PASS。

## 10. 架构变更流程

如果未来认为某项职责或能力边界需要改变，应按以下顺序执行：

1. 修改本文并说明旧边界为何不足；
2. 明确迁移的数据 contract、兼容期和删除条件；
3. 编写独立规格和实施计划；
4. 用小闭环验证新路径；
5. 删除旧路径并更新文件级目录；
6. 更新 `docs/current-state.md` 的证据范围。

普通 bugfix、性能优化和代码清理不得跳过第 1 步暗中改变架构。
