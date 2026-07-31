# 受控评测

`controlled-learning-15-v1` 套件用于检查 FoamPilot 能否在不读取目标 tutorial 的
情况下，独立编写并求解 15 个具有代表性的 Foundation OpenFOAM v10 算例。

六个回归算例覆盖瞬态层流方腔、圆柱绕流势流、稳态 RANS 突扩流、两相液柱坍塌、
可压缩激波管以及湍流浮力方腔。

六个开发算例覆盖标量输运、层流 Maxwell 流动、多孔介质 RANS 流动、可压缩阻塞
通道、共轭传热以及单参考系转子。

三个冻结 holdout 算例覆盖磁流体 Hartmann 流、毛细上升以及带孔平板周围的线弹性
固体位移。算例角色只控制证据可以如何使用，不改变求解路径。

## 信息隔离

每份公开 TaskSpec 描述几何、物理、资源、输出和公开验收要求。模型不会收到
validation YAML 或 reference JSON。只有在 native 运行通过公开检查且 artifact
manifest 验证无误后，qualification 层才会读取这些 evaluator 资产。

仓库只包含紧凑的派生参考指标，不包含官方 tutorial case 目录或求解时间目录树。

## 执行方式

```bash
foampilot qualify suite \
  --suite-file \
    src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root /tmp/foampilot-controlled-learning-15 \
  --workers 2 \
  --model-name gpt-5.6-sol \
  --json
```

最多同时运行两个普通算例。标记为 `exclusive` 的算例，例如较大的浮力和 CHT 算例，
会独占运行。每个算例保留其 TaskSpec 定义的 attempt、wall-time、内存和 MPI 预算。
兼容命令 `foampilot qualify official-six` 只运行六个回归算例。

对于相同的 provider、model 和 account identity，所有 worker 共享一个线程安全的
模型 Gateway 和 circuit breaker。它们不共享任务 deadline、lineage budget、trace、
ArtifactStore、case 或 evaluator workspace。如果持续的 provider 过载或网络故障
打开熔断器，后续任务在 cooldown probe 到来前会直接暂缓，不再发起新的 HTTP
请求。

每个算例都使用同一条规范运行路径：

```text
TaskSpec
-> CapabilityProfile
-> 按槽位组织的上下文
-> ExecutionPlan v3
-> 规范化、策略和语义检查
-> 原生 OpenFOAM
-> 公开验证
-> 外部 qualification
```

Qualification YAML 是 evaluator 配置，不是逐算例的 case 编写适配器。它不会预选
知识 ID，也不会提供隐藏 case 模板。

## 判定结果

- `PASS`：native 公开验证、manifest 验证以及全部必需外部指标均通过；
- `FAIL_AGENT`：case 编写、执行、公开验证、manifest 或物理指标失败；
- `DEFERRED_PROVIDER`：可重试的 provider 故障中断了生成或修复，不属于
  Agent/CFD 失败；
- `BLOCKED_ENVIRONMENT`：OpenFOAM、沙箱或其他本地运行依赖不可用；
- `INVALID_QUALIFICATION`：缺少必需的 evaluator 证据。

仅仅完成求解器执行，不代表 qualification 通过。

`REQUEST_INCOMPLETE` 和 `ROUTING_UNRESOLVED` 发生在生成前，并与 provider、环境、
case、求解器和物理 qualification 失败分别报告。模型响应未通过 v3 schema 时记为
`PROVIDER_SCHEMA_INVALID`；trace 只保存有界的校验位置、类型和消息，不保存原始
响应。

## 阶段指标

Qualification 报告会分别统计：

- 逻辑模型请求和真实传输尝试；
- 生成成功和任意 OpenFOAM 工具启动；
- 网格生成和 `checkMesh` 成功；
- 目标求解器启动和目标求解器正常结束；
- 公开验证和私有物理 qualification；
- 模型耗时和 OpenFOAM 执行耗时。

目标求解器进入率定义为
`target_solver_started / valid_case_bundles`；只执行 `blockMesh` 不满足该指标。
条件求解通过率定义为
`public_validation_pass / target_solver_started`。

对于 continuation 结果，模型请求数、传输尝试数和模型耗时会在经过验证的
parent/child lineage 上累计。每个单独运行的产物仍然保持独立固化。

## 当前证据

2026-07-30 冻结的 15 题全量基线获得 11 个严格通过和 4 个失败。全部 15 个算例
都进入了请求的目标求解器，其中 14 个到达公开验证，另一个 CHT 算例在求解器中
失败。经过小范围、可泛化的知识和 evaluator 修正后，四个受影响求解器族的定向
复测均通过。这些定向结果证明修正有效，但不能表述为一次新的随机性 15/15 全量
运行。

逐算例证据、失败分析和准确的结论边界见
[15 题受控学习报告](reports/2026-07-30-controlled-learning-15.md)。

更早的独立真实算例 gate 在 2026-07-29 达到 2/2
`PUBLIC_VALIDATION_PASS`。它验证了从已安装 wheel 执行 case 编写和 repair 的闭环，
不等价于严格的 15 题物理 qualification。详见
[独立真实算例 gate 报告](reports/2026-07-29-standalone-real-gate.md)。
