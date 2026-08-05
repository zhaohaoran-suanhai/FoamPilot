# 阶段 3：自然语言 TaskBuilder 规格

状态：核心实现已完成，当前证据和未完成真实 gate 见
[第三阶段实施记录](../reports/2026-08-04-stage-3-taskbuilder.md)。本文保留完整规格，未通过的
验收项不得解释为现有能力。

## 1. 背景与目标

当前 FoamPilot 的规范输入是严格 YAML `TaskSpec`。它适合 qualification、自动化和可审计运行，
但普通用户必须理解 schema、公开检查和资源字段，尚不能直接以自然语言和几何附件启动任务。

本阶段增加一条受控入口：

```text
自然语言 + 附件
  -> TaskDraft
  -> 缺失信息检查与必要澄清
  -> 用户确认
  -> 规范 TaskSpec
  -> 现有 NativeAgent.solve()
```

目标是降低使用门槛，同时保证自然语言入口不会绕过 TaskSpec、虚构物理条件或建立第二套求解
状态机。

## 2. 非目标

- 不让聊天记录成为不可审计的求解输入。
- 不允许模型直接从对话调用 OpenFOAM 或 Runner。
- 不自动猜测缺失的物性、几何单位、边界数值或工程验收目标。
- 不把自然语言模型建议的 solver 当作最终 capability route。
- 不让 qualification 经过自然语言重述。
- 不在本阶段实现多用户会话、云端账号或完整 Desktop IDE。
- 不用自由文本替代阶段 2 已定义的几何和网格字段。

## 3. 组件边界

```text
NaturalLanguageRequest
  -> TaskExtractor（模型，结构化提取）
  -> TaskDraft
  -> DraftValidator（确定性）
  -> ClarificationSet
  -> TaskCompiler（确定性）
  -> TaskSpec
  -> canonical validate / route / solve
```

### 3.1 TaskExtractor

负责从用户文本和附件 metadata 中提取：

- 问题类型和物理现象；
- 几何与单位；
- 流体、材料和物性；
- 工况和边界角色；
- 稳态/瞬态意图；
- 湍流、可压缩、多相、能量等显式要求；
- 用户关心的输出和验收目标；
- 资源或时间限制；
- 明确提到的 OpenFOAM 版本、solver 或网格方式。

Extractor 只返回符合 schema 的 `TaskDraft`。它不写 case、不执行命令、不生成隐藏 evaluator
规则，也不能将推断标记成用户事实。

### 3.2 DraftValidator

确定性检查：

- schema、单位和数值范围；
- public asset 是否存在并具有 hash；
- 几何/mesh 字段是否符合阶段 2 契约；
- 必需物理输入是否缺失；
- 边界角色是否冲突；
- 输出目标是否可以由已声明的公开检查表达；
- task 内容是否泄漏 protected path；
- 本机 Foundation v10 能力是否明显不支持。

### 3.3 TaskCompiler

只在关键问题已经解决后，把 `TaskDraft` 编译为规范 TaskSpec。Compiler 负责填充确定性默认值、
生成基础 public checks、计算 asset hash 引用并输出编译报告。

最终 solver family 仍由现有 capability router 根据 TaskSpec 和环境决定。Draft 中的 solver 仅可
作为用户显式要求或建议 evidence，不得跳过路由。

## 4. `TaskDraft`

建议结构：

```yaml
schema_version: 1
draft_id: draft-...
request_text: "..."
facts:
  physics: {}
  geometry: {}
  materials: []
  boundaries: []
  operating_conditions: {}
  outputs: []
  resources: {}
assumptions:
  - id: default-openfoam-version
    value: foundation-10
    source: system_default
    impact: low
unresolved_questions: []
evidence:
  - field: boundaries.inlet.velocity
    source: user_text
    excerpt: "入口速度为 2 m/s"
status: incomplete | ready_for_confirmation | confirmed
```

`TaskDraft` 是可编辑工作状态，不属于求解 attempt。用户修改 draft 不产生 run lineage；一旦编译
为 TaskSpec，TaskSpec 的内容和 hash 才成为一次求解的正式输入。

## 5. 事实、推断和默认值

每个高影响值必须有来源：

| source | 含义 | 是否可直接编译 |
| --- | --- | --- |
| `user_text` | 用户明确提供 | 是 |
| `public_asset` | 从用户允许的附件确定性读取 | 是 |
| `user_confirmation` | 用户确认模型提取或补充 | 是 |
| `system_default` | 仅限低风险运行默认值 | 是，必须显示 |
| `model_inference` | 模型推断 | 高影响值不允许直接编译 |

允许使用系统默认值的范围：

- Foundation OpenFOAM v10；
- 最大 attempt、墙钟、内存和 MPI 等计算预算；
- 日志保存、artifact 和基础公开完整性检查；
- 在不改变物理问题的前提下采用保守启动策略。

必须确认或由公开资产确定的范围：

- 长度单位和关键几何尺寸；
- 材料/流体及关键物性；
- 入口、出口、壁面、热源和载荷的物理数值；
- 初始条件中影响问题定义的值；
- 瞬态终止时间或稳态终止要求；
- 用户要求比较的工程指标及容差。

## 6. 澄清策略

DraftValidator 将问题分成三类：

### 6.1 Blocking

不解决就会改变问题定义或无法安全求解，例如单位、物性、入口条件、几何文件缺失。必须向用户
提问，不能进入 generation。

### 6.2 Confirmable

模型已提取出一个高置信候选，但具有工程影响，例如二维/三维解释、稳态/瞬态意图。界面或 CLI
应显示候选、证据和影响，让用户一次确认。

### 6.3 Advisory

不阻止编译，例如未指定 MPI ranks 或结果写出频率。采用系统默认值并写入 assumptions。

系统应把同一轮可以共同回答的问题合并成简洁表单，避免一次只追问一个字段造成长对话。但每个
问题必须能够独立映射回一个结构字段。

## 7. Public checks 编译

基础公开检查来自确定性 registry，而不是模型自由编写：

| 条件 | 基础检查 |
| --- | --- |
| 所有任务 | case files、mesh command、target solver started、normal completion、required outputs、finite fields |
| 瞬态任务 | 到达目标时间、时间步/Courant 证据 |
| 稳态任务 | 残差趋势、continuity 或 solver-family 收敛证据 |
| VOF | 相分数范围和体积守恒 |
| 可压缩 | Courant、thermo finite、压力/温度范围 |
| 浮力/传热 | 温度范围、continuity、声明时的热平衡 |
| 固体 | 位移/应力有限性和声明载荷输出 |

用户要求的压降、升阻力、Nusselt 数、温度均匀性等指标只有在任务中明确提出时才能加入。模型不
得发明目标值；没有工程 tolerance 时可以报告观测值，但不能声称通过工程验收。

qualification 的私有 checks 不属于 TaskBuilder，也不能由自然语言入口显示或生成。

## 8. CLI 与 Python 接口

建议 CLI：

```bash
foampilot task draft \
  --request-file request.md \
  --asset geometry/model.stl \
  --output task-draft.yaml \
  --backend auto --json

foampilot task validate-draft task-draft.yaml --json

foampilot task compile task-draft.yaml \
  --output task.yaml --json

foampilot solve task.yaml --run-root RUNS --backend auto --json
```

Python 边界：

```python
draft = task_builder.extract(request, assets, model_gateway)
review = task_builder.validate(draft, environment)
task = task_builder.compile(review.confirmed_draft)
outcome = native_agent.solve(task)
```

不提供让 `TaskBuilder` 直接持有 Runner 或调用 OpenFOAM 的接口。

为演示方便，后续可以增加组合命令，但内部仍依次持久化 draft、TaskSpec 和 canonical run：

```bash
foampilot ask --request-file request.md --run-root RUNS
```

组合命令不得隐藏 assumptions 或跳过 blocking clarification。

## 9. 错误语义

| code | 中文含义 | 恢复方式 |
| --- | --- | --- |
| `TASK_EXTRACTION_FAILED` | 模型未返回合法草稿 | Gateway 有界重试或更换 backend |
| `TASK_REQUEST_INCOMPLETE` | 缺少问题定义所需信息 | 用户补充 blocking 字段 |
| `TASK_UNIT_AMBIGUOUS` | 单位缺失或相互冲突 | 用户确认单位 |
| `TASK_PHYSICS_AMBIGUOUS` | 物理模型存在多种重要解释 | 用户确认意图 |
| `TASK_ASSET_UNRESOLVED` | 附件、hash、surface 或 region 不一致 | 修正附件或映射 |
| `TASK_COMPILATION_FAILED` | 草稿无法确定性编译成 TaskSpec | 修正指定字段 |
| `TASK_CAPABILITY_UNAVAILABLE` | 本机缺少明确需要的能力 | 环境处理或改变任务要求 |

这些错误发生在 run 之前，不得记录为 OpenFOAM 或 solver failure。

## 10. 安全与隐私

- request、draft 和 TaskSpec 可能包含工程信息，模型 trace 继续不保存 prompt/response 正文；
- 附件先复制到受控 staging，校验大小、路径、类型和 SHA256；
- backend 只能看到显式允许的 request 和经过摘要的 asset metadata；
- 不把本机任意目录、环境变量或其他 run 文件加入模型上下文；
- protected path 与 qualification leakage 规则继续适用；
- 任何模型输出都必须经过 Pydantic/schema 校验后才能保存为 draft。

## 11. 测试策略

### 11.1 确定性测试

- TaskDraft schema 与状态转换；
- facts/evidence/assumptions 来源完整性；
- blocking/confirmable/advisory 分类；
- 单位和边界冲突；
- TaskCompiler 对系统默认值和 public-check registry 的处理；
- TaskSpec round-trip 和 hash；
- protected path、asset hash 与 leakage；
- 中文错误 message 和 recovery。

### 11.2 fake model 测试

- 合法结构化提取；
- 缺字段；
- 模型虚构物性；
- schema invalid；
- provider overload/network interruption；
- response 含受保护路径；
- 同一逻辑请求重试后成功。

### 11.3 语义 fixture

至少覆盖中文和英文的：

- 完整简单内流；
- 缺单位的表面几何；
- 缺物性的传热；
- 稳态/瞬态歧义；
- 多相初始条件；
- 固体载荷；
- 多区域 CHT；
- 明确指定和未指定 solver；
- 不可由当前 OpenFOAM v10 支持的请求。

### 11.4 真实模型与 OpenFOAM gate

至少选择五个完整自然语言请求，覆盖简单 `blockMesh`、surface、Gmsh、瞬态和多物理。每个请求
必须完成：

```text
request -> draft -> TaskSpec -> canonical solve -> report
```

## 12. 阶段验收

- 完整请求无需人工编辑 YAML 即可生成合法 TaskSpec；
- 缺失高影响物理信息时，系统不虚构数值且不会进入 case generation；
- 所有 assumptions 在编译前对用户可见；
- 同一确认后的 draft 确定性地产生相同 TaskSpec hash；
- TaskBuilder 失败不会污染 solver/mesh 成功率统计；
- qualification 结果与是否安装 TaskBuilder 无关；
- 五个真实请求均通过结构化入口进入现有 `NativeAgent.solve()`，不出现第二条 Runner 路径；
- 至少三个请求达到 target solver started，未达到者具有正确的 case/mesh/solver 失败归因。

## 13. 产物

- `TaskDraft`、review 和 compilation report 数据模型；
- TaskExtractor、DraftValidator 和 TaskCompiler；
- public-check registry；
- `foampilot task` CLI；
- 中文/英文语义 fixtures；
- 自然语言到真实 OpenFOAM 的 gate 报告；
- 更新后的快速开始和 Agent 集成文档。

完成阶段 3 后，CLI 和上游 Agent 已可通过自然语言创建任务。阶段 4 只负责把这些接口组织成
稳定、直观的本地桌面用户体验。
