# FoamPilot Performance v1 规格

状态：**已实施，验收证据见 `docs/reports/2026-08-05-performance-v1.md`**
日期：2026-08-05  
适用基线：当前 FoamPilot `main` 工作区中的 TaskSpec v2、ExecutionPlan v3、ModelGateway、
GeometryProbe、MeshQualityReport、有限 repair 和不可变 artifact 主链。

> 上述“当前”仅指 2026-08-05 的适用基线。当前 verified reuse 还必须验证 CaseDesign、
> CaseBundle、conformance、compiler identities 与 ExecutionPlan v4 authority chain。

## 1. 目标

Performance v1 同时优化两类性能，不能只让重复演示变快：

```text
冷路径：第一次遇到的新 TaskSpec
热路径：允许严格复用计划、几何、网格或 attempt 中间产物的任务
```

本规格的目标是：

1. 对完整的新 TaskSpec 保持至多一次主要 case generation，不增加模型审查循环；
2. 对完全相同的 TaskSpec 显式复用已验证 ExecutionPlan，在兼容主机上 5 秒内启动第一条
   OpenFOAM command，并让选定的小型演示 case 在 30 秒内启动目标 solver；
3. 对相同几何和网格配置复用经过验证的派生网格，不把物理参数相似误认为可复用；
4. repair 只重跑受修改影响的 native stage，不默认重复网格生成；
5. 以统一性能产物分别报告冷启动、热启动、模型、网格、solver、repair、验证和归档耗时；
6. 保持现有 Runner、安全检查、公开验证、qualification 隔离和不可变 attempt 不变。

## 2. 非目标

Performance v1 不增加：

- 模糊 TaskSpec 匹配；
- 逐题模板、renderer、CaseSpec 或 family compiler；
- 自动选择“相似”历史 case；
- qualification 的目标任务计划缓存；
- solver checkpoint/restart 的通用实现；
- 对动态网格、拓扑变化或任意第三方 solver 的缓存承诺；
- 常驻服务、分布式缓存、缓存同步、云端索引或数据库；
- Rust/C++ 重写；
- 新的求解状态机或第二个 Runner；
- 以降低检查、验证或物理正确性要求换取速度。

## 3. 性能边界

### 3.1 冷路径

结构化任务的规范冷路径为：

```text
TaskSpec
→ environment / GeometryProbe
→ deterministic routing（确有歧义时才调用模型）
→ bounded context
→ 一次完整 ExecutionPlan generation
→ inspection / native execution / validation
→ 必要时一次限定范围的 repair
```

自然语言入口在前面增加一个明确阶段：

```text
request
→ 一次 TaskDraft extraction
→ deterministic review / compile
→ TaskSpec 冷路径
```

TaskBuilder、路由和 generation 的模型请求必须分别计数。不能把自然语言提取隐藏在 generation
时间内。

### 3.2 热路径

热路径只允许三种可证明的复用：

1. 完全相同 TaskSpec 显式复用已验证 ExecutionPlan；
2. 几何和网格依赖键完全相同，复用派生几何/网格产物；
3. 同一 run lineage 中，根据 repair 修改集合复用未受影响的前序 stage 产物。

任何无法确定依赖关系的情况都退回冷路径或完整重跑，不进行推测性复用。

## 4. 实施顺序

```text
P0 统一性能观测
→ P1 显式复用已验证 ExecutionPlan
→ P2 内容寻址的几何与网格缓存
→ P3 依赖感知的 repair 阶段复用
→ P4 根据冷路径证据调整模型调用和 fail-fast 策略
```

每个阶段独立验收。P0 通过前不实施缓存；P1 不依赖 P2；P2 和 P3 不得改变 qualification
默认行为。

## 5. P0：统一性能观测

### 5.1 数据来源

不增加第二套 tracing。性能报告只聚合已有证据：

- `workflow-events.jsonl`；
- `model-attempts.jsonl`；
- `run-result.json`；
- `public-validation.json`；
- `mesh-quality-report.json`；
- `summary.json` 和 artifact manifest 时间。

### 5.2 性能产物

每个最终 solve run 增加 `performance-summary.json`，至少包含：

```yaml
schema_version: 1
path_kind: cold | warm_plan | warm_mesh | repair_reuse
workflow_seconds_before_manifest: 0.0
time_to_first_openfoam_command_seconds: 0.0
stages:
  environment_seconds: 0.0
  geometry_seconds: 0.0
  routing_seconds: 0.0
  context_seconds: 0.0
  generation_seconds: 0.0
  materialization_seconds: 0.0
  inspection_seconds: 0.0
  mesh_seconds: 0.0
  initialization_seconds: 0.0
  solver_seconds: 0.0
  postprocess_seconds: 0.0
  validation_seconds: 0.0
  repair_model_seconds: 0.0
model:
  logical_requests: 0
  transport_attempts: 0
  retry_delay_seconds: 0.0
reuse:
  plan: miss | hit | disabled
  geometry: miss | hit | disabled
  mesh: miss | hit | disabled
  repair_start_stage: null | mesh | initialize | solve | postprocess
```

没有发生的阶段记录为 `0.0`；证据不足时使用 `null` 并写入 `diagnostics`，不得猜测耗时。

TaskBuilder 发生在 solve run 之前，不能伪造为 run 内阶段。`foampilot task draft` 单独在输出
旁写入 `<draft-output>.performance.json`，记录 extraction 的逻辑请求、传输次数、退避和总耗时；
编译结果已有 `draft_id` 和 `task_sha256`，自然语言性能 suite 据此关联 TaskBuilder 与 solve
两份报告。直接提供 TaskSpec 时不存在 TaskBuilder 性能记录。

最终 artifact manifest 的构建耗时无法在不产生循环改写的情况下写回已被其覆盖的性能文件，
因此由 `artifact-manifest.json` 自身记录 `build_seconds`。suite 总耗时使用
`workflow_seconds_before_manifest + build_seconds`；不得修改 manifest 后再回写性能摘要。

### 5.3 汇总指标

suite 报告分别统计：

- cold-path pre-solve latency p50/p95；
- warm-path pre-solve latency p50/p95；
- end-to-end latency p50/p95；
- 模型 logical request 和 transport attempt；
- cache hit/miss/invalid 数量；
- target solver entry、normal completion 和 public validation；
- provider/environment terminal blocker；
- 一次 repair 后的有效率与实际重跑起点。

## 6. P1：显式复用已验证 ExecutionPlan

P1 复用现有 [已验证 ExecutionPlan 复用设计](../verified-plan-reuse-design.md) 的基本方向，并以
本节适配当前 TaskSpec v2 和前处理契约。

### 6.1 用户入口

```bash
foampilot solve TASK.yaml \
  --reuse-verified-plan SOURCE_RUN_DIR \
  --run-root NEW_RUN_ROOT \
  --json
```

没有该参数时保持当前 live authoring。P1 不增加自动计划缓存目录，也不搜索历史 run。

### 6.2 严格兼容键

复用要求以下内容完全匹配或通过当前环境兼容检查：

```text
canonical TaskSpec v2 SHA256
public asset 声明与实际字节 SHA256
geometry / mesh intent
Foundation OpenFOAM distribution/version
ExecutionPlan schema
solver executable availability
当前 MPI 与资源预算
```

TaskSpec 任意物理、几何、输出、验收、资源或 protected-path 字段变化，均拒绝完整计划复用。

### 6.3 来源资格

source run 必须：

- 已完成 artifact manifest 且验证无问题；
- 有一个 ExecutionPlan v3 attempt；
- mesh command 正常结束；
- `checkMesh` 返回零并明确报告 `Mesh OK`；
- manifest solver 正常启动、返回零、未超时并出现正常结束标志；
- 相关 task、plan、run-result 和日志均由 source manifest 覆盖。

public validation 或外部 qualification 失败不自动取消计划资格，但必须在复用记录中保留，不能把
“solver 正常结束”表述为“物理正确”。

### 6.4 执行语义

计划复用只替换 ExecutionPlan 的来源。新 run 仍然：

```text
重新 staging 当前 public assets
→ 当前 normalizer / policy / semantic inspection
→ 新空目录物化 plan-authored files
→ 当前 Runner 执行全部 native commands
→ 当前 public validation
→ 新的不可变 artifact manifest
```

不从 source 复制 `polyMesh`、时间目录、processor 目录、日志或求解结果；这些属于 P2，而不是
P1。复用运行不构造 ModelGateway，模型 logical request 和 transport attempt 都为零。

### 6.5 拒绝行为

复用拒绝在物化 case 前结束，返回 `PLAN_REUSE_REJECTED` 和稳定原因。不得静默退回模型生成，
以免演示或性能测试在不知情时切换路径。

## 7. P2：内容寻址的几何与网格缓存

### 7.1 入口

P2 首版只支持显式缓存根目录：

```bash
foampilot solve TASK.yaml \
  --derived-cache CACHE_ROOT \
  --run-root RUN_ROOT \
  --json
```

未提供 `--derived-cache` 时禁用。P2 不扫描其他 run，不建设全局索引、淘汰器或同步服务。
网格缓存查询发生在当前 ExecutionPlan 已完成 normalizer、policy 和 semantic inspection 之后，
因此缓存不能绕过对当前 Agent 输出的检查。

### 7.2 GeometryFacts 键

GeometryFacts cache key 包含：

```text
geometry schema version
每个 asset 的相对路径、role、format 和实际 SHA256
显式 length unit
patch/region mapping
GeometryProbe implementation version
```

命中后仍验证当前 asset 字节 SHA256。缓存只保存结构化 GeometryFacts，不保存用户未声明的文件。

### 7.3 网格键

网格 cache key 包含：

```text
GeometryFacts hash
影响网格生成的 mesh strategy、参数与意图
所有 mesh-stage plan-authored 文件 hash
所有 mesh-stage public assets hash
mesh command executable、args 和顺序
Foundation OpenFOAM/Gmsh 版本
region layout
dynamic/topology-changing 标志
```

任何字段缺失或无法确定时为 cache miss。动态网格、拓扑变化、求解过程中修改 mesh 的任务在
Performance v1 中禁止网格缓存。

网格质量阈值不属于 cache key，因为它不改变网格字节；命中后必须使用当前 TaskSpec 阈值重新
评价缓存网格。

### 7.4 缓存内容与恢复

允许缓存：

- `constant/polyMesh` 或各 region 的 `polyMesh`；
- mesh-stage 工具产生且被后续阶段读取的确定性派生文件；
- 对应 `MeshQualityReport`、`checkMesh` 日志摘要和内容 manifest。

命中后将缓存内容通过 reflink/copy 复制到新 attempt，不能以可写 hardlink 共享。随后至少执行：

- 内容 manifest 验证；
- 当前 mesh quality threshold 比较；
- 当前 `checkMesh`，除非该工具本身在后续独立证据下被批准跳过。

缓存损坏时隔离该 entry、记录 miss 原因并重新生成网格，不把它报告为 solver failure。

GeometryFacts 只有在 probe 成功后才能以原子方式写入缓存。网格 entry 只有在 mesh command、
当前 `checkMesh` 和结构化 MeshQualityReport 均成功后才能原子写入，并必须携带独立内容 manifest、
当前 task/plan hash、工具版本和 source run/attempt ID。solver 是否成功不影响已验证网格的缓存
资格。

## 8. P3：依赖感知的 repair 阶段复用

### 8.1 执行调度边界

P2/P3 不修改模型编写的 ExecutionPlan。确定性性能层在 policy 和 inspection 通过后生成一份
`execution-reuse.json`，声明 `reused_step_ids`、`commands_to_execute`、source hash 和理由。
Runner 仍是唯一命令执行器，只接收需要实际执行的原计划 command 子序列；被复用的 step 不伪造
返回码，而是在 `run-result.json` 的独立 `reused_steps` 中引用缓存或 parent 证据。无法构造保持
依赖顺序的子序列时禁用复用并执行完整计划。

### 8.2 原则

repair 仍创建新的不可变 attempt。系统根据 `RepairPatch` 的实际修改路径和 command 变化，确定
最早受影响 stage；无法确定时从 mesh 开始完整重跑。

### 8.3 最早重跑 stage

| 修改证据 | 最早重跑 stage |
| --- | --- |
| geometry、mesh 字典、mesh command、region/patch topology | `mesh` |
| `0/` 初始场、`setFieldsDict` 或 initialize command | `initialize` |
| 物性、模型、`fvSchemes`、`fvSolution`、solver command、求解控制 | `solve` |
| 只修改后处理字典或 postprocess command | `postprocess` |
| command 顺序、include 依赖或文件作用域无法确定 | `mesh` |

即使最早重跑 stage 为 `solve`，新 attempt 仍重新执行当前 `checkMesh`，但不重新运行 mesh
generator。多区域、动态 mesh 或 solver 会修改网格的任务首版始终从 `mesh` 重跑。

### 8.4 复用方式

前序 stage 产物从 parent attempt 通过 reflink/copy 复制到新 attempt，并记录：

```yaml
source_attempt: 1
earliest_rerun_stage: solve
reused_paths:
  - constant/polyMesh
source_hashes: {}
reason_codes:
  - REPAIR_DID_NOT_CHANGE_MESH_DEPENDENCIES
```

复制完成后重新计算 hash。parent attempt 保持只读；新 solver 绝不能通过 hardlink 修改 parent。

### 8.5 安全降级

以下任一条件发生时停止阶段复用并完整重跑：

- source artifact manifest 无效；
- 依赖文件缺失或 hash 不匹配；
- patch/region 集合变化；
- mesh command 或相关 include 发生变化；
- solver family contract 声明网格会随求解变化；
- inspector 无法证明复用安全。

## 9. P4：冷路径模型优化

P4 不增加新模型阶段，只调整已有阶段的触发和错误策略：

1. 结构化 TaskSpec 直接跳过 TaskBuilder；
2. 自然语言入口至多一次 TaskDraft extraction；
3. 显式 solver 或唯一 family candidate 使用确定性路由；
4. case authoring 保持一次完整 bundle generation；
5. 不恢复逐文件生成或模型 reviewer；
6. 本地确定性错误，如 executable 缺失、只读初始化目录和 backend 配置错误，首次失败即停止；
7. 只有网络中断、过载、限流、超时和可恢复流中断使用有界退避；
8. repair 只发送 FailureClassifier 选择的文件、日志片段、Knowledge 和 Skill；
9. qualification worker 继续共享 Gateway 和 circuit breaker，但任务预算与 artifacts 独立。

P4 的 timeout/backoff 数值只能根据 P0 的 p50/p95 证据调整，不能为了更快而让正常慢响应被系统性
截断。

## 10. Qualification 与泛化边界

正式 qualification 默认：

- qualification CLI 不接受 `--reuse-verified-plan` 或 `--derived-cache`；
- 所有 qualification 任务禁用历史 plan、GeometryFacts 和 mesh derived cache；
- 禁止读取任何历史目标 case 或 plan；
- 继续允许通用 Knowledge、Skills、环境发现缓存和共享 Gateway circuit breaker；
- 报告 cold-path latency、target solver entry 和 physics qualification；
- 不把 warm-path 演示成绩计入 Agent 泛化准确率。

如需单独测量工程重复运行性能，应建立明确标记为 warm-performance 的 suite，不与 blind
qualification 合并。

## 11. 安全、隐私与来源

- 缓存和复用记录不得保存 prompt、模型响应正文、凭据、环境变量值、目标 tutorial 或 golden；
- 所有键由公开 TaskSpec、public asset、工具版本和已生成公开 plan 文件计算；
- source run 在读取前验证 artifact manifest，并始终保持只读；
- 缓存恢复后仍执行当前 path、command、resource 和 semantic policy；
- 缓存内容不拥有比原任务更宽的文件访问权限；
- 任何 cache/reuse hit 都写入 run artifact，不能只存在于控制台日志；
- 删除缓存只影响性能，不应影响冷路径正确性。

## 12. 失败语义

Performance v1 使用独立、可审计的原因码，不把缓存问题记为 CFD 失败：

```text
PLAN_REUSE_REJECTED
DERIVED_CACHE_MISS
DERIVED_CACHE_INVALID
REPAIR_REUSE_UNSAFE
PERFORMANCE_EVIDENCE_INCOMPLETE
```

cache miss 是正常事件，不是 terminal failure。显式计划复用被拒绝是终态，因为该模式禁止静默
切回在线模型。repair 复用不安全时自动执行完整重跑，并在 performance summary 中记录原因。

## 13. 验收标准

### 13.1 P0

- 每个终态 run 产生 `performance-summary.json`；
- 阶段耗时可由原始事件和 command 证据复算；
- 冷/热路径和模型/solver 时间不混合；
- full deterministic suite 不出现状态或 artifact regression。

### 13.2 P1

- exact TaskSpec 复用时模型 logical request 和 transport attempt 均为零；
- 兼容已验证主机上从命令启动到第一条 OpenFOAM command 不超过 5 秒；
- 选定的小型演示 case 从命令启动到目标 solver 启动不超过 30 秒；复杂网格只比较消除的模型
  时间，不使用 30 秒目标；
- 任一 TaskSpec/public asset 变化均拒绝复用；
- 新 run 重新通过 policy、inspection、mesh、solver 和 public validation；
- qualification CLI 不接受计划复用参数。

### 13.3 P2

- 相同网格键命中时 mesh generator 执行次数为零；
- 当前 `checkMesh` 和质量阈值仍通过；
- 几何、单位、patch/region、mesh 字典或工具版本变化产生 miss；
- 损坏缓存被隔离并安全回到网格生成；
- 新几何 cold-path 性能和结果不受缓存模块影响。

### 13.4 P3

- 只修改 `fvSchemes`/`fvSolution` 的 repair 不重新运行 mesh generator；
- 修改 mesh 或 patch topology 的 repair 必须从 mesh 重跑；
- parent attempt hash 在 child/new attempt 完成后保持不变；
- 复用与完整重跑在选定回归 fixture 上得到相同的 mesh manifest 和等价公开验证结论。

### 13.5 P4

- 完整结构化新任务不因性能层增加模型调用；
- 显式 solver 的任务不调用路由模型；
- 本地确定性 backend 错误只进行一次 transport，并在 10 秒内返回稳定中文错误；
- provider/environment blocker 不计入 CFD case 准确率；
- target solver entry 和 public-validation pass 不低于实施前冻结基线。

## 14. 明确决策

Performance v1 冻结以下选择：

1. 先观测，再缓存；
2. 计划复用必须显式且完全匹配；
3. 几何/网格缓存采用内容寻址，不使用语义相似度；
4. repair 复用由确定性依赖规则决定，不由模型自报；
5. qualification 只测冷路径，warm performance 单独报告；
6. 缓存失效永远可以退回现有规范冷路径；
7. 不为性能引入 Rust/C++、renderer、MCP、数据库或第二状态机；
8. 实施必须按 P0 → P1 → P2 → P3 → P4 顺序进行。

本文冻结设计，不授权进入实现，也不授权 commit 或 push。
