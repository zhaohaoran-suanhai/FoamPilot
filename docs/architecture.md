# 架构说明

FoamPilot 只支持一条从公开 TaskSpec 到证据限定结果的规范路径。

## 组件

- `tasks`：严格校验公开要求与资源预算；
- `knowledge`：包含来源信息的已审查 Foundation OpenFOAM v10 知识；
- `skills`：用于算例编写和特定 solver family 的可移植行为指导；
- `routing`：基于证据选择 solver family，并由系统计算 confidence；
- `context`：每个语义槽位最多选择一条有界公开知识，并装配通用/族级 Skill；
- `manifests`：薄型、支持 region 的算例声明，以及带来源的 family contract；
- `models`：单次交换 provider client、共享 `ModelGateway`、retry/deadline policy、
  transport trace、lineage budget 与线程安全 circuit breaker；
- `agent`：构造 prompt、编写完整 case bundle 和执行有界 repair；
- `workflow`：有序持久事件、独占 checkpoint、v2 run state、严格兼容性指纹，以及不可变
  parent/child continuation；
- `plans`：ExecutionPlan v3、完整生成文件、分阶段 typed native command 与狭窄安全的
  MPI launcher normalizer；
- `inspection`：算例内静态安全检查与高置信度跨文件语义检查；
- `runtime`：Foundation v10 发现、bubblewrap/audited-host 自动选择、显式 MPI 启动、预算与日志；
- `validation`：由 evaluator 负责检查命令、日志与写出字段；
- `artifacts`：不可变 attempt 与 SHA256 manifest；
- `qualification`：按角色执行 suite，并使用紧凑 packaged reference 做外部物理比较。

## 数据流

环境发现后，确定性 router 创建 `CapabilityProfile`。其依据包括任务中的显式事实、已安装
executable 和已审查 solver-family metadata。只有 candidate set 含糊时才允许请求模型
辅助路由，且模型不能自行提高 confidence。低置信度或信息不完整的 route 会在完整算例编写前停止。

ContextAssembler 随后在 solver-family、mesh、boundary、physics/transport、
startup/numerics、可选 parallel 与可选 repair-error 槽位中各选择至多一条知识。缺少匹配
时记录空槽位，而不是用无关 top-N 结果填充。不能从通用词安全推断的跨 solver 条目带有显式
`activation_terms`；除非公开任务明确出现对应概念，否则不会加载。模型看到有界公开上下文、
事实环境清单，以及至多一个通用 Skill 和一个 family Skill；看不到目标 tutorial、受保护路径、
evaluator validation YAML 或 reference JSON。

一次逻辑 generation request 返回全部必需 case file、一个 region-aware `CaseManifest`，
以及 ExecutionPlan v3 中全部分阶段 typed command。`ModelGateway` 可在单调时钟 stage
deadline 内进行多次 transport attempt，但会分别记录逻辑请求与实际传输。两个 qualification
worker 只共享 Gateway 和 circuit breaker；每项任务保留独立 deadline ledger、trace、case、
artifact store 与 evaluator workspace。

进入 policy 前，normalizer 只会拆解无歧义的本地
`mpirun|mpiexec|orterun -n N solver [-parallel]` 形式。确定性 policy 检查安全性、
已安装 executable、路径、受保护数据、资源限制与命令形态。Semantic inspection 检查
manifest/solver/application、region/field path、显式 mesh patch、command stage、MPI
decomposition 和已审查 family requirement。未登记 family 只产生 advisory。随后 OpenFOAM
直接读取模型编写的 dictionary。

标记为 `author` 或 `public_asset` 的 field 必须在执行前存在。标记为 `mesh`、`initialize`
或 `solver` 的 field 只检查 region/path 一致性，不会错误地要求它们在创建命令前存在。

执行后，公开 validation 判断要求的结果是否存在并满足声明检查。任务若允许另一次 attempt，
repair 模型会收到公开失败证据，以及编写阶段动态选择的同一批公开知识与 workflow Skill。
模型可以修改生成文件或已有 typed command，修订计划在新 attempt 中物化。

## 工作流与失败语义

`workflow-events.jsonl` 是 task、environment、context、generation、plan、
materialization、inspection、OpenFOAM、public-validation、repair 与 finalization 阶段的
有序、fsync 持久化记录。Checkpoint 采用独占写入，绝不替换。

`RunSummary` schema v2 分离三个问题：

- `workflow_state`：`COMPLETED`、`FAILED` 或 `DEFERRED`；
- `native_status`：如果发生过 native execution，则记录最新 CFD/native 结果；
- `primary_failure` 与 `terminal_blocker`：算例为何失败，以及当前为何无法继续。

例如，solver 可以保持 `SOLVER_FAILED`，同时将 provider overload 独立记录为可重试
terminal blocker。因此 provider deferral 不会被改写为 OpenFOAM 或 Agent 准确性失败。

## 严格续跑

可重试 generation 或 repair 中断会创建新的 child run：

```text
已验证不可变 parent
-> compatibility fingerprint 与 lineage-budget 检查
-> child continuation run
-> 规范 generation 或 scoped repair
-> 规范 inspect/run/validate/finalize
```

parent 永不重新打开。Strict resume 比较 TaskSpec、public asset、model/provider policy、
package content、source revision、plan schema、knowledge、Skill、OpenFOAM target 与
executable capability。Generation 与 repair 各允许至多两个 child continuation，完整
lineage 至多允许七次真实 transport attempt。代码、knowledge、Skill、model 或 policy
变化后必须创建新的 `rerun_with_changes`，不能 strict resume。

历史 RunSummary v1 文件仍可通过 read-only adapter 报告，但不能续跑。

规范 authoring 与 strict resume 只接受 ExecutionPlan v3。历史 v2 replay fixture 使用
狭窄、未导出的 reader，加上独立审查并带 hash 的 v3 manifest overlay；这不是 authoring fallback。

## 隔离

Runner 优先将 attempt case 目录绑定为 `/case`，在 bubblewrap 中关闭网络且不接受 shell
program。`execution_backend=auto` 会对 bubblewrap 做一次有界、缓存的可用性探测；若嵌套
环境拒绝 namespace，则使用同一 typed policy、allowlist、cwd、资源限制和日志契约执行
audited host command。host fallback 不具有 network namespace 隔离，preflight 和每个 step
都会记录实际后端与 fallback 原因。MPI ranks 属于 typed command record，必须处于 TaskSpec
预算内。

Evaluator-only qualification 在已完成 case 的临时副本上运行，因此 VTK marker file 与
post-processing 不会修改 artifact manifest。

`tests/fixtures/artifact-replay` 下的确定性 replay gate 包含经过边界限制和 secret scan
的 single-region、MPI、include、buoyant、multi-region 与 known-failure 历史产物。
Replay 用于保护兼容性，不能替代 native qualification。

当前端到端状态机、实测阶段耗时、失败分类与 operational-readiness 边界，见
[运行流程与求解前健康度分析](runtime-workflow-and-pre-solve-health-analysis.md)。
