# FoamPilot 契约优先的 Agent 架构设计规格

状态：设计已批准，等待实施计划

日期：2026-08-12

目标运行时：Foundation OpenFOAM 10

## 1. 背景与结论

FoamPilot 已经证明能够完成以下真实闭环：

```text
结构化任务
→ 模型编写原生 OpenFOAM case
→ 安全执行
→ 公开验证
→ 不可变产物
```

provided `polyMesh` 多孔阻塞算例进一步证明了现有 Runner、typed command、安全策略、
OpenFOAM 执行和产物保存底座可以保留。同时，该算例暴露出五类问题：

1. 原生 `polyMesh` 目录没有被作为一等原子资产；
2. 输入网格事实提取不足，模型承担了本应由程序完成的读取和判断；
3. 验收要求、求解前观测需求和求解后处理没有形成独立闭环；
4. 一次模型调用耦合了意图理解、方案设计、文件编写和执行计划；
5. 编排器承担领域判断，多个模块重复解释命令和日志。

本设计采用“契约优先的演进式重构”：保留已经稳定的运行、安全、证据、恢复和 Desktop
底座，增加通用的资产、事实、意图、方案、风险、观测、执行证据和报告契约，并将模型推理
拆成串行且职责单一的阶段。

最终责任边界为：

> Agent 负责工程推理和原生 case authoring；程序负责输入前处理、权威事实、风险门禁、
> 计划编译、安全执行、证据提取和结果证明。

## 2. 适用范围与非目标

### 2.1 通用性边界

核心架构面向 FoamPilot 当前支持和近期计划支持的大部分 CFD/CAE 场景，不包含特定算例的
patch 名称、cellZone 名称、字段名称、求解器或后处理指标。

首期实现只保证 Foundation OpenFOAM 10。网格来源、物理族、求解器族、观测指标和未来运行时
通过带版本的适配器扩展。

### 2.2 不建设万能 OpenFOAM 解释器

本设计不建设能够完整解析、生成和重写所有 OpenFOAM 字典的万能 CaseIR，也不把 FoamPilot
变成每个算例对应一个模板的 renderer。Agent 继续编写完整的原生 OpenFOAM case；确定性系统
只验证已登记且能够可靠证明的关系。

### 2.3 其他非目标

- 不引入第二套长期 solve 状态机；
- 不采用逐文件模型调用或逐文件模型 reviewer；
- 不让第三方插件直接调用 Runner 或修改 Workflow 状态；
- 不承诺任意 OpenFOAM 时间步的通用断点续算；
- 不在本规格中实现远程/HPC 和内嵌三维可视化；
- 不因一个多孔算例而把多孔模型写入通用核心。

## 3. 核心设计原则

### 3.1 单一事实来源

- 资产完整性只来自 `AssetBundle`；
- 输入网格事实只来自 `InputMeshFacts`；
- 执行后网格事实只来自 `ExecutedMeshFacts`；
- 仿真意图只来自已验证的 `SimulationIntent`；
- 物理与数值设计只来自冻结的 `CaseDesign`；
- 命令事实只来自 `ExecutionPlan`；
- 执行事实只来自 `RunFacts`；
- 通过或失败结论只来自 `ResultReport` 或 `FailureReport`。

下游模块不得再次读取原始提示词、网格或日志，独立重建已经存在的权威事实。

### 3.2 来源与语义分离

同一个对象名称不能自动决定其物理意义：

| 信息类型 | 示例 | 权威来源 |
| --- | --- | --- |
| 网格事实 | 存在某个 cellZone 且包含 N 个单元 | 程序读取网格 |
| 用户语义 | 该 cellZone 表示多孔流体区 | 用户文本或逐项确认 |
| 工程决策 | 使用某个区域模型和系数 | 冻结的 CaseDesign |

### 3.3 模型不能自我授权

模型可以提出候选、依据、不确定项和影响等级，但不能通过自报 confidence 放行任务。
`RiskGate` 依据来源、完整性、冲突、影响等级和已安装能力确定状态。

### 3.4 编排不解释领域事实

`Coordinator` 只调用阶段、推进状态、持久化 checkpoint、处理取消和恢复。它不得包含特定
物理族、求解器、网格格式、日志标记或验收指标判断。

### 3.5 执行底座保持统一

所有执行最终编译成统一 typed `ExecutionPlan`，继续经过现有 executable allowlist、风险
扫描、bubblewrap/audited-host 策略、预算、取消和不可变 attempt 机制。

## 4. 目标数据流

```text
用户提示词 + 公开资产
        ↓
AssetIngestor Registry
        ↓ AssetBundle + AssetFacts
EnvironmentInspector
        ↓ CapabilityFacts
MeshInspector Registry
        ↓ InputMeshFacts
IntentInterpreter
        ↓ SimulationIntent
RequirementResolver
        ↓ ResolvedRequirements + Uncertainties
CaseDesigner
        ↓ CaseDesignProposal
RiskGate
        ├─ READY_TO_AUTHOR
        ├─ CONFIRMATION_REQUIRED
        ├─ INFORMATION_REQUIRED
        └─ CAPABILITY_UNAVAILABLE
        ↓ CaseDesign（冻结）
ObservationPlanner
        ↓ ObservationPlan
CaseAuthor
        ↓ CaseBundle
CaseVerifier + PlanCompiler
        ↓ VerifiedExecutionPlan
Runner
        ↓ RawRunEvidence
EvidenceExtractor
        ↓ RunFacts + ExecutedMeshFacts
PostProcessor Registry
        ↓ DerivedMetrics
AcceptanceEvaluator
        ↓ ResultReport / FailureReport
```

## 5. 模块职责

| 模块 | 唯一职责 | 明确不负责 |
| --- | --- | --- |
| `AssetIngestor` | 识别、校验、哈希并冻结资产包 | 不解释物理意义 |
| `MeshInspector` | 确定性提取网格实体、规模和拓扑事实 | 不猜测 patch/zone 的物理角色 |
| `IntentInterpreter` | 把提示词转换成带来源的意图和未知项 | 不选择数值格式、不写 case |
| `RequirementResolver` | 检查事实完整性、冲突和必要信息 | 不用默认值掩盖关键缺失 |
| `CaseDesigner` | 提议求解器、模型、材料、边界、时间和数值方案 | 不写原生文件、不执行 |
| `RiskGate` | 依据系统规则决定是否放行 | 不接受模型自报 confidence |
| `ObservationPlanner` | 把输出和验收意图编译成最小证据采集计划 | 不判定最终通过 |
| `CaseAuthor` | 根据冻结设计编写完整相关 case 文件 | 不重新设计物理方案 |
| `PlanCompiler` | 根据能力描述和 manifest 生成 typed commands | 不生成 OpenFOAM 字典 |
| `CaseVerifier` | 检查 CaseBundle 是否忠实实现 CaseDesign | 不执行求解 |
| `Runner` | 执行已验证命令并实施资源、安全和取消策略 | 不解释 CFD 结果 |
| `EvidenceExtractor` | 一次性解析原生输出并形成 `RunFacts` | 不做通过/失败决策 |
| `PostProcessor` | 从事实和写出数据计算派生指标 | 不改变 case |
| `AcceptanceEvaluator` | 按验收契约形成结论 | 不重新解析原始日志 |
| `RepairCoordinator` | 根据失败层选择允许的修复路径 | 不绕过 RiskGate |
| `Coordinator` | 推进状态和持久化工作流 | 不拥有 CFD 领域判断 |

## 6. 资产与网格事实

### 6.1 原子目录资产

`polyMesh` 必须实现为 `OpenFOAMMeshBundle`，而不是一组彼此无关的文件。bundle 记录目标
region：默认 region 安装到 `constant/polyMesh`，命名 region 安装到
`constant/<region>/polyMesh`。最小必需逻辑成员为：

```text
constant/polyMesh/
├── points
├── faces
├── owner
├── neighbour
└── boundary
```

以下可选成员只要存在就必须保留：

```text
cellZones
faceZones
pointZones
sets/
```

`OpenFOAMMeshBundle` 必须：

- 检查必需成员、普通文件类型、路径安全、大小限制和链接策略；
- 对每个逻辑成员接受未压缩文件或对应的 `.gz` 文件，但拒绝两种形式同时出现；
- 对每个成员计算 SHA256，并计算规范目录 manifest hash；
- 根据冻结的 region 身份原子化 staging 到受控目标目录；
- 禁止模型生成、覆盖或删除 bundle 内文件；
- 在 repair 和复用前重新核对 manifest；
- 明确记录可选 zone 文件是否存在，而不是把缺失误报为空 zone。

### 6.2 `PolyMeshInspector`

第一方 `PolyMeshInspector` 在模型调用前产生紧凑、结构化的 `InputMeshFacts`，至少包括：

- points、faces、internal faces 和 cells 数量；
- bounding box、几何尺度和声明长度单位；
- patch 名称、OpenFOAM 类型和面数；
- cellZone、faceZone、pointZone 名称和元素数量；
- region 信息；
- `empty`、wedge 等维度相关边界观测；
- 静态索引范围、owner/neighbour 和边界覆盖一致性；
- 源 bundle manifest hash、解析器版本和 warning。

模型只看到有界 `InputMeshFacts`，不读取大体积的 points/faces/owner/neighbour 原文。

### 6.3 静态与动态网格事实的时序

必须区分：

- `InputMeshFacts`：从已提供资产中可在生成前确定的事实；
- `ExecutedMeshFacts`：网格导入/生成和系统受控 `checkMesh` 后获得的事实。

对于 provided 原生 `polyMesh`，系统可以在模型 case authoring 前，在受控 probe workspace 中
写入最小系统文件并调用已发现的 canonical `checkMesh`，将结果附加为 pre-authoring
`ExecutedMeshFacts`。该命令由系统构造，不由模型提供。

对于 blockMesh、snappyHexMesh、Gmsh 或其他尚未生成的网格，只能先产生输入资产事实；实际
网格事实必须在网格命令执行和 `checkMesh` 后生成，不能伪装成预处理事实。

## 7. 通用核心与扩展环境

### 7.1 标准扩展协议

核心预留以下带版本的接口：

```text
AssetAdapter
MeshInspector
PhysicsFamilyExtension
SolverFamilyExtension
ObservationExtension
RuntimeTargetAdapter
```

每个扩展提供机器可读的 `CapabilityDescriptor`：

```yaml
extension_id: foampilot.physics.incompressible
extension_version: 1.0.0
protocol_version: 1
supported_targets:
  - distribution: foundation
    versions: ["10"]
required_executables: []
input_contracts: []
output_contracts: []
compatible_extensions: []
incompatible_extensions: []
semantic_validators: []
evidence_extractors: []
```

`CapabilityRegistry` 只将协议兼容、目标版本匹配、依赖满足的扩展公布为本机真实能力。

### 7.2 插件权限边界

扩展可以定义 schema、提取确定性事实、提供有界知识/Skill、验证设计和 case、声明证据解析器、
后处理器及允许的 repair 字段。

扩展不能：

- 绕过 `RiskGate`、`PlanCompiler` 或 Runner 安全策略；
- 直接修改 Workflow 状态；
- 直接执行任意命令或注入 shell；
- 访问未声明资产；
- 把无法验证的能力或结果标记为通过。

首期只启用 FoamPilot 包内第一方扩展。公共协议现在冻结为 v1；未来第三方 Python package
entry point 默认禁用，只有进入显式信任 allowlist 后才能启用。

### 7.3 首期扩展范围

第一方实现首先迁移当前 Foundation v10 能力：

- 原生 `polyMesh`、surface、Gmsh 和参数化网格资产；
- 不可压缩、可压缩、VOF/多相、浮力/传热、CHT/多区域、固体及标量族；
- 当前已登记的 solver family；
- 残差、连续性、流量、压差、区域平均值、力和热通量观测。

多孔介质、旋转区域和其他区域模型作为 `PhysicsFamilyExtension` 或组合扩展，而非核心字段。

## 8. 串行模型推理

### 8.1 意图理解

`IntentInterpreter` 输入用户原文、`AssetFacts`、`InputMeshFacts` 和可用能力类别，只输出：

- `SimulationIntent`；
- 未明确的信息；
- 每项结论的来源和文本依据。

该阶段不得写 OpenFOAM 文件、生成执行命令或选择具体数值格式。

### 8.2 方案设计

`CaseDesigner` 输入已验证意图、网格事实、Capability Registry 以及选中扩展提供的有限知识和
Skill，输出不含文件正文的 `CaseDesignProposal`：

```yaml
solver_family: ...
physical_models: []
materials: []
boundary_designs: []
initial_conditions: []
time_design: ...
numerical_design: ...
region_models: []
uncertainties: []
alternatives: []
reasoning_evidence: []
```

### 8.3 case authoring

只有通过门禁并冻结的 `CaseDesign` 可以进入 `CaseAuthor`。模型一次返回完整相关
`CaseManifest + files`，不能拆成每个字典一次调用。

`CaseAuthor` 不得改变已经冻结的求解器、物理模型、材料、边界、区域语义、时间条件或用户
确认值，也不得自由设计执行命令。`PlanCompiler` 根据能力描述和 manifest 确定性生成命令。

### 8.4 阶段恢复

每个模型阶段具有独立 schema、deadline、调用预算、checkpoint、输入/输出 hash 和失败代码。
后续模型调用中断时复用已经冻结的前序结果，不重新解释提示词或做新的物理决策。

## 9. 风险分级与用户确认

### 9.1 门禁状态

| 状态 | 条件 | 行为 |
| --- | --- | --- |
| `READY_TO_AUTHOR` | 必要事实完整、无冲突，高影响值具有权威来源 | 自动继续 |
| `CONFIRMATION_REQUIRED` | 存在具体、合理的中高影响候选值 | 请求逐项确认具体值 |
| `INFORMATION_REQUIRED` | 关键事实缺失、冲突或无唯一候选 | 请求补充事实 |
| `CAPABILITY_UNAVAILABLE` | 当前受信任扩展或环境不支持 | 生成明确能力错误 |

### 9.2 禁止泛化放行

高影响事实缺失时，不提供“仍然继续”“全部采用模型建议”“承担风险继续”等泛化放行操作。
用户必须确认某个具体候选值，或补充新的事实。

确认记录至少包含：

```text
question_id
field_path
previous_candidate
confirmed_value
source=user_confirmation
evidence
answered_at
```

界面可以一次提交多项，但每个高影响字段必须分别保存确认记录。

几何单位、区域作用范围、必要材料参数和无法唯一确定的入口/出口角色等硬缺失，没有候选值时
只能进入 `INFORMATION_REQUIRED`，不能通过确认“继续”绕过。

## 10. 观测、后处理与验收

验收要求不与 case authoring 或 Runner 混合，但证据采集需求必须在求解前编译：

```text
AcceptanceIntent
→ ObservationPlan
→ case authoring / 系统受控观测配置
→ Runner
→ RunFacts
→ PostProcessor
→ DerivedMetrics
→ AcceptanceEvaluator
```

`ObservationPlan` 应区分：

- 可从 solver 原生日志获得的量；
- 可从写出时间目录离线计算的量；
- 必须在求解前配置采样位置、区域或输出频率的量；
- 只能作为观测值、没有工程容差因而不能判定通过的量。

`PostProcessor` 只计算指标；`AcceptanceEvaluator` 只依据结构化 `RunFacts`、
`DerivedMetrics` 和验收契约判定。两者都不得各自重新解析原始 solver 日志。

## 11. Repair 策略

```text
RunFacts
→ 确定性 FailureClassifier
→ RepairScope
├─ 机械修复：确定性执行
├─ 数值修复：模型生成 RepairProposal → NumericalRepairEnvelope 校验
└─ 物理修复：返回用户逐项确认
```

### 11.1 自动修复范围

- 机械性修复：Foundation v10 文件位置、字典语法、字段名称和命令顺序等不改变物理语义的
  修复可以自动执行；
- 数值性修复：只有 `CaseDesign.NumericalRepairEnvelope` 明确允许的字段、方向和范围可以
  自动修改；
- 物理性修复：求解器、物性、边界条件、初始条件、区域模型及其系数、湍流模型和最终模拟
  时间等中高影响变更必须再次逐项确认；
- 未登记字段和超出 envelope 的数值修改一律停止。

自动数值修复默认开启，但用户可以关闭：

```yaml
repair_policy:
  automatic_numerical_repair: true
  numerical_envelope: ...
```

关闭后仍执行安全检查、静态检查、确定性诊断和机械修复。遇到数值失败时，当前 Run 正常终结
为失败，不自动修改数值参数；用户之后可以基于冻结证据创建 repair continuation。

### 11.2 RepairProposal

模型只能提交与失败范围相关的设计差异和完整目标文件替换，不能提交命令或重新生成整个 case。
授权的 proposal 应用后生成派生 CaseDesign/CaseBundle，并重新经过：

```text
CaseVerifier → PlanCompiler → RiskGate → Runner
```

repair 不得改变冻结设计或绕过首次生成时的风险门禁。

## 12. 工作流、可观察性与恢复

### 12.1 状态机

```text
INGESTING_ASSETS
→ INSPECTING_INPUT
→ INTERPRETING_INTENT
→ RESOLVING_REQUIREMENTS
→ DESIGNING_CASE
→ WAITING_FOR_INFORMATION / WAITING_FOR_CONFIRMATION
→ PLANNING_OBSERVATIONS
→ AUTHORING_CASE
→ VERIFYING_CASE
→ EXECUTING
→ EXTRACTING_EVIDENCE
→ POST_PROCESSING
→ EVALUATING
→ COMPLETED / FAILED / CANCELLED
```

等待信息和等待确认是求解前可恢复状态，不计为 CFD 失败。

### 12.2 状态与高频指标分离

主工作流只记录阶段进入、完成和失败等低频事件。残差、时间步、Courant 数和迭代指标进入独立
metrics 数据流，按时间窗口聚合并保留原始日志证据，避免主时间线事件洪泛。

CLI 和 Desktop 只读取统一 `WorkflowProjection`：

```text
current_stage
stage_progress
active_operation
latest_solver_time
recent_residuals
pending_questions
failure_summary
artifact_links
```

Desktop 不自行解析日志或猜测 Run 状态。

### 12.3 恢复

- 每个阶段写入完整不可变 checkpoint 后才允许进入下一阶段；
- 已冻结的 MeshFacts、SimulationIntent 和 CaseDesign 不重复生成；
- 用户确认通过新的不可变 continuation run 保存 lineage；
- 已进入 OpenFOAM 执行的任务不默认承诺任意时间步续算；
- 只有 solver extension 显式声明并验证 continuation capability 时，才能从合法写出时间继续。

## 13. 执行证据与失败报告

### 13.1 单一证据提取

`EvidenceExtractor` 是原生命令结果和日志的唯一语义解析入口。例如 `checkMesh` 结果形成：

```yaml
mesh_check:
  executed: true
  executable_identity: ...
  return_code: 0
  mesh_ok: true
  evidence_paths: []
```

其他模块只能读取该事实，不能再次通过 basename、完整路径或日志正则自行判断。这条约束必须有
架构测试和依赖检查保护。

### 13.2 `FailureReport`

所有失败正常终结并生成确定性报告：

```yaml
failure_layer: execution
failure_code: NUMERICAL_DIVERGENCE
failed_stage: EXECUTING
failed_attempt: 1
failed_step_id: solve
observations: []
confirmed_causes: []
hypotheses: []
automatic_repair:
  enabled: false
  attempted: false
  reason: disabled_by_user
completed_progress: []
preserved_artifacts: []
recommended_actions: []
evidence_paths: []
```

报告严格区分观测事实、确认原因和推测原因。证据不足时不得把推测包装为根因。

确定性报告完成后可以可选调用模型生成进一步诊断建议。该功能默认开启，但输出必须放入带有
`hypothesis` 标签的独立 `model_diagnostic` 字段，不能覆盖确定性证据。模型不可用时，基础报告
仍必须完整生成且 Run 正常终结。

## 14. 迁移计划

### 阶段 1：资产与网格事实

- 建立 `AssetBundle`、`AssetFacts` 和第一方 adapter registry；
- 实现 `OpenFOAMMeshBundle` 与 `PolyMeshInspector`；
- 统一迁移 surface、Gmsh 和参数化资产入口；
- 增加 provided mesh 的系统受控 pre-authoring `checkMesh`。

### 阶段 2：意图、设计与风险门禁

- 建立 `SimulationIntent`、`CaseDesignProposal` 和冻结 `CaseDesign`；
- 拆分意图理解和方案设计模型调用；
- 实现程序所有的 RiskGate 和逐字段确认；
- 迁移 TaskDraft/TaskSpec 到新契约。

### 阶段 3：Authoring 与计划解耦

- CaseAuthor 只输出 manifest 和原生文件；
- PlanCompiler 确定性产生 typed commands；
- CaseVerifier 检查冻结设计实现；
- repair 改为 RepairProposal 和 NumericalRepairEnvelope。

### 阶段 4：薄 Coordinator 与单一 RunFacts

- 从 NativeAgent 移出领域判断；
- 原始日志统一由 EvidenceExtractor 解析；
- 高频 metrics 与工作流事件分离；
- CLI/Desktop 统一读取 WorkflowProjection；
- 实现 FailureReport 和可选模型诊断。

### 阶段 5：Observation 与后处理扩展

- 建立 ObservationExtension 和 PostProcessor registry；
- 实现首批通用指标；
- AcceptanceEvaluator 只消费结构化事实和指标。

每阶段迁移后直接切换规范 CLI 路径。允许短期内部 adapter，但不提供第二条公开 solve 路线；
迁移完成后删除旧 authoring、验证和重复日志解析实现。旧 Run 只读报告，不进入新的严格 resume。

## 15. 测试与验收

### 15.1 通用场景矩阵

至少覆盖：

- provided polyMesh + 瞬态不可压缩 + 区域模型；
- blockMesh + 稳态不可压缩；
- 可压缩瞬态；
- VOF 或多相；
- 浮力/传热；
- 多区域或 CHT；
- surface/Gmsh 网格路线；
- 缺少单位、patch role、材料或工况的确认门禁；
- 模型不可用和扩展能力缺失；
- 数值发散时自动 repair 开启/关闭；
- 取消、崩溃恢复和 continuation；
- Desktop 与 CLI 的状态、失败原因和残差一致性。

schema、门禁、权限和状态迁移使用确定性测试；case 语义和证据解析使用冻结 fixture/replay；
每个主要物理族至少保留一个 Foundation v10 小型真实门禁。多孔算例只是 provided polyMesh 和
区域模型的代表门禁，不能成为核心设计模板。

### 15.2 架构验收条件

- 模型不能靠自报 confidence 绕过门禁；
- 高影响字段没有具体确认值时不能进入 CaseAuthor；
- CaseAuthor 不能改变冻结设计；
- provided polyMesh 以一个原子资产进入，zone 文件不会丢失；
- 模型不读取大体积 polyMesh 原始内容；
- Runner 不解释 CFD 结果；
- 原始日志只由 EvidenceExtractor 解析一次；
- Coordinator 不包含物理族、求解器或网格格式专用判断；
- 插件不能绕过 RiskGate、PlanCompiler 和 Runner；
- 关闭自动数值 repair 后仍产生充分、证据分层的 FailureReport；
- 旧主流程和重复解析器在迁移完成后删除。

## 16. 原始问题追踪矩阵

| 原始问题 | 设计中的直接措施 | 实施阶段 | 主要验收证据 |
| --- | --- | --- | --- |
| polyMesh 输入抽象错位 | `OpenFOAMMeshBundle`、目录 manifest、原子 staging、禁止模型覆盖 | 阶段 1 | 完整/缺文件/zone/篡改测试与真实 provided-mesh gate |
| 网格事实未成为系统权威 | `PolyMeshInspector`、`InputMeshFacts`、`ExecutedMeshFacts`、受控 checkMesh | 阶段 1 | patch/zone/count/bounds/维度 fixtures 和真实 checkMesh |
| 验收与后处理缺少独立闭环 | `ObservationPlan → PostProcessor → AcceptanceEvaluator` | 阶段 5；契约在阶段 2 建立 | 流量、压差、连续性、区域平均等通用指标测试 |
| 模型承担过多职责 | 三次有界调用：Intent、Design、Author；冻结 CaseDesign；RepairProposal | 阶段 2、3 | schema、hash、越权修改和恢复测试 |
| 编排器过重且重复解释证据 | 薄 Coordinator、唯一 EvidenceExtractor、统一 RunFacts/Projection | 阶段 4 | 依赖边界测试、单次解析测试、CLI/Desktop 一致性 gate |

这五项均是本设计的显式交付条件；任何阶段只完成类和接口、但没有通过对应验收证据，都不能
声称该问题已经解决。
