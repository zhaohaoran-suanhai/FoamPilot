# 阶段 1：Knowledge 与 Skills 优化规格

状态：核心实现、阶段 1.1 和阶段 1.2 定向实现已完成；30+20 大回归按最新优先级延期到
Agent Harness v2 P0/P1 之后。当前证据见
[第一阶段实施记录](../reports/2026-08-04-stage-1-knowledge-skills.md)、
[阶段 1.1 报告](../reports/2026-08-05-knowledge-skills-stage-1-1.md)与
[阶段 1.2 报告](../reports/2026-08-06-knowledge-skills-stage-1-2.md)。本文保留完整规格，coverage
中的条目存在不等于求解能力通过验证。

## 1. 背景与目标

当前 FoamPilot 已经可以在长批任务中稳定完成模型调用和原生执行，但 30 题基线仍暴露出：

- solver-family 必需字典或关键词缺失；
- 边界条件、物性与数值格式跨文件不一致；
- 可压缩、多相和多区域算例进入 solver 后发生数值失稳；
- 同一个错误可能需要多轮 repair 才逐步补齐；
- 通用 Skill 之外，只有少数 solver-family 具有专用行为指导。

本阶段只优化 Agent 编写和修复原生 case 的知识质量，不修改 Runner、workflow、artifact 或
qualification 的基本架构。

目标是提高：

1. 初始计划的字典完整率；
2. 目标求解器进入率；
3. 求解器正常完成率；
4. repair 的定向性和一次修复有效率。

## 2. 非目标

- 不为每道官方题编写专用 YAML、Skill、prompt 或 renderer。
- 不把官方 case 文件复制进知识库。
- 不使用私有 golden、validator 规则或目标专用容差指导生成。
- 不要求安装向量数据库或 embedding 模型。
- 不用机械检查替代 Agent 的 CFD 策略选择。
- 不在本阶段加入自然语言入口、Desktop IDE 或复杂几何处理。

## 3. 内容分层

### 3.1 Knowledge Contract

Knowledge 保存可独立检索、适用范围明确的事实和规则。主要类型保持现有分类：

- `solver_guide`；
- `mesh_pattern`；
- `boundary_condition`；
- `physics_model`；
- `numerics`；
- `error_playbook`；
- `parallel_execution`；
- `validation_pattern`。

每条知识至少回答：

- 适用于哪个 Foundation 版本、solver、physics family 和模型；
- 需要哪些文件、字段、关键词和 dimensions；
- 哪些组合必须同时出现；
- 哪些做法只适用于特定条件；
- 常见原生日志信号是什么；
- 可以用哪些公开证据验证；
- 来源、SHA256、许可证和泄漏组是什么。

Knowledge 回答“事实和约束是什么”，不描述完整工作流。

### 3.2 Family Skill

Skill 保存 Agent 的思考、编写、自检和修复顺序。阶段 1 采用物理族，而不是逐 solver 无限
扩张。首批目标族为：

| Skill family | 覆盖重点 |
| --- | --- |
| `incompressible-pressure-velocity` | SIMPLE、PISO、PIMPLE，压力参考、闭域连续性和稳态/瞬态启动 |
| `compressible-transient` | thermo 一致性、波速/Courant、时间步、激波与出口边界 |
| `multiphase-vof` | 相分数初始化、有界性、界面压缩、重力和 `p_rgh` |
| `buoyant-cht` | `p/p_rgh`、thermo、wall function、region 接口、Diffusion number |
| `solid-mechanics` | 位移字段、材料属性层级、约束和载荷边界 |
| `scalar-field-transport` | scalar 初始化、源项、输运属性和 function object 契约 |

旋转、MHD、电静力、浅水等较窄场景先由 Knowledge Contract 支撑。只有跨算例失败证据表明
需要流程级指导时，才新增 family Skill。

运行时最多加载：

```text
一个通用 native-case Skill
+ 一个 physics-family Skill
+ 第二阶段开始后可选的一个 mesh-workflow Skill
```

### 3.3 Failure Playbook

repair 阶段根据公开失败证据选择一个最相关的 playbook：

```text
stage + executable + exit code + normalized log signals
  -> FailureClassifier
  -> failure family
  -> relevant files
  -> one error playbook
  -> scoped repair
```

Failure Playbook 必须说明：

- 可识别的原生日志片段；
- 最可能的跨文件原因；
- 允许检查或修改的文件；
- 修复后必须重跑的命令；
- 不得随意改变的控制量；
- 何时应停止并报告证据不足。

## 4. 检索与上下文装配

现有 slot-based retrieval 继续作为主机制。检索输入扩展为：

```text
TaskSpec facts
+ CapabilityProfile
+ workflow stage
+ repair failure code/log signals（仅 repair）
```

每个 slot 最多选择一条知识。正式 authoring 的建议槽位为：

1. `solver_family_contract`；
2. `mesh_pattern`；
3. `boundary_condition_contract`；
4. `physics_transport_model`；
5. `startup_numerics`；
6. `parallel_execution`（任务需要时）；
7. `error_playbook`（repair 时）。

选择过程先过滤 version、solver、family、visibility、leakage 和 activation terms，再计算相关性。
没有可靠匹配时保持空槽位，不以无关 top-N 填充。

本阶段保留轻量 lexical/metadata retrieval。只有以下证据同时成立时才评估 embedding：

- 条目数量增长后 lexical recall 出现可重复漏召回；
- 漏召回无法通过 metadata、alias 或 activation terms 修正；
- embedding 不引入目标 case 泄漏或显著启动成本。

## 5. 知识覆盖矩阵

增加一份自动生成的 coverage report，以 solver family 为行、知识槽位为列，区分：

- `covered`：存在正式条目并通过 corpus 校验；
- `partial`：只有通用条目或缺少关键模型组合；
- `missing`：没有可用条目；
- `development_only`：存在候选，但不能进入正式 qualification。

coverage report 用于发现系统性缺口，不能把“条目存在”解释为 Agent 已经具备该能力。

## 6. 从官方 example 受控学习

官方 example 只在 attempt 已固化、evaluator 已完成评分之后由 teacher 侧读取：

```text
冻结的 Agent attempt
+ 公开失败报告
+ evaluator-only 官方 reference
  -> 差异分类
  -> 通用原则候选
  -> development-only Knowledge/Skill candidate
  -> development cases
  -> holdout cases
  -> promotion review
```

允许沉淀：

- solver-family 必需文件关系；
- Foundation v10 字典层级和关键词语义；
- 通用时间步、稳定性和边界一致性原则；
- 跨算例可复现的错误诊断方法。

禁止沉淀：

- 完整官方 case 内容；
- 官方路径、basename 或可唯一定位目标的描述；
- 目标几何和 patch 参数集；
- golden 数值、私有 tolerance 或 evaluator 实现；
- 只对单个目标成立的调参结果。

promotion 继续需要人工批准，不允许运行时自动修改正式知识库。

## 7. 对检查器的边界

只有满足以下条件的规则才能成为 blocking semantic rule：

- Foundation v10 中确定成立；
- 与 solver/model family 明确绑定；
- 错误会导致 case 无法解释或无法执行；
- 有来源、版本、测试和 provenance；
- 不需要 CFD 策略判断。

数值格式优劣、网格密度、湍流模型选择和工程收敛标准通常属于 advisory、Knowledge 或
Evaluator，不应成为全局硬拦截。

## 8. 开发子阶段

### 8.1 失败证据归档

- 从现有 30 题报告和已固化 run 中建立 failure-family 清单；
- 区分检查器误拦、缺知识、未遵循知识、数值失稳和 evaluator failure；
- 为每一项指定改进目标：Knowledge、Skill、prompt、inspector 或 evaluator。

### 8.2 Knowledge Contract 补齐

优先补齐已有失败密集族：

- 通用 `blockMesh` 拓扑一致性；
- incompressible pressure/reference；
- compressible transient startup；
- VOF 有界性与初始化；
- CHT region/thermo/time-step；
- solid properties 与 boundary constraint。

### 8.3 Family Skills

按本规格的六个 family 编写并配套 positive、negative、boundary scenario。不得只用文档人工
阅读证明 Skill 有效。

### 8.4 检索与 scoped repair

- 增加 family/alias 覆盖；
- 让 repair query 使用 failure signal；
- 记录选择理由、缺失槽位、上下文大小和 source hash；
- 验证无关知识不会进入 prompt。

### 8.5 受控回归

先执行确定性测试和冻结 replay，再执行目标族复测，最后执行一次冻结配置的 30 题基线。

## 9. 测试策略

### 9.1 确定性测试

- knowledge schema、manifest 和 source hash；
- version/family/leakage/activation filter；
- coverage report；
- Skill 格式和 scenario；
- failure signal 到 error playbook 的路由；
- prompt budget 与 protected-path scan；
- frozen artifact replay 不产生新的错误拒绝。

### 9.2 模型行为测试

每个新增 family Skill 至少包含：

- 一个已知合法的 authoring 场景；
- 一个故意缺失关键字典的 repair 场景；
- 一个不适用场景，证明 Skill 不会被错误加载；
- 一个不同几何或边界的 holdout 场景。

### 9.3 真实 OpenFOAM gate

- 先运行各 family 的最小真实 case；
- 对历史失败族进行至少三次独立生成，避免把单次随机改善当成结论；
- 最后在固定 backend/model/资源和知识快照下运行 30 题。

## 10. 阶段验收

本节保留阶段 1 初始实施时的历史基线和 gate；完成结构化输出轻量修正后的当前基线与阶段 1.1
验收口径以第 12 节为准，不能直接混用两组数字。

在与当前 30 题基线可比的协议下，阶段 1 的初始验收目标为：

| 指标 | 当前基线 | 阶段 gate |
| --- | ---: | ---: |
| generation success | 30/30 | 30/30 |
| target solver started | 28/30 | 至少 29/30 |
| solver normal completion | 20/30 | 至少 23/30 |
| public validation pass | 18/30 | 至少 20/30 |
| strict qualification pass | 10/15 | 不低于 10/15，并报告变化原因 |
| provider/environment terminal blocker | 0 | 0 |

同时必须满足：

- 不新增逐题内容；
- 不放宽 evaluator 阈值制造通过；
- prompt 上下文 P90 不超过基线的 1.25 倍；
- 已知合法 frozen replay 零新增 blocking regression；
- 新知识在至少一个非开发 holdout 上产生可解释改善。

模型具有随机性，因此单次 30 题结果只作为总体 gate；具体能力提升必须同时由重复目标族测试
和确定性契约测试支持。

## 11. 产物

- 经治理的 Knowledge 条目和 manifest；
- 六类 family Skill 及 scenario；
- coverage report 生成器和报告；
- failure-family 与 improvement target 报告；
- 检索与 scoped repair 测试；
- 阶段 1 真实 OpenFOAM 验收报告。

完成本阶段后，FoamPilot 仍然以结构化 `TaskSpec` 和简单/已有网格路线为主；复杂几何支持在
阶段 2 实施。

## 12. 阶段 1.1：基于 30 题证据的轻量修正

### 12.1 设计动机

2026-08-05 的同后端 30 题复测在消除大部分结构化输出失败后得到：

- case generation：29/30；
- target solver started：27/30；
- solver normal completion：21/30；
- public validation pass：20/30；
- qualification：16 `PASS`、13 `FAIL_AGENT`、1 `DEFERRED_BACKEND`。

剩余问题不能统一归结为“知识条目不足”。冻结产物显示至少存在四种不同情况：

1. **知识激活过宽**：专用 `volumeFractionSource` 条目只因 executable 匹配便进入不需要该
   模型的上下文；
2. **Foundation v10 原生契约缺口**：例如 phase dictionary 必需关键词、矩阵对称性与线性
   求解器组合；
3. **有契约但行为顺序不够明确**：例如先保证热力学可行状态和保守启动，再追求高阶格式或
   大时间步；
4. **不应由 Knowledge/Skill 修复的失败**：backend timeout、单题 evaluator 偏差、复杂网格
   几何错误和需要确定性程序修复的流程问题。

因此阶段 1.1 不增加知识库规模目标，而是提高检索精度和现有 family 指导的有效密度。

### 12.2 实施范围

本轮只处理可由公开 Foundation v10 事实支撑、且能够迁移到同 solver family 的内容：

| 领域 | Knowledge 修正 | Skill 行为修正 |
| --- | --- | --- |
| 专用 physics model | 为 `volumeFractionSource` 等 opt-in 条目补充明确 `activation_terms`，无任务证据时保持槽位为空 | 只有任务明确声明模型时才创建相应字段、字典和初始化命令 |
| `interFoam` | 明确 alpha solver dictionary、`p_rgh`、相物性和初始化的 Foundation v10 契约 | 按 mesh → `checkMesh` → `setFields` → solver 顺序，并先验证初始相分数 |
| `twoLiquidMixingFoam` | 补齐 phase dictionary 的必需输运/扩散关键词及字段关系 | solver 启动前按模型读取顺序核对 phase properties 和 alpha 字段 |
| `rhoCentralFoam` / `rhoPimpleFoam` | 明确对称/非对称矩阵的 solver/preconditioner 兼容性、thermo 字段一致性和正状态启动 | 先采用可运行的保守启动组合，并根据 Courant、温度和压力证据逐步调整 |
| `buoyantFoam` | 强化 Boussinesq/ideal-gas 适用边界、`p/p_rgh`、能量变量和参考状态关系 | 在修改松弛或离散格式前先验证 thermo package、参考状态和初始温度可反演 |
| Maxwell/PIMPLE | 保留专用 Maxwell 契约，补充 Courant、应力对流和 outer coupling 的稳定启动顺序 | repair 每次只改变一个有证据的稳定性因素，并检查应力 residual 是否恶化 |

阶段 1.1 不新增 per-case Knowledge、Skill、TaskSpec 字段、状态机、renderer 或 blocking
inspector。复杂 SRF 拓扑只归档为 mesh-family 后续工作，除非能够从公开源码提炼出与目标几何
无关的通用规则。

### 12.3 内容形式

Knowledge 继续保存事实和适用条件；Skill 只保存判断与动作顺序。新增文字优先使用正向契约：

```text
任务事实
→ 激活一个适用条目
→ 声明必需文件/字段关系
→ 采用保守可运行的启动配置
→ 原生日志验证
→ 只对观测到的失败做最小 repair
```

不得把某道题的最终字典、几何尺寸、patch 名、golden value、evaluator tolerance 或官方路径写入
Knowledge/Skill。官方 example 如被 teacher 侧读取，只能在 attempt 冻结后用于核对通用
Foundation v10 语义。

### 12.4 测试与验收

修改顺序遵循：

1. 用现有 Skill 时运行隔离的基线场景，记录模型是否遗漏或违反目标契约；
2. 先增加会失败的 retrieval/activation 或内容契约测试；
3. 最小修改一条 Knowledge 或一个 family Skill；
4. 运行相同场景验证行为变化；
5. 运行未参与提炼的同族 holdout 和 frozen artifact replay；
6. 最后再决定是否需要完整 30 题复测。

第一优先级是稳定、较快地进入目标求解器。阶段 1.1 的最小验收条件为：

- 专用知识在缺少显式任务证据时不再被选中；
- 新增或修改的 family 契约均有 Foundation v10 来源与独立测试；
- 目标失败样本的 target solver entry 或正常完成得到可解释改善；
- 同族 holdout 的 target solver entry 不回退；
- 不放宽 public checks 或 qualification evaluator；
- prompt 上下文大小不因本轮修正显著增加；
- backend/environment failure 继续与 case failure 分开统计。

严格物理 qualification 是第二层指标。它必须完整报告，但不以针对单题调参的方式作为本轮
Knowledge/Skill promotion 条件。

## 13. 阶段 1.2：Knowledge 遵从与 family Skill 路由

阶段 1.1 后的四个复杂 solver gate 已经检索到正确 solver guide，但没有加载 family Skill，
模型仍逐项遗漏 guide 中明确列出的 reader contract。阶段 1.2 因此不重复扩充相同 Knowledge，
而是：

- 为 `compressibleInterFoam`、`driftFluxFoam`、`multiphaseEulerFoam` 和 `reactingFoam`
  登记恰好一个适用 family Skill；
- 增加一个跨 solver 的 coupled multiphase Skill；
- 要求通用 author Skill 将 selected solver guide 的成组必需项转为生成前原子清单；
- 继续用既有 30+20 题验证 solver entry、normal completion 和 public validation。

具体步骤与 gate 见[阶段 1.2 实施计划](../plans/2026-08-06-knowledge-skills-stage-1-2.md)。本阶段
不修改 Runner、Gateway、ExecutionPlan、repair schema 或 evaluator，也不进入 Agent Harness
v2 P0/P1 的实现。
