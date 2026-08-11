# FoamPilot 下一阶段顺序演进规格

状态：四项设计方向已确认。阶段 1—3 已按顺序实现并分别保留证据报告；阶段 4 已完成
Desktop A 和 Desktop B 交互式求解 v1，Desktop C/D 尚未实施。围绕日常本机可靠性的三份
后续规格已经确认，将按核心活性、任务监督、恢复语义的顺序串行实施。各子规格中的
“目标实现”描述应结合对应阶段报告判断，
不能把可靠取消、崩溃重连、恢复语义、三维可视化、人工 revision 或未通过的真实 gate
当作现有能力。

## 1. 目标

FoamPilot 下一阶段围绕四个方向顺序演进：

1. 优化 Knowledge 与 Skills，提高原生 OpenFOAM case 的编写正确率；
2. 扩展前处理，使 Agent 能从参数化几何、表面几何或外部网格输入开始工作；
3. 增加自然语言到 `TaskSpec` 的受控转换；
4. 增加面向 Linux 仿真工作站的 `FoamPilot Desktop IDE`。

前三项增强 Agent 的核心能力，第四项把已经验证的核心能力组织成可直接使用的产品入口。

## 2. 核心决策

四个方向按顺序实施，不并行建设四套系统。现有规范主链保持唯一：

```text
TaskSpec
  -> environment / routing / context
  -> Agent 编写 ExecutionPlan v3
  -> inspect / mesh / initialize / solve / postprocess
  -> public validation / bounded repair
  -> workflow summary / immutable artifacts
```

后续模块只能在这条主链的入口、上下文或观察界面上增量扩展：

```text
自然语言或结构化输入
  -> TaskBuilder（第三阶段，可选）
  -> TaskSpec
  -> GeometryProbe（第二阶段，仅几何任务）
  -> 现有 FoamPilot 规范主链
  -> Desktop IDE（第四阶段，调用、观察和受控编辑主链）
```

## 3. 共享数据接口的处理方式

不单独建设“公共数据契约”项目。这里的契约只是模块之间经过校验的数据结构，不是数据库、
MCP、微服务或第二套 CaseSpec。

每个阶段只增加当时确实需要的最小数据：

| 阶段 | 复用的数据 | 该阶段允许新增的数据 |
| --- | --- | --- |
| Knowledge/Skills | `TaskSpec`、`CapabilityProfile`、`AgentContext`、`ExecutionPlan` | 原则上不新增任务结构 |
| 前处理 | 上述全部 | `GeometryInput`、`MeshIntent`、`GeometryFacts`、`MeshQualityReport` |
| TaskBuilder | 前处理完成后的规范 `TaskSpec` | 可编辑的 `TaskDraft` 与编译报告 |
| Desktop IDE | 全部现有结构与 workflow artifacts | 最小 job receipt 和 Qt view model，不新增 CFD 真相源 |

若第二阶段需要修改 `TaskSpec`，应把新版本作为唯一规范 authoring 格式，批量迁移仓库内任务；
不得长期维护两条 solve 路径。历史 run 只保留只读报告能力。

## 4. 人、Agent 与确定性程序的边界

| 角色 | 主要职责 |
| --- | --- |
| 用户/工程师 | 提供几何、单位、物性、工况、边界物理含义、目标输出和工程验收标准；确认高影响假设 |
| Agent | 选择适用求解策略，编写完整原生 case 和 typed commands，依据公开证据进行有限修复 |
| 确定性程序 | 校验数据、探测环境与几何、约束命令、执行 OpenFOAM、解析日志、计算公开指标并保存证据 |
| qualification evaluator | 在与 Agent 隔离的边界内使用 reference/golden 进行外部评测 |

Agent 不得虚构缺失的物性、边界数值、单位或工程目标。确定性程序也不替 Agent 选择本应由
CFD 推理决定的离散格式和模型组合。

## 5. 顺序和阶段 gate

```text
阶段 1 Knowledge/Skills
  -> 通过知识治理、holdout 和 30 题回归 gate

阶段 2 前处理
  -> 通过 blockMesh、surface、Gmsh 三条真实 native gate

阶段 3 NaturalLanguageTaskBuilder
  -> 通过语义保持、缺失信息和无虚构 gate

阶段 4 FoamPilot Desktop IDE
  -> 通过同一 run 的 CLI/Qt 一致性和本地端到端演示 gate
```

只有当前阶段达到验收标准，才进入下一阶段。某一阶段的实现不得以提前搭建下一阶段框架为
理由扩大范围。

## 6. 跨阶段不变量

- 当前验证目标保持 Foundation OpenFOAM v10，扩展版本支持属于后续独立工作。
- `NativeAgent.solve()` 及其底层状态机仍是规范求解路径。
- qualification 不经过自然语言转换，继续使用冻结的结构化任务和隔离 evaluator。
- 目标 tutorial、私有 validator 和 golden 不进入 authoring 或 repair 上下文。
- OpenFOAM 返回零不等于物理通过；公开验证与 qualification 继续分层报告。
- 所有命令仍为 typed command；不允许 Agent 生成 shell、`Allrun` 或 MPI launcher。
- 每个 attempt 保持不可变；人工编辑、rerun 和 resume 具有明确 lineage。
- bubblewrap 不可用时继续采用有记录的 audited-host fallback，不等待交互授权。
- 不建立逐题 renderer、逐题 Skill、逐题知识条目或第二套运行状态机。
- 不自动 promotion 学习结果；所有正式 Knowledge/Skill 变更都需要离线证据和人工批准。

## 7. 文档索引

- [Agent Harness 演进 v2：状态、定向修复、受控经验和消融](agent-harness-evolution-v2-design.md)
- [Performance v1：冷路径、计划复用、网格缓存与 repair 阶段复用](performance-v1-design.md)
- [阶段 1：Knowledge 与 Skills 优化](knowledge-skills-design.md)
- [阶段 2：前处理能力](preprocessing-design.md)
- [阶段 3：自然语言 TaskBuilder](natural-language-task-builder-design.md)
- [阶段 4：FoamPilot Desktop IDE](desktop-ide-design.md)
- [Desktop B 交互式求解 v1 设计](desktop-ide-interactive-v1-design.md)
- [核心执行可观测性与活性规格](execution-observability-liveness-design.md)
- [本机任务监督与 Desktop 可靠性规格](local-job-supervision-reliability-design.md)
- [恢复、Resume 与 Rerun 语义规格](recovery-resume-rerun-design.md)
- [阶段 1 实施记录](../reports/2026-08-04-stage-1-knowledge-skills.md)
- [阶段 2 实施记录](../reports/2026-08-04-stage-2-preprocessing.md)
- [阶段 3 实施记录](../reports/2026-08-04-stage-3-taskbuilder.md)
- [Agent Harness v2 P0/P1 实施与验证](../reports/2026-08-06-agent-harness-p0-p1.md)
- [Desktop A Run Inspector 实施与验证](../reports/2026-08-06-desktop-a-run-inspector.md)
- [Desktop B 交互式求解 v1 实施与验证](../reports/2026-08-11-desktop-b-live-solve.md)

## 8. 总体验收口径

各阶段报告至少区分：

- TaskSpec 是否有效；
- generation 是否成功；
- mesh generation 与 `checkMesh` 是否通过；
- target solver 是否启动；
- solver 是否正常结束；
- public validation 是否通过；
- qualification 是否通过；
- provider/environment 是否构成 terminal blocker；
- 从任务开始到第一个 OpenFOAM command 的时间；
- 模型请求数、repair 次数和总墙钟时间。

任何阶段都不能只以“能生成文件”“命令返回零”或“界面可以打开”作为完成证据。
