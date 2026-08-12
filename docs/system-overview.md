# FoamPilot 架构、运行流程与功能边界

## 1. 文档范围

本文介绍 FoamPilot 当前仓库中已经实现的规范运行路径。内容以
`foampilot` Python 包、命令行接口和 `NativeAgent.solve()` 状态机为准，
不包含历史兼容路径，也不把设计文档中的未来功能视为现有能力。

当前经过验证的运行目标是 **Foundation OpenFOAM v10**。

## 2. 系统定位

FoamPilot 是围绕 OpenFOAM 构建的单 Agent CFD 工作流，不是新的 CFD
求解器。两者的职责边界如下：

- OpenFOAM 负责网格工具、初始化工具、数值求解器和原生后处理程序；
- FoamPilot 负责把完整自然语言请求编译为结构化任务、选择求解器族、组织公开知识、分阶段调用
  模型编写 OpenFOAM case、确定性编译执行计划、约束执行、检查结果、有限修复和保存证据。

FoamPilot 从空 case 目录开始工作。Agent 编写的是 OpenFOAM case 文件和执行
计划，而不是 OpenFOAM 求解器的 C++ 源码。运行时直接调用本机已经安装的
OpenFOAM 工具和求解器。

FoamPilot 是独立可安装的 Python 工具包，不依赖原始 Foam-Agent、LangGraph、
FAISS、MCP、预先存在的目标 tutorial、Case renderer 或 `Allrun` 脚本。

## 3. 输入和输出

### 3.1 TaskSpec

`foampilot solve` 的直接输入是 YAML 格式的 `TaskSpec v3`。普通用户可以先通过
`foampilot task draft -> validate-draft -> compile`，把一份完整的中文或英文自然语言请求和
显式声明的公开附件转换成相同的规范 `TaskSpec`。`TaskSpec` 包含：

- 任务标识、标题和自然语言物理需求；
- Foundation OpenFOAM 版本；
- 最大尝试次数、总执行时间、MPI 核数和内存预算；
- 必须生成的结果；
- 公开验收要求和公开检查；
- 可选的公开输入文件及其 SHA256；
- Agent 不得访问的受保护路径。
- 可选的几何输入、显式长度单位、patch/region role 和网格质量意图。

TaskBuilder 不直接调用 OpenFOAM：模型只提取带来源的 `TaskDraft`，确定性 Validator 区分
blocking、confirmable 和 advisory，Compiler 只填充公开可见的低风险运行默认值。单位、物性、
边界值、初始条件、终止时间和工程容差等高影响事实缺失时不会被猜测，也不会进入 case generation。
当前提供的是三个可审计 CLI/Python 步骤，尚未提供持续聊天会话、交互表单或一条命令完成澄清与求解。

新的 authoring 只接受 v3；`TaskSpec v2` 只供历史 run 的只读 adapter 展示，不能重新进入
authoring、resume 或 qualification。Agent 能看到任务的物理需求、资源、输出要求和公开资产说明，但看不到
`public_checks`、受保护路径、qualification 私有规则或参考答案。

### 3.2 CaseBundle 与 ExecutionPlan v4

Case Author 一次生成不含执行权限的完整 `CaseBundle`：

```text
manifest   = 求解器、物理族、region、field、patch 和模型声明
files[]    = case 相对路径和完整文件内容
```

`CaseVerifier` 检查 bundle 是否忠实实现已冻结 CaseDesign。随后 `PlanCompiler` 从已冻结的
第一方扩展生成系统所有的 `ExecutionPlan v4`：它引用设计 SHA256 和 compiler identities，
并加入带 step_id、stage、可执行程序、参数、MPI 核数与超时的 typed commands。命令阶段包括
网格、网格检查、初始化、区域分解、求解、重构和后处理；模型不能提供或修改这些命令。计划中
不允许 shell 片段、`Allrun`、主机选择或任意外部路径。

### 3.3 运行结果

每次运行生成一个独立目录，保存任务、环境、路由、上下文、模型传输、执行计划、
每次尝试的 case、OpenFOAM 日志、验证报告、工作流事件和最终摘要。终态文件由
SHA256 manifest 覆盖，之后的修改能够被 `foampilot report` 检出。

## 4. 核心组件

| 组件 | 职责 |
| --- | --- |
| `taskbuilder` | 从自然语言和公开附件 metadata 提取事实、检查缺失信息并确定性编译 TaskSpec |
| `tasks` | 校验 TaskSpec、资源预算、公开资产和信息隔离规则 |
| `preprocessing` | 探测公开几何事实并从原生日志构造网格质量报告 |
| `environment` | 发现 Foundation OpenFOAM v10、可执行程序和本地运行条件 |
| `routing` | 根据任务事实、已安装程序和知识元数据确定求解器及物理族 |
| `simulation` | 解释意图、确定性解析需求、提议并冻结 CaseDesign、执行程序所有的 RiskGate |
| `knowledge` / `skills` | 提供经过审查的公开 OpenFOAM 知识和行为规范 |
| `context` | 按语义槽位选择有限知识，并装配通用及求解器族 Skill |
| `models` | 执行结构化模型调用、超时、重试、错误分类、追踪和熔断 |
| `authoring` / `agent` | 生成无命令 CaseBundle，并根据公开失败证据提出有限 RepairProposal |
| `plans` / `manifests` | 确定性编译 ExecutionPlan v4、定义 CaseManifest 和计划策略 |
| `inspection` | 检查设计一致性、路径、文件、命令和高置信度跨文件语义 |
| `runtime` | 通过 bubblewrap 或 audited host 后端执行 typed OpenFOAM 命令 |
| `validation` | 根据 TaskSpec 中的公开规则检查日志和写出的场 |
| `workflow` / `artifacts` | 记录事件、检查点、续跑关系、摘要和内容哈希 |
| `performance` | 从既有证据聚合耗时，并校验显式计划复用、派生缓存和 repair 阶段复用 |
| `qualification` | 在普通求解之后执行与 Agent 隔离的外部物理评测 |

## 5. 规范运行流程

```text
自然语言 + 显式公开附件（可选）
  -> TaskDraft -> 确定性 review -> TaskSpec
TaskSpec（也可直接提供）
  -> 环境发现
  -> 公开资产 staging 与 GeometryProbe
  -> 求解器族路由
  -> 动态知识和 Skill 装配
  -> SimulationIntent 与确定性 ResolvedRequirements
  -> CaseDesignProposal 与 RiskGate
  -> READY_TO_AUTHOR 后冻结 CaseDesign
  -> Case Author 生成 CaseBundle
  -> CaseVerifier 检查设计一致性
  -> PlanCompiler 生成 ExecutionPlan v4，或显式加载严格匹配的已验证 authority chain
  -> 计划与安全策略
  -> 空目录物化 case
  -> 静态与跨文件语义检查
  -> bubblewrap 或 audited host 执行 OpenFOAM
  -> 公开验证
  -> 必要时定向修复并重新执行
  -> 状态总结和产物哈希
```

### 5.1 运行前准备

真实求解前应先执行：

```bash
foampilot preflight --json
```

`preflight` 通过统一 resolver 检查 Foundation v10、工作目录和生产等价的完整 sandbox
launch。默认 `sandbox_preferred`；`sandbox_required` 不允许 host，`trusted_host` 是显式
宿主执行选择。audited host 与 bubblewrap 不具有相同安全性：host 后端虽保留 typed argv、
资源限制和日志，但没有 network/filesystem namespace。有效配置与逐字段来源分别写入
`runtime-config.json` 和 `runtime-config-provenance.json`；每次 case materialize/repair 后重新
产生 `execution-risk-report.json`，并在第一条 OpenFOAM 命令前写入
`execution-policy.json`。`solve` 会在 run 内保留完整 preflight 证据。

环境 source 使用临时隔离 HOME；命令发现、help 探测和 Runner 都绑定验证后的 absolute command
path。host 路线还会拒绝 `-case`/distributed-root 覆盖、绝对 argv、动态代码/库、外部 include、
`systemCall` 与 `timeActivatedFileUpdate`，且在 mesh cache 恢复后重新计算 case 风险。

### 5.2 自然语言任务构建（可选）

```bash
foampilot task draft --request-file request.md --output task-draft.yaml --json
foampilot task validate-draft task-draft.yaml --json
foampilot task compile task-draft.yaml --output task.yaml --json
```

Extractor 只能看到 request 正文和显式声明附件的相对路径、用途与 SHA256，不读取附件正文、
宿主任意目录或受保护 tutorial。事实的 `source` 和 `evidence` 由系统复核：原文中不存在的
`user_text`、无法对应附件 metadata 的 `public_asset` 会降级为未确认模型推断。完整请求可以直接
编译；缺少高影响事实时返回稳定代码和中文恢复说明，用户或上游界面补充后再产生新的 draft。
TaskDraft 属于求解前可编辑状态，不创建 run，也不计入 mesh/solver 失败率。

### 5.3 任务校验与运行目录创建

CLI 首先把 YAML 解析为严格的 `TaskSpec`。字段缺失、路径非法、资源参数非法或
公开内容泄漏受保护路径时，任务在模型调用和 OpenFOAM 执行前被拒绝。

合法任务会获得唯一的运行目录。之后的阶段通过 `workflow-events.jsonl` 按阶段记录，
不会共用另一个任务的 case 或状态。

### 5.4 环境发现与能力路由

FoamPilot 读取本机 OpenFOAM 发行版、版本和可执行程序清单。路由器使用：

- TaskSpec 中明确的求解器和物理事实；
- 本机实际安装的求解器；
- 公开的求解器族知识元数据。

路由置信度由确定性程序根据证据计算，不接受模型自报的置信度。任务明确指定且
环境支持的求解器可以得到高置信度；唯一兼容候选可以得到中置信度；缺少关键物理
信息或存在多个无法区分的候选时，会在完整 case 生成前停止。只有候选集合确实
含糊时，路由器才允许一次受限模型建议。

### 5.5 动态上下文装配

确定求解器族之后，ContextAssembler 按语义槽位检索公开知识，主要包括：

- 求解器族；
- 网格模式；
- 边界条件；
- 物理与输运模型；
- 启动和数值格式；
- 可选的并行执行；
- 修复阶段的错误知识。

每个槽位最多选取一条知识；没有可靠匹配时保留为空，而不是用无关条目填满提示。
运行时采用“通用 Skill + 至多一个物理族 Skill”；任务声明 geometry/mesh 时再增加一个
mesh Skill。通用 Skill 约束原生 case 编写，物理族 Skill 只补充不可压缩、可压缩、VOF、浮力/CHT、固体或标量/势场中
与当前 solver 相符的一类判断。窄求解器没有可靠映射时只加载通用 Skill，不叠加多个 Skill。

repair 阶段使用公开验证反馈和失败日志尾部重新选择 error playbook；失败日志只进入
error-playbook 检索槽位，不会改变生成阶段已经确定的其他语义槽位。

几何任务在路由前生成 `geometry-facts.json`。STL/OBJ 单位只能来自 TaskSpec；hash、路径、
surface name 或 patch role 无法确定时会在零模型调用处快速失败。显式 mesh strategy 优先于
prompt 关键词；Gmsh 只有被环境发现后才能进入 typed plan。

### 5.6 完整 case 生成

在完整 case 生成前，FoamPilot 用两个有界、schema 独立的模型阶段分别生成
`SimulationIntent` 和不含文件/命令的 `CaseDesignProposal`。确定性 Requirement Resolver
将网格事实、用户事实和 capability requirement 合并；RiskGate 再产生四种状态：

- `READY_TO_AUTHOR`：冻结 `case-design.json`；
- `CONFIRMATION_REQUIRED`：用 `CONCRETE_CONFIRMATION_REQUIRED` 要求逐字段候选确认；
- `INFORMATION_REQUIRED`：缺少无安全默认值的信息，或权威事实冲突；
- `CAPABILITY_UNAVAILABLE`：当前能力注册表无法实现设计。

后三种会以 `workflow_state=DEFERRED`、`native_status=null` 固化，因此不是 CFD 求解失败。
模型不能自报 confidence；系统禁止 accept-all、continue-anyway 和高影响风险 override。
`foampilot questions` 展示字段、理由和候选，`foampilot confirm` 只接受 exact candidate/value，
为每个字段写独立记录并创建不可变 child。确认动作本身不执行 OpenFOAM。

只有冻结 CaseDesign 才会传给 Case Author。ModelGateway 发起一次逻辑生成请求，要求模型同时
返回所有 case 文件和 CaseManifest，形成不含命令的 CaseBundle。底层传输在阶段 deadline 和
次数预算内可以对网络中断、服务过载或限流进行重试，但不会无限等待。

CaseVerifier 校验 authored manifest/files 的 solver、物理族、稳/瞬态、region role、字段和
网格 patch 不得与冻结设计及权威网格事实矛盾。PlanCompiler 只从 CaseDesign 冻结的第一方
mesh/solver contributor 生成 ExecutionPlan v4 命令，并记录设计 hash 与 contributor 版本。
`foampilot plan` 使用相同链路，但在 case 物化与 Runner 前以 `PLAN_READY` 结束。

Model backend 只负责一次交换；Gateway 负责错误分类、重试、deadline、
传输追踪和熔断。qualification 的多个 worker 可以共享 Gateway 和熔断状态，但各
算例的任务、时间预算、case、日志和评测状态相互独立。

完全相同的 TaskSpec 可以通过 `--reuse-verified-plan SOURCE_RUN` 显式选择一个已经 manifested、
网格检查通过且目标 solver 正常结束的 authority chain。该路径不创建 Gateway；source attempt
必须同时包含一致的 CaseDesign、CaseBundle、conformance、compiler identities、ExecutionPlan v4
和 run result。任何任务、资产、版本、solver、编译器身份或资源不兼容都以
`PLAN_REUSE_REJECTED` 结束，不会静默调用模型。普通 live solve 和 qualification 默认仍生成新计划。

### 5.7 计划编译与检查

冻结设计后的产物依次经过：

1. Case Author 返回 CaseManifest 和全部文件组成的 CaseBundle；
2. CaseVerifier 检查 bundle 与 CaseDesign、权威网格事实和扩展契约；
3. PlanCompiler 按 contributor/stage/local order 确定性生成 typed commands；
4. ExecutionPlan v4 schema 和安全策略校验；
5. case 文件物化，并执行原生文件检查和高置信度语义检查。

Case Author 不生成命令，因此不存在模型 MPI wrapper、stage 或 step_id 的兼容性规范化。
PlanCompiler 不猜测求解器、主机、核数或未知参数；这些值必须来自冻结设计、任务预算和注册
贡献器。贡献器输出冲突、缺失命令或未安装 executable 会在 Runner 前失败。

检查器阻止路径逃逸、shell 语法、受保护路径引用、缺失文件、明显损坏的 OpenFOAM
文件、MPI 预算超限和已登记求解器族中的确定性跨文件矛盾。它不替 Agent 选择网格、
离散格式或物理模型，也不以机械规则证明 CFD 策略正确。

由网格、初始化程序或求解器创建的场不要求在执行前已经存在；Agent 或公开资产
声明创建的文件必须存在。

### 5.8 OpenFOAM 执行

Runner 按计划顺序逐条执行命令：

- 每条命令都是“可执行程序 + 参数”，不接受 Agent 编写的 shell；
- case 目录在沙箱内挂载为 `/case`；
- 沙箱关闭网络；
- MPI 启动由 Runner 根据 `mpi_ranks` 统一构造；
- 每步有独立超时，整体受 TaskSpec 资源预算限制；
- stdout、stderr、返回码、开始时间、结束时间和超时状态全部记录。

因此系统贡献器可以安排网格生成、`checkMesh`、场初始化、求解和原生后处理；Case Author
没有执行权限，实际执行始终由 Runner 控制。某一步失败后，后续命令不再继续。

`--derived-cache CACHE_ROOT` 显式启用内容寻址的 GeometryFacts/polyMesh 缓存。只有几何、资产、
mesh intent、网格文件和命令、region 及工具版本组成的依赖键完全相同时才命中。命中会复制
派生产物并跳过网格生成，但当前 Runner 仍重新执行 `checkMesh`、solver 和验证；动态网格或
依赖不明确时退回原路径。

### 5.9 公开验证

执行完成或静态检查失败后，Evaluator 根据 TaskSpec 中的公开检查生成
`public-validation.json`。检查对象包括：

- 命令是否成功完成；
- 网格检查是否通过；
- 目标求解器是否实际启动和正常结束；
- 必需输出是否存在；
- 日志中的残差、连续性或场统计；
- 写出场的范围、积分或其他公开物理要求。

返回码为零只表示程序正常退出，不自动等价于收敛、物理正确或 qualification
通过。只有所有公开检查通过，普通求解状态才是 `PUBLIC_VALIDATION_PASS`。
一次 validation 对每个 step 日志只解析一次，并把结构化摘要复用于全部公开检查；失败 step
直接走失败层分类，不为后续物理检查重复扫描大日志。

执行过原生网格步骤的 attempt 还会写出 `mesh-quality-report.json`，其中的 cell 数、
non-orthogonality、skewness 等观测与 TaskSpec 阈值分开保存。网格命令失败使用
`MESH_FAILED`，网格已生成但不满足公开阈值使用 `MESH_QUALITY_FAILED`。

### 5.10 定向修复

如果检查失败且 TaskSpec 尚有尝试预算，FoamPilot 先由确定性组件完成：

- 对原始 OpenFOAM failure 分类；
- 从分类结果、日志、公开验证和文件依赖构造 `RepairScope`；
- 按上下文预算选择完整文件、匹配块、首尾摘要、结构或元数据表示；
- 生成不含 protected/public asset 原文的 repair 状态快照。

自动 repair 只接受 solver/validation 层已分类的 `numerical_instability`，且冻结 CaseDesign
必须包含非空 `NumericalRepairEnvelope`。repair 模型在 `RepairScope` 内返回不含命令的
`RepairProposal`；每个设计字段、操作方向、上下界、目标文件和 dictionary keyword 都必须由
envelope 预先声明。授权后生成新的派生 CaseDesign 与 CaseBundle，再次通过 CaseVerifier、
PlanCompiler、安全策略、物化、静态检查、OpenFOAM 执行和公开验证。模型不能插入、替换或删除
typed command。

`repair_policy.automatic_numerical_repair` 默认开启但可关闭。关闭时，或遇到物理、能力、solver、
mesh、envelope 外变更、无实质变化、重复 failure、环境错误和预算耗尽时，当前 run 正常终结为
失败，并在 summary 中给出稳定 code、具体缘由和 rerun/补充确认建议；不得泛化放行。

新 attempt 会根据 repair 修改集合保守选择最早重跑阶段。求解字典变化可以复用前序网格，
初始场变化从 initialize 开始；网格、patch、include、动态网格、多区域或并行拓扑依赖变化
则完整重跑。复用只复制允许的前序产物，并对 parent/child 内容记录哈希；当前 `checkMesh`
不会被跳过。

mesh failure 不属于自动数值 repair 范围。改变网格策略、网格文件、patch 或 zone 必须由新的
权威输入和 CaseDesign 进入 rerun；repair 模型不能修改 mesh/check command。

仓库随附任务的 `max_attempts` 当前均为 2，因此这些规范任务最多执行一次初始生成
和一次修复尝试。

### 5.11 终态归档

运行结束时写入 `summary.json`，随后生成 `artifact-manifest.json`。manifest 记录
运行目录内每个文件的大小和 SHA256。`foampilot report RUN_DIR --json` 会重新计算
这些信息，并报告缺失文件、额外文件或哈希变化。

## 6. 模型后端中断与严格续跑

生成或修复阶段发生可重试的模型后端中断时，FoamPilot 可以把当前运行结束为
`DEFERRED`，而不是把它改写成 OpenFOAM 失败。恢复命令为：

```bash
foampilot resume PARENT_RUN --run-root NEW_RUN_ROOT --json
```

续跑具有以下约束：

- parent run 必须已经完成 manifest 固化且哈希验证通过；
- 只支持从被中断的生成或修复模型阶段继续；
- 新运行是独立 child run，不重新打开或修改 parent；
- TaskSpec、公开资产、模型/backend 策略、包内容、知识、Skill、OpenFOAM 目标和
  可执行能力必须满足严格兼容性；
- 每个模型阶段最多创建两个 continuation，整个 lineage 最多使用七次真实传输；
- 修改代码、任务、知识、Skill、模型或策略后必须作为新运行，而不是 strict resume。

续跑不支持从已经中断的 OpenFOAM 进程内部恢复，也不等价于 OpenFOAM 自身的
restart 功能。

## 7. 状态和失败语义

`RunSummary` v2 分开表达三个问题：

| 字段 | 含义 |
| --- | --- |
| `workflow_state` | 整个工作流是 `COMPLETED`、`FAILED` 还是 `DEFERRED` |
| `native_status` | 已经发生的最新 OpenFOAM/公开验证结果；未进入 native 阶段时可以为空 |
| `primary_failure` | 算例首先在哪个任务、计划、网格、求解或验证层失败 |
| `terminal_blocker` | 当前为何无法继续，例如模型后端暂时不可用 |

主要失败层包括：

- `REQUEST_INCOMPLETE`：TaskSpec 不完整或非法；
- `ROUTING_UNRESOLVED`：无法可靠确定求解器族；
- `BLOCKED_ENVIRONMENT`：本机运行环境不满足要求；
- `CASE_GENERATION_FAILED`：模型输出或 case 物化失败；
- `GENERATION_INVALID`：模型返回内容最终不能形成符合冻结设计的 canonical CaseBundle；这是
  Agent authoring 失败，不是模型后端或环境阻断；
- `PLAN_INVALID`：执行计划违反 schema 或安全策略；
- `STATIC_INSPECTION_FAILED`：原生 case 或跨文件语义检查失败；
- `MESH_FAILED` / `INITIALIZATION_FAILED` / `SOLVER_FAILED` /
  `POSTPROCESS_FAILED`：相应 OpenFOAM 阶段失败；
- `PUBLIC_VALIDATION_FAILED`：求解执行结束但公开检查未通过；
- `PUBLIC_VALIDATION_PASS`：普通求解的全部公开检查通过。

例如，算例首先发生 `SOLVER_FAILED`，随后修复请求遭遇模型后端过载时，
`primary_failure` 仍是 solver，`terminal_blocker` 单独记录 backend，避免把环境
或模型服务故障误记为 CFD 错误。

## 8. 普通求解、qualification 和离线改进

### 8.1 普通求解

`foampilot solve` 的目标是完成 Agent 编写、原生执行和 TaskSpec 公开验证。它不读取
官方标准答案，也不代表结果已经通过外部基准比较。

### 8.2 Qualification

`foampilot qualify` 在规范求解路径之后增加独立的外部物理评测：

- case authoring 和 repair 模型看不到 qualification 规则与参考数据；
- 评测器在完成 case 的临时副本上工作，避免修改已经固化的运行产物；
- 官方题目中的目标 tutorial 不作为 Agent 输入；
- `PUBLIC_VALIDATION_PASS` 与 qualification `PASS` 分开统计。

qualification 用于评估 Agent 独立编写 case 的能力，不是普通用户求解的必要步骤。

### 8.3 离线受控改进

`foampilot improve` 可以根据冻结的失败运行和 qualification 报告生成候选学习项，
再比较优化前后的评测结果。该流程：

- 不在 `solve()` 内自动修改知识库或 Skill；
- 不自动提升候选规则；
- 只允许在冻结评测之后，把官方 example 作为开发者侧教师参考；
- 只沉淀可泛化原则，不复制完整 case、目标几何、官方路径或 golden 数值。

## 9. 主要运行产物

一个完整运行通常包含：

```text
task.yaml
environment.json
capability-profile.json
agent-context.json
agent-status-author-01.json
agent-status-repair-01.json    # 仅在发生修复时
resume-compatibility.json
model-attempts.jsonl
model-configuration.json
performance-context.json
performance-summary.json
authored-execution-plan.json
plan-normalization.json
execution-plan.json
workflow-events.jsonl
failure-classification-attempt-01.json  # 仅在失败时
repair-scope-attempt-01.json            # 仅在进入修复时
repair-patch-attempt-01.json            # 仅在形成有效补丁时
checkpoints/
attempt-01/
  execution-plan.json
  generation-trace.json
  static-inspection.json
  run-result.json
  mesh-cache.json             # 显式缓存运行中可选
  execution-reuse.json        # 网格/repair 复用时可选
  public-validation.json
  case/
    0/
    constant/
    system/
    .foampilot/logs/
attempt-02/                   # 仅在再次尝试时
summary.json
artifact-manifest.json
```

并非每个终态都会出现全部文件。例如在模型生成前失败时不会有 attempt 目录，在从未
进入 OpenFOAM 时也不会有 `run-result.json`。

## 10. 当前能力边界

### 10.1 已具备的能力

FoamPilot 当前可以：

- 把信息完整的中文或英文自然语言请求和声明附件编译为规范 TaskSpec；
- 在高影响事实缺失时停止在求解前，并给出稳定代码、中文解释和恢复建议；
- 针对 Foundation OpenFOAM v10，从空目录生成完整单区域或多区域 case；
- 根据 TaskSpec 和公开知识选择已安装的求解器族；
- 生成网格、边界条件、物性、数值格式、初始化和控制字典；
- 调用本机 OpenFOAM 网格、初始化、求解和后处理工具；
- 按任务预算执行串行或有限 MPI 求解；
- 在执行前阻止高置信度结构错误和危险命令；
- 解析执行日志并检查写出结果；
- 根据公开失败证据进行有限修复；
- 在模型后端可重试中断后通过不可变 child run 严格续跑；
- 对完全相同 TaskSpec 显式复用已验证 ExecutionPlan，并以零模型调用重新执行；
- 通过显式内容寻址缓存复用严格匹配的几何事实和网格派生产物；
- 根据 repair 修改集合复用未受影响的前序 stage，同时保留当前网格检查；
- 为每个 run 生成可复算的阶段、模型、native 和复用性能摘要；
- 对官方题库式任务执行盲测 qualification，并保留可审计证据；
- 将冻结失败转化为人工审查的通用改进候选。

### 10.2 不保证或尚未具备的能力

FoamPilot 当前不保证：

- 模型每次都生成语法、数值和物理上正确的 case；
- `PUBLIC_VALIDATION_PASS` 等价于高精度、充分收敛或工程可用；
- 覆盖所有 OpenFOAM 求解器、模型、网格工具和耦合场景；
- 支持 ESI OpenFOAM、其他 Foundation 版本或任意第三方 solver；
- 自动编写、编译新的 OpenFOAM C++ 求解器；
- 维护多轮聊天会话，或在 CLI 内交互式收集并确认缺失事实；
- 对求解中断执行通用 checkpoint/restart；
- 自动生成工程级网格质量方案、网格无关性研究或不确定性分析；
- 自动将失败经验写入正式知识库或 Skill；
- 生产服务级的可用性、调度、权限管理和多人协作。

Performance v1 已进入普通 `solve` 路径，但所有跨 run 复用都必须由用户显式启用；系统不做
模糊 TaskSpec 匹配、不自动扫描历史 run，也不把 warm path 计入 qualification 泛化结果。
详见 [Performance v1 规格](design/performance-v1-design.md) 与
[实施报告](reports/2026-08-05-performance-v1.md)。

## 11. 最小使用入口

```bash
foampilot preflight --json
foampilot validate TASK.yaml --json
foampilot solve TASK.yaml --run-root RUNS --json
foampilot report RUNS/RUN_DIR --json
```

如果运行因可重试的生成或修复 backend 故障进入 `DEFERRED`：

```bash
foampilot resume RUNS/PARENT_RUN --run-root RUNS --json
```

进一步的命令和输入格式见
[快速开始](independent-agent-quickstart.md)，内部模块和数据契约见
[Architecture](architecture.md)，评测口径见
[受控评测](qualification.md)。
