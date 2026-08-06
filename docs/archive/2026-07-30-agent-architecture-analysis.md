---
title: "智能体架构优化分析"
source: "https://chatgpt.com/c/6a6b4448-1208-83ec-a3de-081b99330d71"
status: "archived"
archived: 2026-08-06
superseded_by:
  - "../architecture-optimization-design.md"
  - "../system-overview.md"
author:
published:
created: 2026-07-30
description: "ChatGPT conversation with 4 messages"
tags:
  - "clippings"
---

> **归档说明（2026-08-06）**
>
> 本文是 2026-07-30 的历史对话剪藏，用于保存 FoamPilot 架构优化的讨论背景，
> 不代表当前实现能力或现行开发规范。Provider/Workflow 韧性、能力路由、槽位上下文、
> CaseManifest 和自然语言 TaskBuilder 已由后续实现与验收材料承接；仍未完整实现的
> family compiler、定向 RepairPatch、command 插入和扩展学习路由，已由
> [架构优化设计规格](../architecture-optimization-design.md)继续跟踪。当前能力以
> [架构、运行流程与功能边界](../system-overview.md)和源码测试为准。

[zhaohaoran-suanhai/FoamPilot](https://github.com/zhaohaoran-suanhai/FoamPilot) 这是我开发的一个基于openfoam v10的智能体，通过自然语言交互能够编写仿真场景对应的case，并执行求解。但是我发现在使用过程中总是出现问题，我使用此智能体尝试求解openfoam官方题库中的算例，发现大多数都没有正常通过，我将目前这个智能体的流程和出现的问题总结到这个文档中，请你帮我分析，目前的这个agent是否存在流程上的问题，是否存在不健康的架构和设计。如果错误出现在求解阶段，我可以认为是当前的知识库还不完善，skill中的行为引导还不全面，导致case的编写不够正确，但是现在出现的问题往往出现在求解环节之前，这让我怀疑当前这个智能体的设计是否出现了不合适的地方。请你在阅读完文档后仔细思考，并给我一个合理的判断，帮我分析一个健康的智能体应该是怎样的，我应该怎样优化当前开发的这个智能体？

---

**FoamPilot 不是“整个架构都错了”，但确实存在几个不能靠继续补充知识库和 Skill 根治的结构性问题。**

它目前最准确的定位不是“可稳定处理广泛 OpenFOAM 场景的生产级智能体”，而是：

> **一个安全性较强、评测边界清晰，但生成可靠性和运行韧性不足的盲测型 OpenFOAM case 合成与资格验证流水线。**

你上传的文档对远端模型过载、求解前耗时和状态覆盖问题的判断基本正确。
但我认为文档仍然 **低估了 case 生成架构本身的问题** ：即使模型服务完全稳定，仅仅继续补知识条目，也很难把当前架构提升到真正可靠的工程智能体。

当前架构中值得保留的部分很多：

- typed command，而不是让模型写任意 shell；
- bubblewrap 网络隔离；
- 模型不能直接决定评测结果；
- public task、private evaluator 和 golden reference 分离；
- 每个 attempt 不覆盖前一次结果；
- 日志、case 和哈希清单可追溯。

这些部分是健康的。Runner 对可执行程序、MPI、路径、shell 字符和资源预算的控制也比较清晰，不应该因为通过率低而放松。

真正不健康的是以下组合：

> **外部自然语言转 TaskSpec<br>
> → 无明确求解器族路由<br>
> → 一次模型调用直接生成所有原始文件字节<br>
> → 只有安全校验，没有语义编译校验<br>
> → 同步执行<br>
> → 失败后再进行一次同类大模型调用。**

---

## 一、首先需要纠正：“求解前失败”不等于“架构失败”

OpenFOAM 的主求解器启动之前，实际上存在多个完全不同的失败层。

| 失败位置 | 典型问题 | 主要责任 |
| --- | --- | --- |
| 模型响应前 | `server_is_overloaded` 、超时、SSE 中断 | 模型服务与运行架构 |
| plan 校验 | `mpirun` 、不可用 executable、路径错误 | 输出契约与 Agent 行为 |
| 静态检查 | `.inc` 误报、patch 检查误报 | FoamPilot 检查器 |
| `blockMesh` | 顶点顺序、共享面、boundary face 错误 | case 编写能力与几何表达 |
| `checkMesh` | 网格拓扑、退化维度、非一致接口 | case 编写能力 |
| solver 启动瞬间 | 缺少字典、字段或关键字层级错误 | solver-family 知识与跨文件一致性 |
| solver 迭代后 | 发散、Courant 数、松弛、数值格式问题 | 数值策略 |

因此：

- `blockMesh` 失败虽然发生在“求解前”，本质上仍然属于 Agent 的 case 编写能力；
- `.inc` 被错误拦截，才是静态检查器的流程问题；
- `server_is_overloaded` 是外部故障被架构放大；
- solver 启动时缺少字典键，仍然主要是 solver-family 契约不完整。

你当前报告里的证据实际上也不是“多数 CFD 算例失败”。最新 10 题报告是：

- 6/10 已通过；
- 4/10 没有完成最新修正后的复测；
- 其中多题是因为远端服务没有返回新 case，而不是最新 case 已被证明失败。

所以应该分别计算：

$$
P(\text{端到端完成})
$$

和

$$
P(\text{CFD 通过}\mid\text{成功生成有效 case})
$$

这个区分是完全正确的。

但即使排除模型过载，当前生成路径仍然有较高的结构性脆弱性。

---

## 二、当前最核心的架构问题：模型同时承担了“工程师、编译器和文件生成器”

当前 `ExecutionPlan` 只有两类内容：

```
GeneratedFile:
    path
    content

NativeCommand:
    executable
    args
    mpi_ranks
    timeout_seconds
```

它没有表达：

- 物理问题类型；
- 稳态还是非稳态；
- 单相、两相、可压缩或不可压缩；
- solver family；
- 所需字段集合；
- 字段维度；
- 网格区域和 patch 拓扑；
- 边界条件的物理含义；
- turbulence/transport/thermophysical 模型；
- 各字典之间的依赖；
- command 的 mesh、initialization、solve、reconstruct 阶段；
- 哪个字段由哪个组件创建；
- 哪些文件是某个 solver 的必需文件。

源码中的 `ExecutionPlan` 本质上就是“完整文本文件列表加命令列表”。

这意味着模型必须在一次响应中同时完成：

1. 理解物理需求；
2. 选择求解器；
3. 决定网格；
4. 形成所有字段；
5. 保证所有 patch 名一致；
6. 保证所有 dimensions 一致；
7. 保证 turbulence/transport 字典匹配；
8. 保证命令顺序合理；
9. 输出合法 OpenFOAM 语法；
10. 输出完整且可通过 JSON Schema 的大段文本。

而确定性校验只检查路径、命令、安全和预算，并明确“不判断 CFD 策略”。

因此，当前路径实际上是：

> **让 LLM 直接生成一种没有经过类型检查和语义编译的 OpenFOAM 源代码包。**

这类似于要求模型一次生成一个包含几十个文件的软件项目，然后只检查文件名是否安全，就直接编译运行。

知识库可以告诉模型更多规则，却不能从根本上保证：

```
controlDict.application
= 实际执行的 solver
= fvSolution 中算法配置
= fvSchemes 中出现的离散项
= 0/ 目录中的字段
= transport/turbulence/thermo 字典
```

这些跨文件关系仍然完全由一次随机生成承担。

## 正确方向不是“重新引入逐文件 Reviewer”

逐文件调用模型会增加成本、延迟和不一致性，我不建议恢复。

真正需要的是一个 **薄型、分层的 CaseIR** ，而不是重型的万能 renderer。

例如：

```yaml
case_intent:
  regime: steady
  physics_family: incompressible_rans
  solver_family: simpleFoam
  turbulence_model: kOmegaSST

mesh:
  kind: structured_block
  dimensions: [1.0, 0.1, 0.01]
  cells: [100, 20, 1]
  patches:
    inlet: inlet
    outlet: outlet
    walls: wall
    front_back: empty

fields:
  U:
    dimensions: velocity
    initial: [0, 0, 0]
  p:
    dimensions: kinematic_pressure

boundaries:
  inlet:
    U: fixedValue
    p: zeroGradient

numerics:
  profile: conservative_startup

execution:
  parallel: false
```

然后由 Foundation v10 的 family adapter 确定性地产生：

- `controlDict` ；
- `fvSchemes` ；
- `fvSolution` ；
- transport/turbulence 字典；
- 基础字段头和 dimensions；
- 必需 command；
- MPI 分解和重构步骤。

对于复杂几何、特殊初始化和不常见字典，可以保留 Agent 生成的扩展文件，但标准部分不应继续全部依赖模型写原始字节。

---

## 三、当前并不是真正完整的“自然语言智能体”

仓库自己定义的边界是：

```
用户自然语言
→ 外部 Agent 创建 TaskSpec
→ FoamPilot 接收 TaskSpec
```

FoamPilot 自身不负责从模糊自然语言中澄清需求。仓库文档也明确说明，它从已经形成的 `TaskSpec` 开始，一次请求模型生成全部文件。

但当前 `TaskSpec` 的结构主要仍然是：

- `title` ；
- 一段 `prompt` ；
- `required_outputs` ；
- `acceptance_requirements` ；
- `public_checks` 。

它并没有验证物理信息是否完整。只要文本非空，就可以通过 schema。

与此同时，Skill 又要求模型：

> 不要虚构缺失的物理值。

这里存在一个逻辑缺口：

```
任务可以缺少必要物理信息
        ↓
TaskSpec 仍能通过
        ↓
模型不允许补充缺失值
        ↓
系统又没有询问用户的阶段
```

状态类型里虽然有 `REQUEST_INCOMPLETE` ，但当前 `NativeAgent.solve()` 主路径没有真正利用它完成澄清循环。

一个健康的自然语言 CAE Agent 应该有正式的 specification 阶段：

```
自然语言
→ 信息抽取
→ 完整性检查
→ 必要时询问用户
→ 明确记录假设
→ TaskSpec v2 / CaseIntent
```

例如，在生成前明确判断：

- 几何尺寸是否完整；
- 单位是否明确；
- 流体物性是否完整；
- 边界条件是否闭合；
- 初始条件是否需要；
- 是否稳态；
- 是否湍流；
- 输出目标是什么；
- 收敛与物理验收标准是什么。

这一步不能继续放在外部 Agent 的隐式行为里。

---

## 四、知识库并不是主要问题，但当前知识路由确实不健康

当前 `load_agent_context()` 的实际行为是：

1. 把标题、prompt 和 required outputs 拼起来；
2. 对全部知识做一次词项检索；
3. 选前 5 条；
4. 始终加载同一个通用 Skill。

更关键的是，底层 `KnowledgeQuery` 明明支持：

- `solver` ；
- `knowledge_types` ；
- `evaluation_family` ；

但 canonical 调用没有传这些字段。

所以当前流程是：

```
还没有正式选定 solver family
→ 直接用任务文本检索
→ 希望 top-5 正好覆盖 solver、mesh、BC、numerics
```

这会产生两个问题。

## 1\. 检索发生得太早

应该先形成一个粗粒度能力分类：

```
incompressible / compressible
steady / transient
laminar / RANS
single-region / multi-region
single-phase / VOF / multiphase
mesh strategy
candidate solver family
```

再进行定向检索。

## 2\. 所有知识类型竞争同一个 top-5

网格知识、求解器契约、边界条件和数值格式可能互相挤占位置。

更健康的方式是“槽位检索”：

```
1 条 solver-family contract
1 条 field/dictionary dependency contract
1 条 mesh pattern
1 条 boundary-condition contract
1 条 numerics/startup contract
必要时 1 条 error playbook
```

## 3\. Skill 在仓库里存在，但运行时没有真正路由

README 表示包中有通用、buoyant、benchmark、 `rhoCentralFoam` 等 Skills。

但实际 `load_agent_context()` 始终读取：

```
skills/openfoam-author-native-case/SKILL.md
```

所以 solver-family Skill 目前更多是“仓库中存在”，不是 canonical solve 路径的一部分。

这属于明确的运行时架构缺口，而不只是知识内容不够丰富。

---

## 五、模型服务故障确实被当前架构严重放大

## 1\. 整个 solve() 是一个同步大状态机

环境发现、上下文、模型生成、plan 校验、落盘、执行、验证和 repair 全部放在一次同步的 `NativeAgent.solve()` 中。初始模型调用失败时，run 直接以 `BLOCKED_ENVIRONMENT` 结束。

repair 调用失败时同样直接终止，并把顶层状态改为 `BLOCKED_ENVIRONMENT` 。

这不是简单的重试参数问题，而是缺少 **可恢复工作流状态** 。

## 2\. 重试没有错误分类

当前所有 `TransportError` 都使用同一套：

```
5、15、45、90 秒
```

总共最多五次。

但这些错误应该区别处理：

| 错误 | 是否重试 |
| --- | --- |
| 服务过载、429、部分 5xx | 重试或 deferred |
| DNS/短暂连接失败 | 重试 |
| access token 无效 | 不应重试五次 |
| 权限不足 | 不应重试 |
| Schema 不合法 | 小型格式修复，而非 transport 重试 |
| SSE 已收到部分内容后断开 | 应保留诊断与部分响应 |

当前 provider 把多种请求、流解析和 SSE 错误归入较粗的 `TransportError` ，而且客户端只返回最终解析后的 Pydantic 对象，没有 request ID、重试次数、输出大小或 finish reason。

## 3\. 记录的 model call 数量并不是真实 HTTP 尝试数量

`NativeAgent` 只在调用 `author_case_bundle()` 或 `request_repair()` 前把 `model_calls` 加一；内部 `generate_with_retry()` 即使发起了五个传输请求，最终配置仍可能记录为一次逻辑调用。

所以你计划增加的模型可观测性不能只在现有 `model_calls` 上补字段，需要把 tracing 下沉到 provider gateway。

## 4\. 批量调度器没有共享熔断器

资格测试会把任务提交给线程池；每个任务独立构造模型客户端并执行完整重试。

即使 `workers=1` ，后续任务也不会知道上一题已经证明 provider 处于过载状态。 `workers=2` 时还可能进一步放大压力。

## 5\. CLI 虽然核心定义了 ModelClient Protocol，但实际固定使用一个 provider

`ModelClient` 的抽象只有一个 `generate_structured()` 方法。
CLI 则直接创建 `CodexOAuthModelClient` ，没有 provider、fallback 或离线模式选择。

因此，“核心是 provider-neutral 的”目前只在类型层面成立，在实际产品路径上尚未成立。

---

## 六、当前 repair 名义上是 evidence-scoped，实际上只限制了输出，没有限制输入

`request_repair()` 会把以下内容全部再次发给模型：

- 整个 plan；
- 所有当前声明文件的完整内容；
- 验证报告；
- 失败日志尾部；
- 全部动态知识；
- 完整 Skill。

所以它的准确描述应当是：

> **输出范围受限的全量上下文 repair。**

这会导致：

1. repair 请求可能比初始生成更大；
2. 与错误无关的文件进入上下文；
3. 模型可能修改错误的组件；
4. provider 负载更高；
5. 发生传输失败时损失更大。

健康的 repair 应当先进行确定性错误定位：

```
failed_step
→ 日志错误分类
→ 涉及的文件
→ 字典依赖
→ 最小上下文
→ 受约束 patch
```

例如：

```
keyword laminar is undefined
→ constant/thermophysicalTransport
→ buoyant laminar transport contract
→ 只发送该文件、相关 solver contract 和错误片段
```

而不是发送全部 case。

## 另一个限制：repair 不能增加新的 command

repair 可以增加文件，但 `changed_commands` 只能修改已经存在的 `step_id` ；新 step 会被判为 `UNKNOWN_REPAIR_STEP` 。

因此如果初始计划漏掉：

- `setFields` ；
- `topoSet` ；
- `decomposePar` ；
- `reconstructPar` ；

repair 即使正确诊断，也无法安全插入这一步。

更合理的 patch contract 应支持：

```
replace_file
add_file
replace_command
insert_command_before
insert_command_after
remove_command
```

所有操作继续经过 deterministic policy 即可。

---

## 七、状态模型需要从“一个终态字符串”改为“工作流状态 + 原始失败 + 当前阻塞”

当前 `RunSummary` 只有一个顶层 `status` 。

这正是以下问题的根源：

```
第一次 attempt: SOLVER_FAILED
repair 模型: server_is_overloaded
最终 status: BLOCKED_ENVIRONMENT
```

建议改成：

```yaml
workflow_state: DEFERRED_REPAIR

last_completed_stage: SOLVER_EXECUTION

primary_failure:
  layer: SOLVER_FAILED
  step_id: solve
  code: missing_dictionary_keyword

terminal_blocker:
  layer: MODEL_PROVIDER
  code: server_is_overloaded
  retryable: true

resume:
  allowed: true
  checkpoint: repair-request.json
```

这样就不会再让“当前无法继续”覆盖“原始 CFD 为什么失败”。

同时应当把 `BLOCKED_ENVIRONMENT` 拆开：

```
LOCAL_ENVIRONMENT_BLOCKED
MODEL_PROVIDER_UNAVAILABLE
MODEL_AUTH_FAILED
MODEL_RATE_LIMITED
MODEL_OUTPUT_INVALID
```

本地 OpenFOAM 不存在和远端大模型过载不属于同一种 environment failure。

---

## 八、不可变产物与可恢复运行不应互相冲突

当前所有终止路径都会调用 `_finish()` ，然后生成不可变 manifest。ArtifactStore 的冻结机制本身是好的。

但“冻结证据”和“工作流不能恢复”不应绑定在一起。

推荐两种设计之一：

## 方案 A：可变 workflow，终态后冻结

```
run-workspace/
    workflow-state.json
    checkpoints/
    attempts/

最终结束
→ 生成 immutable artifact bundle
```

## 方案 B：每次继续创建新的 continuation run

```
run-A:
    provider failure
    frozen

run-B:
    parent_run_id: run-A
    resume_from: repair
    frozen
```

为了保持你当前非常重视的审计性，我更建议方案 B。它不修改旧产物，但可以从旧 run 中恢复。

---

## 九、需要把“盲测模式”和“工程生产模式”分开

当前 FoamPilot 的设计明显优先考虑：

- 不读取目标 tutorial；
- 不使用 golden；
- 不使用题目专用模板；
- 不使用 deterministic renderer；
- 防止评测泄漏。

这对于衡量 Agent 的泛化能力是合理的。文档甚至明确说明 canonical path 中没有 CaseSpec 和 renderer。

但这与工程产品的目标并不相同。

## Blind qualification mode

目标是测试：

> Agent 不看目标答案时，能否独立构造 case。

应该：

- 禁止目标 tutorial；
- 固定模型和版本；
- 不使用 fallback provider 混合结果；
- 保持严格的泄漏控制；
- 报告随机性和条件通过率。

## Engineering production mode

目标是：

> 尽可能可靠地帮助工程师完成仿真。

应该允许：

- 使用经过审核的 solver-family recipe；
- 使用公开官方文档与通用 tutorial pattern；
- 询问用户；
- 使用已验证的 case family 模板；
- provider fallback；
- 缓存和断点恢复；
- 人工审批关键假设。

当前架构把盲测约束当成了唯一产品路径。它非常适合研究评测，但会人为降低真实工程使用中的成功率。

这也是为什么我认为它目前更像 **qualification harness** ，而不是成熟的工程 Agent。

---

## 十、我建议的目标架构

```
┌──────────────────────────────────────────┐
│ 1. 自然语言理解与需求澄清                │
│    TaskSpecBuilder / AssumptionManager   │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 2. 能力分类与路由                         │
│    physics family / solver family / mesh │
│    dynamic Skill + slot-based retrieval  │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 3. 语义 CaseIR                            │
│    fields / patches / models / numerics  │
│    commands / dependencies               │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 4. Foundation v10 family compiler         │
│    deterministic dictionaries and steps  │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 5. 分层预检查                              │
│    schema → native parse → mesh → init    │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 6. Sandboxed OpenFOAM execution           │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 7. 独立验证与物理资格评测                  │
└───────────────────┬──────────────────────┘
                    ↓
┌──────────────────────────────────────────┐
│ 8. 故障分类与最小 patch repair             │
└──────────────────────────────────────────┘
```

两个横向基础设施贯穿全部阶段：

```
Provider Gateway
- health
- deadlines
- retry classification
- circuit breaker
- tracing
- fallback
- request checkpoint

Workflow Store
- stage events
- checkpoints
- primary failure
- blocker
- continuation
- immutable evidence
```

关键原则是：

> **模型负责理解、选择和解释；确定性程序负责语法、依赖、执行和测量。**

而当前设计是：

> **模型负责理解、选择、语法、依赖和全部文件字节；程序主要负责安全执行。**

---

## 十一、建议按这个顺序改造

## P0：先修端到端运行韧性

这部分不改变 CFD 生成逻辑，也最容易验证价值。

### 1\. 新建 ModelGateway

不要继续让 `generate_with_retry()` 只是一个简单循环。它至少应返回：

```
provider
model
request_id
purpose
request_hash
prompt_bytes
output_bytes
started_at
finished_at
transport_attempts
server_error_code
retryable
retry_after
partial_output_available
```

错误分成：

```
ProviderOverloaded
RateLimited
AuthenticationFailed
PermissionDenied
NetworkUnavailable
StreamInterrupted
SchemaOutputInvalid
```

### 2\. 增加共享 circuit breaker

按：

```
provider + model + account
```

共享状态，而不是每题独立重试。

连续出现 `server_is_overloaded` 后：

```
后续题 → MODEL_DEFERRED
```

而不是继续每题等待五次。

### 3\. 分开预算

TaskSpec 增加：

```yaml
generation_deadline_seconds:
repair_deadline_seconds:
execution_deadline_seconds:
total_deadline_seconds:
```

`max_wall_seconds` 不再只覆盖 OpenFOAM command。

### 4\. 增加 resume

例如：

```bash
foampilot resume RUN_DIR --from repair
foampilot resume RUN_DIR --from generation
```

### 5\. 输出实时 stage event

```
TASK_VALIDATED
ENVIRONMENT_READY
ROUTING_READY
MODEL_REQUEST_STARTED
MODEL_RETRY
MODEL_DEFERRED
PLAN_READY
CASE_COMPILED
STATIC_CHECK_PASS
MESH_STARTED
MESH_READY
SOLVER_STARTED
VALIDATION_COMPLETE
```

---

## P1：修复 domain architecture

### 1\. 引入 TaskSpec v2

不要把所有物理信息放在 prompt 中。增加结构化字段，但保留 `prompt` 作为原始需求：

```
geometry
materials
physics
initial_conditions
boundary_conditions
models
numerics_preferences
outputs
acceptance
assumptions
unresolved_questions
```

### 2\. 引入 CapabilityProfile

先路由，再检索：

```yaml
physics_family: buoyant_compressible
solver_family: buoyantFoam
mesh_family: structured_block
regions: 1
turbulence: kOmegaSST
parallel_required: true
```

### 3\. 动态 Skill 路由

每次最多：

```
1 个通用 workflow Skill
1 个 solver-family Skill
```

不要把多个长 Skill 全塞进 prompt。

### 4\. 槽位式知识检索

保证覆盖：

- solver contract；
- required-field/dictionary contract；
- mesh contract；
- BC contract；
- startup numerics；
- 可选 error playbook。

---

## P2：逐步引入 CaseIR，而不是一次开发万能 renderer

先覆盖最常见的三类：

1. `simpleFoam/pisoFoam/pimpleFoam` ；
2. `buoyantFoam` ；
3. `rhoSimpleFoam/rhoPimpleFoam` 。

每个 family adapter 负责：

- 必需文件清单；
- 必需字段及 dimensions；
- transport/turbulence/thermo 字典；
- fvSchemes 必需项；
- fvSolution 必需项；
- pressure reference；
- command graph；
- 基础稳健数值 profile。

然后再逐渐扩展：

- VOF；
- solid；
- CHT；
- electromagnetics；
- SRF/MRF。

这不是为每道题写模板，而是给每一类求解器建立 **版本化的编译契约** 。

---

## P3：重构 repair

### 1\. 增加错误分类器

先使用确定性规则识别：

```
missing keyword
unknown patch
dimension mismatch
unknown field
mesh topology
linear solver divergence
floating-point exception
Courant instability
```

### 2\. 建立文件依赖图

例如：

```
buoyantFoam
 ├── controlDict
 ├── fvSchemes
 ├── fvSolution
 ├── thermophysicalProperties
 ├── thermophysicalTransport
 ├── momentumTransport
 ├── g
 └── 0/{U,p,p_rgh,T,...}
```

日志指向某个关键字时，只取相关子图。

### 3\. 使用 patch，而不是重新发送完整文件集合

```
replace_dictionary_entry
add_dictionary_entry
replace_file
insert_command
```

### 4\. 将 plan 形状错误与 CFD repair 分离

例如模型输出 `mpirun` 时，不必再调用模型。可以由 deterministic normalizer：

```
mpirun -n 4 simpleFoam -parallel
```

正规化为：

```yaml
executable: simpleFoam
mpi_ranks: 4
args: []
```

已知、无歧义的结构错误应由程序修正；只有涉及工程判断时才调用模型。

---

## 十二、哪些事情不应该做

我同意你文档中的部分否定结论：

- MCP 不能解决模型过载；
- 不应恢复逐文件模型 Reviewer；
- 不应放宽 sandbox；
- 不应把官方目标 case 直接复制为答案；
- 不应继续在静态检查中硬编码大量不确定语义；
- 不应把 provider 过载计入 CFD case 准确率。

但我不同意“因此完全不需要改变主流程”这一更强的推论。

报告最后认为四题只需等待服务稳定后复测、无需改变主流程。

这个判断对于“尽快完成这四道特定题的复测”是成立的。

对于你的更大目标——

> 让 Agent 稳定处理不同 OpenFOAM 官方题库和真实工程场景

则不成立。

如果不引入语义 TaskSpec、solver-family 路由和薄型 CaseIR，今后每增加一个新的 solver family，仍然会重复出现：

```
缺字段
字典层级错误
维度错误
patch 不一致
command 漏失
数值启动不稳
```

然后继续把这些现象逐条写入越来越长的知识库。这会形成一种不健康的演化：

> **用更多提示词修补缺失的编译器。**

---

## 最终结论

FoamPilot 当前的基础并不差，不需要推倒重来。

应当保留：

- TaskSpec 和 evaluator 隔离；
- typed commands；
- sandbox；
- 独立验证；
- immutable attempts；
- 受控学习与防泄漏设计。

但需要改变核心责任边界：

### 当前

```
LLM
= 需求理解
+ solver 选择
+ 物理建模
+ 文件依赖
+ OpenFOAM 语法
+ 全部文件生成
+ command 编排
+ repair
```

### 健康架构

```
LLM
= 需求理解
+ 歧义处理
+ solver/模型选择
+ 高层 CaseIR
+ 异常情况下的受约束修复

确定性系统
= solver-family contract
+ 字典生成
+ 字段与维度检查
+ patch/依赖检查
+ command graph
+ 运行预算
+ sandbox
+ 验证
+ checkpoint/resume
```

**最关键的改动不是增加更多 OpenFOAM 知识，而是把 LLM 从“完整 case 字节生成器”逐渐降级为“语义决策器和异常修复器”。**

同时，把模型 provider 从 `NativeAgent.solve()` 的同步必经点中抽离，形成具备 deadline、错误分类、熔断、checkpoint 和 resume 的独立 gateway。

完成这两项后，FoamPilot 才会从一个安全但脆弱的盲测流水线，成长为一个真正健康、可扩展的 OpenFOAM 工程智能体。

需要说明的是：我核验的是 GitHub 当前 `main` 中的代码；你的文档注明部分结论基于本地未提交改动，因此具体实现细节可能略有差异，但上述关键状态机、模型边界、上下文路由和 plan schema 与文档描述是一致的。

---

我听说一个agent的三要素是LLM、工具、上下文，同时一个理想的agent具备持续学习、优化迭代的能力，从这个角度上看，你认为的FoamPilot的健康架构能满足这几点吗

---

可以。 **我所说的健康架构不仅能够满足“LLM、工具、上下文”三要素，而且更有利于建立真正的持续学习闭环。**

但需要先澄清一点：

> “LLM、工具、上下文”描述的是 Agent 在一次任务中如何运行；
> “持续学习、优化迭代”描述的是 Agent 完成任务后如何利用反馈改进下一次任务。

前者是 **运行闭环** ，后者是 **学习闭环** 。一个完整的工程 Agent 需要两者同时存在。

---

## 一、三要素并不等于完整 Agent 架构

可以用控制系统做一个类比：

| Agent 部分 | 控制系统类比 | FoamPilot 中的含义 |
| --- | --- | --- |
| LLM | 控制器、决策器 | 理解需求、选择求解路线、诊断错误 |
| 上下文 | 当前状态与参考目标 | 用户需求、OpenFOAM 环境、知识、Skill、运行日志 |
| 工具 | 执行器和被控对象 | OpenFOAM、文件生成器、检查器、Runner、评测器 |
| 评测反馈 | 传感器 | `checkMesh` 、日志、收敛指标、物理验证 |
| 记忆与学习 | 参数辨识和控制器更新 | 将失败经验沉淀为知识、Skill、规则或工具改进 |

所以严格来说，一个健康 Agent 至少应该具备：

```
LLM + 工具 + 上下文
        ↓
执行任务
        ↓
独立评测
        ↓
经验记录
        ↓
受控学习和版本更新
```

仅仅有前三项，只能说明它“能够行动”，不能说明它“能够可靠地改进”。

---

## 二、健康 FoamPilot 中 LLM 应该扮演什么角色

当前 FoamPilot 确实有 LLM，但 LLM 承担的责任太多。

它现在需要一次性完成：

```
理解物理问题
→ 选择求解器
→ 选择网格方案
→ 确定字段
→ 编写所有 OpenFOAM 文件
→ 保证跨文件一致性
→ 编排命令
→ 输出完整 JSON
```

仓库的 canonical 流程明确是一轮模型调用返回全部文件与命令，随后确定性策略只检查安全、路径、资源和命令形状，而不判断 CFD 语义。

这当然也是一种 Agent，但属于一种 **LLM 责任过载的 Agent** 。

健康架构下，LLM 应主要负责四类任务。

## 1\. 需求理解

把自然语言转换为结构化仿真意图：

```yaml
physics:
  compressibility: incompressible
  regime: steady
  turbulence: RANS

geometry:
  type: pipe
  diameter: 0.1
  length: 2.0

boundary_conditions:
  inlet:
    kind: velocity
    value: 1.0
  outlet:
    kind: pressure
    value: 0.0
```

## 2\. 工程决策

例如判断：

- 选择 `simpleFoam` 还是 `pimpleFoam` ；
- 是否需要湍流模型；
- 使用结构网格还是外部网格；
- 应采用保守启动还是高阶离散；
- 是否需要 MPI。

## 3\. 异常诊断

结合日志、当前 case 结构和 solver contract，判断：

```
这是缺字段？
字典层级错误？
网格拓扑错误？
边界条件不一致？
还是数值发散？
```

## 4\. 提出受约束的修复方案

例如：

```
修改 constant/thermophysicalTransport
将 laminar 模型放入 Foundation v10 要求的顶层子字典
其他文件不变
```

LLM 不应继续承担所有固定字典模板、字段维度、命令图和语法拼装工作。

这不是削弱 Agent，而是让 LLM 做它擅长的事情：

> **理解、推理、选择和解释。**

---

## 三、工具不只是 OpenFOAM 求解器

很多人把 Agent 的“工具”理解为：

```
LLM 调用 blockMesh
LLM 调用 checkMesh
LLM 调用 simpleFoam
```

但对于 FoamPilot，工具层应该远比这丰富。

健康架构至少需要六类工具。

## 1\. 需求检查工具

负责判断当前任务是否完整：

- 几何尺寸是否齐全；
- 单位是否明确；
- 材料参数是否完整；
- 边界条件是否闭合；
- 稳态还是非稳态；
- 是否给出验收指标。

如果信息不完整，返回：

```
REQUEST_INCOMPLETE:
缺少入口速度或质量流量
```

而不是让模型猜。

## 2\. Solver-family 编译工具

将高层 CaseIR 转换为 Foundation OpenFOAM v10 文件。

例如：

```
IncompressibleRANSCaseCompiler
BuoyantCaseCompiler
CompressibleCaseCompiler
VOFCaseCompiler
SolidCaseCompiler
```

它负责确定性地产生：

- 必需字段；
- 字段 dimensions；
- `controlDict` ；
- `fvSchemes` ；
- `fvSolution` ；
- transport/turbulence/thermo 文件；
- command graph。

## 3\. 静态语义检查工具

不仅检查括号和路径，还检查明确的跨文件契约：

```
controlDict.application == 执行的 solver
solver 所需字段是否存在
mesh patch 是否全部被字段覆盖
字段 dimensions 是否符合 solver family
湍流模型与字段是否匹配
并行求解是否包含 decompose/reconstruct
```

不确定的语法仍交给 OpenFOAM，不需要构造一个完整 OpenFOAM 解析器。

## 4\. OpenFOAM 原生工具

包括：

```
blockMesh
checkMesh
setFields
topoSet
decomposePar
solver
reconstructPar
postProcess
```

当前 FoamPilot 的 typed command、sandbox 和 MPI 控制属于比较健康的工具边界：模型不直接控制 shell，Runner 负责执行与资源限制。

## 5\. 评测工具

包括：

- 日志解析；
- 收敛判断；
- 连续性检查；
- 字段有界性；
- 守恒检查；
- 最终场输出检查；
- 与参考结果的物理资格比较。

这一部分在 FoamPilot 中已经比较清晰：模型生成结果，但评测器独立判断是否通过。

## 6\. 模型服务工具

模型本身也应该被包装成工具服务：

```
ModelGateway
├── provider health
├── timeout
├── error classification
├── retry
├── circuit breaker
├── request tracing
├── checkpoint
└── provider fallback
```

当前 CLI 实际固定创建 `CodexOAuthModelClient` ，虽然核心有 `ModelClient` Protocol，但生产入口还没有真正形成 provider 池和恢复能力。

因此，健康架构非常符合“工具型 Agent”的理念，只是工具不能只有 OpenFOAM 命令，还需要 **编译、检查、评测、记忆和服务治理工具** 。

---

## 四、上下文不等于知识库

这是 FoamPilot 当前最需要澄清的概念之一。

知识库只是上下文的一个来源：

$$
\text{知识库}\neq\text{上下文}
$$

真正发送给 LLM 的上下文，应该是：

```
当前任务
+ 已确认的工程假设
+ 环境能力
+ solver-family contract
+ 相关知识
+ 当前工作状态
+ 失败证据
+ 可修改范围
+ 安全和评测规则
```

健康 FoamPilot 应该具有多层上下文。

## 1\. 任务上下文

描述当前要解决什么问题：

- 几何；
- 材料；
- 物理；
- 边界；
- 初始条件；
- 输出；
- 验收条件。

## 2\. 环境上下文

描述当前实际能做什么：

- Foundation OpenFOAM v10；
- 可用 solver 和 utility；
- MPI 能力；
- CPU、内存和时间预算；
- 是否有 Gmsh；
- 是否有输入网格资产。

## 3\. 领域上下文

根据已选定的 solver family 动态加载：

- solver contract；
- 字段依赖；
- 字典依赖；
- 边界条件规则；
- 网格规则；
- 数值启动策略。

当前 FoamPilot 是把任务文本做一次词项匹配，选择最多 5 条知识，并固定加载同一个通用 Skill。

这已经算动态上下文，但仍然比较扁平：

```
任务文本 → top-5 知识
```

更合理的是：

```
任务解析
→ solver-family 路由
→ 按知识槽位检索
→ 形成针对当前决策的上下文
```

## 4\. 工作上下文

当前 run 的实时状态：

```
TaskSpec 已确认
solver family 已选择
CaseIR 已生成
blockMesh 已通过
checkMesh 已通过
solver 在第 2 次迭代发生 FPE
```

这部分应该被 checkpoint 化，而不是只存在于一次同步 `solve()` 调用中。

## 5\. 情景记忆

历史上类似问题发生了什么：

```
rhoSimpleFoam + 高负荷弯管
+ linearUpwind
+ 弱松弛
→ 早期压力方程 FPE

保守 upwind + 更强松弛
→ 稳定启动
```

情景记忆不应把整个旧 case 塞给模型，而应保存：

- 问题特征；
- 失败特征；
- 根因；
- 修改；
- 验证结果；
- 适用边界。

## 6\. 规则上下文

包括：

- 不能读取当前目标 tutorial；
- 不能访问 private evaluator；
- 不能执行任意 shell；
- 允许修改哪些文件；
- 资源预算；
- 当前是盲测模式还是工程模式。

因此，健康 FoamPilot 的上下文应该是 **分层、动态、按决策组装的上下文系统** ，而不是不断扩大的统一 prompt。

---

## 五、FoamPilot 是否能真正持续学习

可以，但应当是 **受控持续学习** ，而不是让 Agent 自动修改自己的代码和知识库。

对于 CFD 工程 Agent，完全自主在线学习风险很高。例如一次失败可能来自：

- 模型服务过载；
- 静态检查误报；
- TaskSpec 本身不合理；
- OpenFOAM 版本差异；
- 数值策略错误；
- 偶发模型输出错误；
- 题目特殊性。

如果 Agent 看到一次失败就立即修改全局知识，容易造成错误经验传播。

健康的学习闭环应该是：

```
执行任务
→ 保存完整证据
→ 分类失败
→ 提炼候选经验
→ 判断应该修改什么
→ 在回归、开发和留出场景上重新验证
→ 人工或策略审批
→ 版本化发布
→ 可以回滚
```

FoamPilot 已经有这个方向的基础。

仓库现在的 improvement 工作流是：

```
frozen solve / qualification
→ improve analyze
→ 开发者应用候选变更
→ 重新运行 qualification
→ improve compare
→ 显式 promotion decision
```

并且明确不自动推广。

这不是“缺少学习能力”，而是已经有了一个比较安全的 **离线学习雏形** 。

目前缺的是自动化程度和学习对象分类。

---

## 六、学习不能全部沉淀到知识库和 Skill

这是非常关键的一点。

每一次失败经验应该先判断它属于哪一层。

| 发现 | 应该修改的位置 |
| --- | --- |
| Foundation v10 的固定字典事实 | 知识库 |
| Agent 经常采取错误操作顺序 | Skill |
| 所有 `buoyantFoam` 都需要固定字段集合 | solver-family compiler |
| `.inc` 被错误要求 FoamFile 头 | 静态检查工具 |
| `mpirun` 应转换为 typed MPI command | plan normalizer |
| provider 连续过载 | ModelGateway / 调度器 |
| 评测误把合法时间目录判错 | evaluator |
| 用户需求缺少入口条件 | TaskSpecBuilder |
| 某个特定场景的数值启动经验 | 情景记忆或 numerics profile |

如果所有问题都被处理成：

```
再给知识库增加一条
再给 Skill 增加一句
```

就会产生越来越长、互相冲突、难以验证的提示词。

健康的学习系统应该具有一个 **学习路由器** ：

```
Failure evidence
      ↓
Root-cause classifier
      ↓
┌────────────┬─────────┬───────────┬──────────┐
│ Knowledge  │ Skill   │ Tool/code │ Evaluator│
└────────────┴─────────┴───────────┴──────────┘
      ↓
Regression / development / holdout validation
      ↓
Promotion
```

这才是真正的架构优化能力。

---

## 七、四种记忆共同构成持续学习 Agent

可以把 FoamPilot 的记忆分成四类。

## 1\. 工作记忆

当前任务正在发生什么：

```
当前 solver
当前 CaseIR
当前 attempt
当前失败步骤
当前修复目标
```

需要 checkpoint 和 resume 支持。

## 2\. 语义记忆

长期稳定的 OpenFOAM 知识：

- 字典语法；
- solver 所需字段；
- Foundation v10 版本差异；
- 边界条件规则；
- 数值方法原则。

这就是知识库。

## 3\. 程序性记忆

Agent 应该“怎样做”：

- 如何选 solver；
- 如何从保守格式启动；
- 如何执行 mesh → initialize → solve；
- 如何进行最小修复；
- 哪些操作不能做。

这就是 Skill、solver-family workflow 和工具规则。

## 4\. 情景记忆

过去实际解决过什么问题：

```
任务特征
失败日志
根因
采取的修改
验证指标
是否泛化成功
```

当前 FoamPilot 有很强的不可变 run artifacts，但“保存了历史”还不等于“拥有情景记忆”。

只有当这些 run 能够被：

- 索引；
- 分类；
- 去除题目特有细节；
- 提炼成可复用经验；
- 在新任务中受控召回；

它们才真正成为 Agent 的情景记忆。

---

## 八、当前 FoamPilot 与健康目标的对比

| 能力 | 当前状态 | 健康目标 |
| --- | --- | --- |
| LLM | 存在，但责任过重 | 负责语义推理、路线选择和诊断 |
| 工具 | Runner、安全执行和评测较强 | 增加 CaseIR compiler、语义检查、错误分类、Provider Gateway |
| 上下文 | 动态 top-5 知识 + 通用 Skill | 先路由，再按槽位组装分层上下文 |
| 工作记忆 | 主要存在于同步 `solve()` | 持久状态机、checkpoint、resume |
| 语义记忆 | 已有知识库 | 版本化、适用范围明确的知识契约 |
| 程序性记忆 | 已有通用 Skill | 通用 Skill + solver-family Skill + deterministic workflow |
| 情景记忆 | 有不可变 artifacts | 有索引、有归纳、有受控召回的经验库 |
| 独立评测 | 已有较好基础 | 保持并加强 physics qualification |
| 持续学习 | 有离线 improvement 雏形 | 自动归因、候选生成、回归验证、审批和回滚 |
| 服务韧性 | 较弱 | 熔断、deadline、deferred、恢复和 provider fallback |

---

## 九、建议形成两个相互连接的闭环

## 运行闭环

```
用户需求
→ 任务澄清
→ solver-family 路由
→ 动态上下文
→ LLM 生成 CaseIR
→ 确定性工具编译 case
→ OpenFOAM 执行
→ 独立评测
→ 有界修复
```

## 学习闭环

```
冻结 run 证据
→ 故障归因
→ 经验抽象
→ 确定学习目标
   ├─ Knowledge
   ├─ Skill
   ├─ Compiler
   ├─ Inspector
   ├─ Evaluator
   └─ Orchestrator
→ 回归/开发/留出测试
→ 显式推广
→ 新版本
```

运行闭环负责“把这道题做完”。

学习闭环负责“让下一道相似的题更容易成功”。

---

## 最终判断

**健康 FoamPilot 完全可以满足 LLM、工具、上下文三要素，也可以具备持续学习和优化迭代能力。**

而且我所建议的 CaseIR、solver-family compiler 和确定性工具，并不会让它“不再是 Agent”。恰恰相反，它会使 Agent 的职责更加合理：

```
LLM 决定做什么以及为什么
工具保证怎样可靠地做
上下文告诉它当前知道什么
评测器告诉它结果是否正确
记忆系统保存发生过什么
学习闭环决定系统应该改哪里
```

当前 FoamPilot 已经具备：

- LLM；
- OpenFOAM 与安全 Runner 工具；
- 基础知识和 Skill 上下文；
- 独立评测；
- 不可变证据；
- 离线受控学习的初步框架。

所以它不是从零开始。

它目前真正缺少的是：

1. **LLM 与确定性工具之间的语义中间层；**
2. **分层、按决策组装的上下文系统；**
3. **可恢复的工作记忆和状态机；**
4. **将失败分别路由到知识、Skill、工具、评测器和架构的学习机制。**

补齐这四点之后，FoamPilot 会更接近一个真正的：

> **面向 OpenFOAM 的、工具增强的、上下文驱动的、证据闭环的持续学习工程 Agent。**
