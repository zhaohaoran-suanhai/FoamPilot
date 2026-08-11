# 架构说明

FoamPilot 只支持一条从公开 TaskSpec 到证据限定结果的规范求解路径。自然语言 TaskBuilder 是
可选的求解前编译边界，最终仍产生同一个 TaskSpec，不建立第二条 Runner 或状态机。

## 组件

- `taskbuilder`：从自然语言和显式公开附件 metadata 提取带来源事实，检查缺失信息并确定性
  编译 TaskSpec；
- `tasks`：严格校验公开要求与资源预算；
- `preprocessing`：在路由前探测几何 hash、单位、bounds、surface/patch/region 事实，并从原生
  日志生成 MeshQualityReport；
- `knowledge`：包含来源信息的已审查 Foundation OpenFOAM v10 知识；
- `skills`：用于算例编写和特定 solver family 的可移植行为指导；
- `routing`：基于证据选择 solver family，并由系统计算 confidence；
- `context`：每个语义槽位最多选择一条有界公开知识，并装配通用/族级 Skill；
- `manifests`：薄型、支持 region 的算例声明，以及带来源的 family contract；
- `models`：单次交换 backend、共享 `ModelGateway`、retry/deadline policy、
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

完整自然语言请求可以先经过 `TaskExtractor -> DraftValidator -> TaskCompiler`。模型只能做
结构化事实提取；用户原文与附件来源由系统复核，高影响模型推断必须确认，系统默认值只由
Compiler 引入。TaskBuilder 不调用 Runner，失败也不创建 solve run。qualification 继续直接
使用冻结 TaskSpec。

环境发现后，确定性 router 创建 `CapabilityProfile`。其依据包括任务中的显式事实、已安装
executable 和已审查 solver-family metadata。只有 candidate set 含糊时才允许请求模型
辅助路由，且模型不能自行提高 confidence。低置信度或信息不完整的 route 会在完整算例编写前停止。

几何任务会在路由前 staging 已声明 public asset 并生成 `geometry-facts.json`。显式单位、
patch/region role 或 mesh strategy 不得由 probe 猜测；必要的外部网格程序未被环境发现时在零
generation 调用处结束。进入 native execution 后，每个 attempt 生成独立的
`mesh-quality-report.json`，把日志观测值与 TaskSpec 阈值分开保存。

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

例如，solver 可以保持 `SOLVER_FAILED`，同时将 backend overload 独立记录为可重试
terminal blocker。因此 backend deferral 不会被改写为 OpenFOAM 或 Agent 准确性失败。

## 严格续跑

可重试 generation 或 repair 中断会创建新的 child run：

```text
已验证不可变 parent
-> compatibility fingerprint 与 lineage-budget 检查
-> child continuation run
-> 规范 generation 或 scoped repair
-> 规范 inspect/run/validate/finalize
```

parent 永不重新打开。Strict resume 比较 TaskSpec、public asset、model/backend policy、
package content、source revision、plan schema、knowledge、Skill、OpenFOAM target 与
executable capability。Generation 与 repair 各允许至多两个 child continuation，完整
lineage 至多允许七次真实 transport attempt。代码、knowledge、Skill、model 或 policy
变化后必须创建新的 `rerun_with_changes`，不能 strict resume。

历史 RunSummary v1 文件仍可通过 read-only adapter 报告，但不能续跑。

规范 authoring 与 strict resume 只接受 ExecutionPlan v3。历史 v2 replay fixture 使用
狭窄、未导出的 reader，加上独立审查并带 hash 的 v3 manifest overlay；这不是 authoring fallback。

## 隔离

Runner 优先将 attempt case 目录绑定为 `/case`，在 bubblewrap 中关闭网络且不接受 shell
program。统一 Runtime resolver 提供 `sandbox_required`、`sandbox_preferred`、`trusted_host`
三档策略。每个 attempt 在首命令前对 materialized case 生成
`execution-risk-report.json`，执行完整 launch probe，并冻结一次 backend；运行中禁止切换。
只有 `sandbox_preferred`、low-risk case 且 bwrap/namespace 机制不可用时才允许 host fallback。
沙箱 setup 或可信挂载错误绝不降级。audited host 与 bubblewrap 不具有相同安全性：host
没有 network/filesystem namespace。`runtime-config.json`、`sandbox-probe.json` 和
`execution-policy.json` 进入不可变 manifest，MPI ranks 仍必须同时满足 TaskSpec 和 Runtime
预算。

host 后端只执行 EnvironmentSnapshot 中已验证的 canonical executable path，并拒绝模型提供的
case/root 覆盖与绝对参数。最终 materialized case（含 cache restore）会重新扫描宏展开型 include、
type/library、动态代码、命令执行和任意文件更新入口；`.foampilot` 由 Runner 独占。环境发现与 help
探测使用隔离 HOME 和最小环境，避免用户 prefs 或 PATH shadow 改变随后执行的命令事实。

Evaluator-only qualification 在已完成 case 的临时副本上运行，因此 VTK marker file 与
post-processing 不会修改 artifact manifest。

`tests/fixtures/artifact-replay` 下的确定性 replay gate 包含经过边界限制和 secret scan
的 single-region、MPI、include、buoyant、multi-region 与 known-failure 历史产物。
Replay 用于保护兼容性，不能替代 native qualification。

当前端到端状态机、实测阶段耗时、失败分类与 operational-readiness 边界，见
[运行流程与求解前健康度分析](runtime-workflow-and-pre-solve-health-analysis.md)。
