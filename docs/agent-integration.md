# Agent 集成

## 稳定边界

FoamPilot 是与框架无关的执行边界。外部 Agent 可以调用 CLI 或 Python API，但本工具包
不依赖外部 agent framework。

外部 Agent 不得直接运行 OpenFOAM、修改不可变 attempt、读取官方目标 tutorial、检查私有
golden data，或自行判定正式 benchmark PASS。

## 规范原生闭环

0. 可选：把自然语言和显式公开附件通过 `TaskDraft -> DraftReview -> TaskCompiler` 编译为
   公开 `TaskSpec`；任何 blocking/confirmable 问题必须先解决。
1. 加载公开 `TaskSpec`。
2. 原子检查公开资产；provided polyMesh 在模型调用前生成权威 `InputMeshFacts`。
3. 发现 Foundation OpenFOAM v10 与已安装原生 executable，并检索有界公开知识。
4. 模型只解释 `SimulationIntent`；程序解析权威需求并暴露缺失/冲突。
5. 模型只提出一个完整 `CaseDesignProposal`；RiskGate 决定是否可以冻结。
6. 程序编译 `AcceptancePlan` 与 `ObservationPlan`。
7. 模型根据冻结 CaseDesign 一次编写全部相关 case 文件，不生成命令。
8. CaseVerifier 检查一致性；PlanCompiler 从第一方扩展生成 typed command。
9. 物化、静态检查，并通过 Runner 执行 Foundation v10。
10. EvidenceExtractor 一次生成 `RunFacts`，再生成 `DerivedMetrics` 与 `ResultReport`。
11. 只允许使用已配置、由证据和冻结 envelope 限定范围的 repair 预算。

规范路径中不存在逐文件 model loop、model reviewer、预选 knowledge-ID allowlist、CaseSpec
renderer 或模型拥有的执行命令。三个模型阶段是串行且职责单一的：理解任务、形成完整设计、
编写完整 CaseBundle。

### 保守求解前 gate

求解前检查只阻止机械上确定的缺陷，例如显式缺少 boundary patch，或已知与 Foundation
v10 不兼容的 dictionary construct。如果 include、substitution 或其他动态语法使结果不确定，
inspection 只记录 advisory，并让 OpenFOAM 自己判定。

阻断性静态缺陷与 runtime failure 消耗同一份由证据限定的 repair 预算，不会在 Agent 有机会
修正文件前终止整个任务。执行期间，即使 `checkMesh` 返回零，只要明确出现
`Failed N mesh checks`，后续 solver command 也会停止。含糊或未知日志文本只作为证据保留，
不会被自动固化为新的 hard-coded failure。

## 机器可读命令

```bash
foampilot task draft --request-file REQUEST.md --output DRAFT.yaml --json
foampilot task validate-draft DRAFT.yaml --json
foampilot task compile DRAFT.yaml --output TASK.yaml --json

foampilot validate TASK.yaml --json
foampilot plan TASK.yaml --output PLAN.json --model-name MODEL --json
foampilot solve TASK.yaml --run-root RUN_ROOT --model-name MODEL --json
foampilot inspect TASK.yaml PLAN.json CASE_DIR --json
foampilot report RUN_DIR --json
foampilot results RUN_DIR --json

foampilot knowledge validate src/foampilot/knowledge/openfoam10 --json
foampilot knowledge search src/foampilot/knowledge/openfoam10 "QUERY" \
  --formal --limit 8 --json
foampilot skill validate \
  src/foampilot/skills/openfoam-author-native-case --json

foampilot improve analyze RUN_DIR \
  --qualification-report BASELINE.json \
  --candidate-id CANDIDATE \
  --lesson "GENERAL LESSON" \
  --target knowledge \
  --output IMPROVEMENTS/candidate.yaml
foampilot improve compare BASELINE.json CURRENT.json \
  --candidate IMPROVEMENTS/candidate.yaml \
  --output IMPROVEMENTS/promotion.json \
  --json
```

退出码：0 表示命令成功，2 表示 CLI 输入无效，3 表示 environment block，4 表示执行或
显式 acceptance 失败，5 表示未预期内部错误。

## Python 边界

主要集成类型包括：

- `TaskDraft`, `DraftReview`, `TaskCompilation`;
- `TaskSpec`;
- `AssetBundle`, `InputMeshFacts`, `SimulationIntent`, `CaseDesign`;
- `ObservationPlan`, `CaseBundle`, `ExecutionPlan`, and `NativeCommand`;
- `RunFacts`, `DerivedMetrics`, and `ResultReport`;
- `NativeAgent`;
- `RuntimeConfig` and `PlanRunner`;
- `ArtifactStore`.

`NativeAgent.solve()` 负责完整状态机。Adapter 应保留其 JSON outcome 与 artifact path，
而不是重新实现 intent、design、authoring、execution、evidence、acceptance 或 repair。

TaskBuilder 是求解前边界，不持有 Runner，也不创建 solve run。上游 Agent 可以使用
`extract_task_draft()`、`validate_task_draft()` 和 `compile_task_draft()`，但不得把模型推断的
高影响值直接改写为 `user_confirmation`。完整 TaskSpec 产生后必须交给同一个
`NativeAgent.solve()`，不能从对话层直接调用 OpenFOAM。

## 检索与泄漏边界

正式 retrieval 排除 development-only 条目。工具包记录被选择 knowledge ID 与 source hash
用于溯源，但任务本身不选择这些 ID。

只有通用公开材料可以进入 Agent prompt。当前目标 tutorial path、私有 validator、
golden value 与 source mapping 始终位于 Agent 边界之外。

## 离线改进边界

improvement command 是在冻结证据上运行的 developer workflow，不属于
`NativeAgent.solve()`：

```text
冻结 solve/qualification
-> improve analyze
-> developer 应用一项 candidate change
-> 重新运行 qualification
-> improve compare
-> 显式 promotion decision
```

系统不会自动 promotion。Analyzer 先验证 artifact manifest，并要求匹配的 qualification
result，之后才能为可选官方 example 计算 hash。盲编写与 repair 期间无法访问官方 example；
只有事后才可检查它们以提取通用原则。其路径、完整 dictionary、目标专用几何、golden value
和 evaluator tolerance 绝不进入 model context。

Learning candidate 与 promotion report 只写入 developer 在 run root 旁选择的路径，
不写入不可变 run；未经显式审查与批准，也不会成为 package knowledge 或 Skills。

## 单一执行路径

早期私有模型边界、CaseSpec/renderer 与 Agent 编写 `Allrun` 的路径不属于 FoamPilot。
所有集成都使用上述原生闭环，不存在 legacy fallback 或兼容命令面。

当前评测边界见[受控评测](qualification.md)。
