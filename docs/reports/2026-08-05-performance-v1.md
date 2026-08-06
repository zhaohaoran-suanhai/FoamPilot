# FoamPilot Performance v1 实施与验证报告

日期：2026-08-05
基线：本地 `main` 提交 `06107cd` 之后的未提交工作区
目标运行时：Foundation OpenFOAM v10

## 1. 结论

Performance v1 已按冻结规格进入现有 `NativeAgent.solve()` 主路径，没有增加第二套状态机、
Runner、数据库或常驻服务。普通冷路径保持不变；显式 warm path 可以严格复用计划、几何/网格
派生产物和同一 lineage 中未受 repair 影响的前序阶段。

首次真实 30 题冷路径复测给出了更严格的结论：Performance v1 对相同 TaskSpec 的显式复用
有效，但当时**不能据此宣称全新算例的冷启动性能已经提高**。在初始 `codex-cli` 批次上，30 题
只有 22 题生成成功、20 题启动目标 solver，首次 OpenFOAM 命令中位数为 243.5 秒。18/30 题
至少发生一次 `SCHEMA_INVALID`，其中 8 题最终在生成阶段暂缓。

随后按该证据实施了轻量结构化输出修正：只对无歧义 `step_id` 和内容完全等价的 manifest
field 做本地、可审计规范化，并把最终无效输出归为 `GENERATION_INVALID`，不再伪装成后端故障。
同一 `codex-cli`、模型、suite 和单 worker 复测后，生成成功提高到 29/30，目标 solver 启动
提高到 27/30，首次 OpenFOAM P50 降到 166.2 秒，suite PASS 提高到 16/30。最终只有一个真实
模型命令 timeout 形成 backend deferred，环境/bubblewrap 阻断仍为零。

因此本版的完整判断是：

- 已验证计划与派生网格复用满足快速演示和重复复算目标；
- repair 阶段复用能够避免多数无关前序阶段重跑；
- 全新题目的主要性能风险已从高频结构化输出作废，收敛为模型生成长尾和 case 数值质量；
- 同后端 30 题证据支持冷路径已显著改善，但不能保证每个未知算例都更快或求解正确。

本轮最小真实算例验证中，完全相同 TaskSpec 的热运行：

- 未创建 ModelGateway，计划复用记录为 `hit`；
- 网格缓存记录为 `hit`，`blockMesh` 未再次执行；
- 当前 run 重新执行 `checkMesh` 和 `icoFoam`；
- 0.406 秒启动第一条 OpenFOAM 命令；
- 约 0.520 秒启动目标 solver；
- 1.467 秒完成 manifest 前工作流；
- 0.0038 秒完成 artifact manifest 构建；
- 最终状态为 `PUBLIC_VALIDATION_PASS`，artifact 校验无问题。

这些结果满足 5 秒内启动第一条 OpenFOAM 命令、30 秒内启动目标 solver 的演示目标。它们验证
的是本机 native/reuse 路径，不是在线模型服务延迟或 CFD 泛化准确率。

## 2. 实施内容

### 2.1 统一性能证据

新增 `foampilot.performance` 包。每个终态 run 写出 `performance-summary.json`，它只聚合现有：

- `workflow-events.jsonl`；
- `model-attempts.jsonl`；
- `execution-plan.json` 与每个 attempt 的 `run-result.json`；
- `performance-context.json` 中显式记录的复用状态。

摘要区分 `cold`、`warm_plan`、`warm_mesh` 和 `repair_reuse`，记录首条 OpenFOAM 命令延迟、
各阶段耗时、模型逻辑请求、传输次数、退避和 cache/reuse 状态。证据缺失时保留 `null` 或诊断，
不推测耗时。`artifact-manifest.json` 自身记录 `build_seconds`，避免归档后循环改写。

TaskBuilder 位于 solve run 之前，因此 `foampilot task draft` 在 draft 旁单独写
`<output>.performance.json`。qualification report schema v3 增加冷/热 pre-solve、端到端延迟、
目标 solver 进入、正常结束、公开验证、blocker、cache 和 repair 汇总。

### 2.2 已验证 ExecutionPlan 显式复用

新增入口：

```bash
foampilot solve TASK.yaml \
  --reuse-verified-plan SOURCE_RUN \
  --run-root RUNS \
  --json
```

source run 必须通过 manifest 校验，并具有 manifested 网格命令、`checkMesh: Mesh OK` 和目标
solver 正常结束证据。当前 TaskSpec、公开资产字节、OpenFOAM 目标、solver 可用性和 MPI 预算
必须严格兼容。拒绝返回 `PLAN_REUSE_REJECTED`，发生在 case 物化前，且不会静默调用模型。

复用只改变计划来源。当前 normalizer、policy、semantic inspection、Runner、公开验证和新
artifact manifest 全部保留。

### 2.3 内容寻址的几何与网格缓存

新增显式入口：

```bash
foampilot solve TASK.yaml --derived-cache CACHE_ROOT ...
```

缓存键包含几何事实、公开资产实际字节、mesh intent、网格依赖文件、网格命令、region 和
OpenFOAM/Gmsh 指纹。entry 使用独立 manifest 和原子写入；损坏 entry 会移到缓存根目录下的
`invalid/`，不会当作命中。恢复使用复制，不使用可写 hardlink。

网格命中只跳过 `stage=mesh` 命令。当前 `checkMesh`、质量阈值评价、solver 和公开验证仍执行。
动态网格、依赖不明确等情况保守 miss。qualification 默认不提供缓存根目录。

### 2.4 repair 阶段级复用

repair 变更集合会被分类为最早重跑阶段：

| 变更 | 最早重跑阶段 |
| --- | --- |
| 网格、patch、include、动态网格或命令拓扑 | `mesh` |
| `0/` 初始场 | `initialize` |
| `fvSchemes`、`fvSolution` 等求解字典 | `solve` |
| 仅后处理配置 | `postprocess` |

满足条件时，新 attempt 复制 parent 的允许前序产物，记录 parent/child hash 和
`execution-reuse.json`，并始终保留当前 `checkMesh`。多区域、动态网格、并行分解/重构或证据
不足时完整重跑。parent attempt 不被修改。

### 2.5 模型调用最小化与 fail-fast

审计确认现有边界已满足规格，无需改写 Gateway：

- 直接 TaskSpec 不触发 TaskBuilder；
- 显式 solver 或唯一兼容候选走确定性路由；
- case authoring 保持一次完整 bundle 逻辑请求；
- `BACKEND_MISCONFIGURED`、`AUTH_FAILED` 等确定性 backend 错误在单个 backend 内只传输一次，
  不退避；
- 普通 solve 可以立即 failover 到下一健康 backend，qualification 固定 backend/model。

本轮补充了配置错误“单次 transport、零 sleep”的回归契约，没有根据小样本臆调 timeout。

## 3. 验证证据

### 3.1 确定性测试

```text
494 passed, 5 skipped in 15.95s
```

覆盖性能聚合、TaskBuilder 分离计时、qualification 汇总、计划复用拒绝与命中、几何/网格
cache、repair 阶段分类、parent 不可变、Gateway fail-fast、continuation 和既有状态机。

### 3.2 环境与模型后端

- `foampilot preflight --json`：`PASS`；
- bubblewrap 探测发现 `NETLINK_ROUTE` 权限限制后立即选择 audited host，没有权限交互或长时间等待；
- `foampilot model doctor --json`：`PASS`，`codex-cli` health check 约 0.055 秒。

### 3.3 wheel

```text
foampilot-0.1.0-py3-none-any.whl
sha256 365363b3e290725705e3dcd5bdbba9c434c2e03b78c8f097e39c96fa31d0a706
```

已检查 wheel 包含 `foampilot/performance` 的 models、reporting、plan reuse、derived cache 和
repair reuse 模块，以及 `foampilot/plans/input_normalizer.py`。

### 3.4 真实 OpenFOAM gates

以下三个 opt-in 测试共同执行为：

```text
3 passed in 7.83s
```

- 自然语言 TaskDraft → TaskSpec → canonical `NativeAgent.solve()` → `icoFoam`；
- solver failure → repair backend 暂缓 → immutable child continuation → 成功；
- cold run 生成 source/cache → 零模型 warm plan + mesh cache → `checkMesh` + `icoFoam`。

性能 gate 单独复测：

| 指标 | cold fixture run | warm plan + mesh run |
| --- | ---: | ---: |
| 首条 OpenFOAM 命令 | 0.465 s | 0.406 s |
| manifest 前工作流 | 1.621 s | 1.467 s |
| mesh/check 阶段 | 0.228 s | 0.114 s |
| solver | 0.715 s | 0.715 s |
| manifest 构建 | 0.0041 s | 0.0038 s |
| 执行命令 | blockMesh, checkMesh, icoFoam | checkMesh, icoFoam |

该 gate 的冷计划由仓库冻结的测试模型返回，未访问在线模型；因此表中 cold generation 约
0.007 秒和 trace 中零 transport 不能用于描述真实 LLM 延迟。热运行本身确实以 `gateway=None`
执行，因而其零模型调用是受代码和测试共同约束的行为。

保留的临时证据位于：

```text
/tmp/foampilot-performance-v1-real-gate-20260805-v2
```

## 4. 边界与后续观测

- 性能复用提高重复复算和演示速度，不提高首次未知算例的 case 编写准确率；
- 新任务仍需要一次主要 generation，真实网络模型 p50/p95 应从后续 suite 的
  `performance-summary.json` 聚合，而不是由冻结 gate 代替；
- 缓存命中不代表物理正确，`PUBLIC_VALIDATION_PASS` 也不等价于 qualification `PASS`；
- 当前不做模糊任务匹配、自动历史搜索、solver checkpoint/restart、动态网格缓存或跨版本复用；
- 如果后续冷路径报告显示 generation 之外还有稳定瓶颈，再用真实 p50/p95 决定是否调整预算或
  增加新的确定性优化，不先引入 Rust/C++ 或新的编排层。

## 5. 30 题真实冷路径复测

### 5.1 运行口径

本轮使用与旧基线相同的 suite、模型名称和串行调度：

```text
suite: official-corpus-30-baseline-v1
model: gpt-5.6-sol
workers: 1
OpenFOAM: Foundation v10
backend: codex-cli
run root: /tmp/foampilot-performance-v1-cold-30-20260805-v2
```

qualification 按设计禁用 verified-plan reuse 和 derived cache，因此这轮测量的是未知 TaskSpec 的
完整冷路径，不测重复复算速度。求解器可以按 Agent 生成的 typed command 使用 MPI；suite 本身
保持单题串行，避免并发模型请求和算例之间的资源竞争。

旧基线使用 `codex-oauth`，本轮使用当前 `codex-cli`。两者模型名相同，但传输实现、服务负载与
结构化输出行为不同，因此下表属于真实运行条件下的 operational comparison，不是只改变一项
代码的严格因果 A/B。两轮 prompt/request 字节量接近，不能用上下文体积增长解释本轮延迟翻倍。

第一次在受限嵌套沙箱中启动的批次位于：

```text
/tmp/foampilot-performance-v1-cold-30-20260805-v1
```

它因 `codex` 初始化目录只读而产生 30 个 `DEFERRED_BACKEND`，只证明共享 circuit breaker 在
前两题后使剩余 28 题快速停止请求；该批次不计入 CFD 或性能结果。随后在允许模型与
bubblewrap 的宿主环境中执行的 `v2` 批次才是有效基线。有效批次内部没有 environment blocker
或权限等待。

### 5.2 总体结果

| 指标 | 2026-08-03 旧基线 | 2026-08-05 Performance v1 | 变化 |
| --- | ---: | ---: | ---: |
| generation success | 30/30 | 22/30 | -8 |
| native execution started | 28/30 | 22/30 | -6 |
| checkMesh pass | 28/30 | 22/30 | -6 |
| target solver started | 28/30 | 20/30 | -8 |
| solver normal completion | 20/30 | 14/30 | -6 |
| public validation pass | 18/30 | 13/30 | -5 |
| strict physics qualification pass | 10/15 | 7/15 | -3 |
| suite PASS | 17/30 | 11/30 | -6 |
| backend/provider deferred | 0/30 | 8/30 | +8 |
| environment blocked | 0/30 | 0/30 | 0 |
| logical model requests | 47 | 42 | -5 |
| transport attempts | 51 | 56 | +5 |
| 累计模型时间 | 3989.9 s | 7374.3 s | +84.8% |
| 累计 OpenFOAM 时间 | 1403.3 s | 1000.6 s | -28.7% |
| 累计单题墙钟 | 5503.2 s | 8520.2 s | +54.8% |
| 单题墙钟 P50 | 147.0 s | 267.5 s | +82.0% |
| 单题墙钟 P90 | 236.0 s | 336.1 s | +42.5% |
| 首个 OpenFOAM P50 | 130.5 s（28 题） | 243.5 s（22 题） | +86.5% |
| 首个 OpenFOAM P90 | 164.6 s（28 题） | 287.9 s（22 题） | +74.9% |

OpenFOAM 累计时间下降不是 solver 加速证据，而是本轮少了 8 个目标 solver 启动，并有多个
solver 很快失败。真正导致总时间增长的是模型阶段：42 个逻辑请求产生 56 次 transport，模型
时间占累计单题墙钟约 86.6%。

本轮最终分布为：

```text
PASS                 11
FAIL_AGENT           11
DEFERRED_BACKEND      8
BLOCKED_ENVIRONMENT   0
```

30/30 artifact manifest 已重新独立校验，没有缺失文件、额外文件或 hash mismatch。机器报告为：

```text
/tmp/foampilot-performance-v1-cold-30-20260805-v2/
  official-corpus-30-baseline-v1-report.json
  official-corpus-30-baseline-v1-report.md
```

JSON 报告 SHA-256：

```text
40e2b7cde28901dd644bdb686e8e2e157b99917667140363b2b4a3ade7d522eb
```

### 5.3 冷路径主要瓶颈

56 次 transport 中记录到：

- 20 次 `SCHEMA_INVALID`，涉及 18/30 个算例；
- 2 次 `TIMEOUT`；
- 34 次成功 transport。

18 个发生 schema 错误的算例中，12 个最终通过结构纠正获得可执行 plan；另外 6 个最终仍以
`SCHEMA_INVALID` 暂缓。`solid-plate-hole` 在首份 schema 无效后，第二次传输耗尽剩余 deadline，
最终表现为 `TIMEOUT`。高频错误主要是：

- command `step_id` 未满足小写标识规则；
- manifest 中同一 region 的字段身份重复；
- 其他完整 ExecutionPlan 结构不符合 schema。

这些错误发生在完整 case 文本已经生成之后，却使数分钟输出整体作废。更严重的是，当前 summary
将其写成 `Model transport is unavailable` 和 `DEFERRED_BACKEND`。这会把确定性的生成契约错误
误报成环境/传输问题，不利于恢复、统计和受控学习。

两个真实 timeout 均在受控期限内结束：单 transport 最长约 300 秒，逻辑生成 deadline 约
360 秒。由此可以确认当前不会无界等待，但 300--360 秒对交互演示仍然过长。

### 5.4 本版已体现的复用收益

尽管 qualification 禁用了跨 run plan/cache 复用，本轮 12 题进入 repair：

- 9/12 题安全应用阶段级复用，从 `solve` 阶段重启；
- 3/12 因 MPI、网格字典变化等原因保守回退到 `mesh`；
- 3/12 最终由修复转为成功。

这证明 repair reuse 能在不同 solver-family 中泛化，而不是只针对最小 `icoFoam` gate。由于没有
为每个 repair 同时运行一份“强制全量重跑”的反事实副本，本报告不虚构其累计节省秒数。

相同 TaskSpec 的显式 verified-plan + mesh-cache 路径仍保持独立的真实证据：旧的在线冷启动
约 65.7 秒才执行首条 OpenFOAM 命令，而热运行约 0.406 秒，约 161.7 倍；热运行约 0.520 秒
启动目标 solver，约 1.467 秒完成 manifest 前工作流。这个结论只适用于已验证的相同 TaskSpec，
不外推到新题冷启动。

### 5.5 下一轮性能优先级

大样本结果不支持继续优先优化 Python 本地执行开销，也不支持改用 Rust/C++。下一轮应保持主
流程不变，优先处理模型边界：

1. 对 `step_id` 做无歧义、本地、可审计的规范化，并保留原值与变换记录；
2. 对重复 manifest field identity 做保守去重，只有语义冲突才拒绝；
3. 将 plan 内容错误归为 `GENERATION_INVALID`，不再标为 backend/environment unavailable；
4. schema 修正只发送错误路径与必要结构，不重新生成完整 case bundle；
5. 为交互演示设置更短的 provider profile，同时保留长批 qualification profile；
6. 修正 qualification 中 `warm_path_pre_solve` 的命名：当前 9 个样本是同一 run 的
   `repair_reuse`，不是跨 run 的 verified-plan warm path。

在完成这些轻量改动前，Performance v1 可以用于已验证算例的快速演示和复算，但全新算例的
首次生成仍不满足“稳定、较快进入求解”的演示目标。

## 6. 结构化输出轻量修正与同后端复测

### 6.1 修正边界

根据 5.3 的失败证据，本轮没有增加新状态机、模型调用、solver-family 特例或机械审查，而是只
调整模型输出到 canonical ExecutionPlan 的边界：

- `step_id` 作为内部标签，可以确定性转为小写安全标识，并以序号解决标签碰撞；
- manifest field 只有内容完全等价时才去重；identity 相同但内容冲突时仍拒绝；
- 每次本地修正记录 code、location、original 和 normalized，不保存 prompt 或 case 正文；
- 无法形成 canonical ExecutionPlan 的最终输出归为 `GENERATION_INVALID` / plan failure，
  qualification 记为 `FAIL_AGENT`，不再写成 backend/environment unavailable；
- OpenFOAM 文件内容、typed command 语义、安全 policy、Runner 和 evaluator 均未放宽。

未被本地规则覆盖的 schema 错误仍保留一次模型结构纠正，因此该版本没有宣称已经实现任意
ExecutionPlan 的确定性修复。当前真实复测中也没有命中“完全重复 manifest field”规则；该规则
由回归测试验证，真实收益主要来自 `step_id` 规范化。

### 6.2 复测口径与结果

复测保持与 5.1 有效 `v2` 批次相同的 suite、`codex-cli`、`gpt-5.6-sol`、`workers=1` 和
Foundation OpenFOAM v10，仅加入上述轻量修正：

```text
run root: /tmp/foampilot-performance-v1-cold-30-normalized-20260805-v1
```

| 指标 | 修正前 codex-cli | 修正后 codex-cli | 变化 |
| --- | ---: | ---: | ---: |
| generation success | 22/30 | 29/30 | +7 |
| native execution started | 22/30 | 29/30 | +7 |
| checkMesh pass | 22/30 | 28/30 | +6 |
| target solver started | 20/30 | 27/30 | +7 |
| solver normal completion | 14/30 | 21/30 | +7 |
| public validation pass | 13/30 | 20/30 | +7 |
| strict physics qualification pass | 7/15 | 8/15 | +1 |
| suite PASS | 11/30 | 16/30 | +5 |
| backend deferred | 8/30 | 1/30 | -7 |
| environment blocked | 0/30 | 0/30 | 0 |
| logical model requests | 42 | 41 | -1 |
| transport attempts | 56 | 44 | -12 |
| `SCHEMA_INVALID` transport | 20 | 3 | -17（-85%） |
| `TIMEOUT` transport | 2 | 1 | -1 |
| 累计模型时间 | 7374.3 s | 5524.3 s | -25.1% |
| 累计单题墙钟 | 8520.2 s | 6426.7 s | -24.6% |
| 单题墙钟 P50 | 267.5 s | 189.5 s | -29.2% |
| 单题墙钟 P90 | 336.1 s | 308.5 s | -8.2% |
| 首个 OpenFOAM P50 | 243.5 s（22 题） | 166.2 s（29 题） | -31.7% |
| 首个 OpenFOAM P90 | 287.9 s（22 题） | 261.5 s（29 题） | -9.2% |

最终 qualification 分布为：

```text
PASS                 16
FAIL_AGENT           13
DEFERRED_BACKEND      1
BLOCKED_ENVIRONMENT   0
```

44 次 transport 中，8 次成功传输记录了本地规范化，共修正 27 个 `step_id`。剩余 3 次
`SCHEMA_INVALID` 均通过一次模型纠正获得有效 plan；唯一最终暂缓是 `cht-cooling-cylinder`
在 300 秒处发生真实外部模型命令 timeout。后续题仍正常执行，说明单题暂缓没有使 suite 停止。

### 6.3 真实 gate 与审计证据

在 30 题前单独运行 `compressible-shock-tube` 冷路径：模型首份输出包含两个非法大小写
`step_id`，本地规范化后只使用一次 transport，随后完整执行：

```text
blockMesh -> checkMesh -> setFields -> rhoCentralFoam
```

该 run 在 131.6 秒启动首条 OpenFOAM 命令，目标 solver 正常结束，最终为
`PUBLIC_VALIDATION_PASS`：

```text
/tmp/foampilot-stage1-family-gates-20260804/
  run-20260805T085754925504Z-863f8293
```

30 题报告位于：

```text
/tmp/foampilot-performance-v1-cold-30-normalized-20260805-v1/
  official-corpus-30-baseline-v1-report.json
  official-corpus-30-baseline-v1-report.md
```

报告 SHA-256：

```text
JSON  5eab05a828b86169846d0eb8c0eb7a87307deb6942834969ed227aa8d6296b92
MD    029c4ef8517232ba10c3ebacaa601bd1f869fedd9bcde91185d9ea49d9d25df0
```

30/30 run 的 artifact manifest 已使用 `ArtifactStore.verify()` 独立复核，问题数为零。全仓测试为：

```text
494 passed, 5 skipped in 15.95s
```

### 6.4 解释与剩余边界

这是一组同后端、同模型、同 suite、同 worker 的受控 operational comparison，比 5.2 的跨后端
比较更有解释力；但在线模型输出和服务负载仍有随机性，因此不是字节级确定性实验。性能、进入率
和通过率同时改善，且真实 trace 中出现 27 次对应规范化记录，结果与修正机制一致。

修正后的主要问题已经不再是大批算例在 schema/环境层无法进入求解，而是：

- 一个多区域 CHT 任务发生真实 300 秒模型 timeout；
- 13 个 `FAIL_AGENT` 主要位于网格、solver、postprocess 或严格物理指标层；
- 可压缩激波、两液体和部分浮力场景仍需要更准确的 solver-family 知识与数值策略；
- 未知任务首个 OpenFOAM P50 仍为 166.2 秒，不适合作为即时交互体验；已验证相同 TaskSpec 的
  warm plan 路径仍是当前最稳定的快速演示方式。

因此下一步不应继续扩大前求解机械审查，而应保持当前主流程，分别优化模型生成长尾和真实 CFD
case 质量。`GENERATION_INVALID` 的错误分层和本地规范化已经达到本轮轻量修正目标。
