# FoamPilot 历史文档索引

状态：**历史与审计入口，不是现行规格**

本索引不物理移动旧文件，避免破坏已有链接。被列入这里表示该文档用于追溯决策、实现过程或
当时证据；它可以保留独立价值，但不能覆盖：

1. [`../current-state.md`](../current-state.md) 的当前事实；
2. [`../../AGENTS.md`](../../AGENTS.md) 的仓库规则；
3. [`../architecture.md`](../architecture.md) 的冻结职责与数据契约；
4. 当前源码、schema 和新鲜测试证据。

旧文档中的版本、测试数量、路径、未实现事项和产品口径可能已经失效。引用旧报告时必须同时
写明日期和证据层，不能把历史 solver completion、public validation 或阶段性定向复测改写成
当前 qualification。

## 1. 当前仍需直接阅读的设计和计划

以下文件不属于历史执行依据，而是当前代码/测试减重任务的有效输入：

- [代码库减重与新会话交接设计](../superpowers/specs/2026-08-13-codebase-consolidation-design.md)：
  已批准的范围、目标结构和验收门禁；
- [代码与测试减重实施计划](../superpowers/plans/2026-08-13-code-and-test-consolidation.md)：
  新对话的逐步执行顺序；
- [公开与私有资产分发边界](../design/distribution-asset-boundary.md)：已经决定暂缓实施，但公开
  发布前仍需重新启用的边界。

## 2. 已完成的 contract-first 架构记录

这些文档解释当前架构为何形成；实现已经进入源码，执行减重时以 `architecture.md` 为准：

- [总体设计](../superpowers/specs/2026-08-12-contract-first-agent-architecture-design.md)
- [总体路线图](../superpowers/plans/2026-08-12-contract-first-architecture-roadmap.md)
- [Phase 1：资产与网格事实](../superpowers/plans/2026-08-12-contract-first-phase-1-assets-mesh-facts.md)
- [Phase 2：意图、设计与风险](../superpowers/plans/2026-08-12-contract-first-phase-2-intent-design-risk.md)
- [Phase 3：author、plan 与 repair](../superpowers/plans/2026-08-12-contract-first-phase-3-author-plan-repair.md)
- [Phase 4：coordinator 与 evidence](../superpowers/plans/2026-08-12-contract-first-phase-4-coordinator-evidence.md)
- [Phase 5：observations、postprocess 与 acceptance](../superpowers/plans/2026-08-12-contract-first-phase-5-observations.md)
- [Task ingress reconciliation 设计](../superpowers/specs/2026-08-13-task-ingress-reconciliation-design.md)
- [Task ingress reconciliation 计划](../superpowers/plans/2026-08-13-task-ingress-reconciliation.md)

最后两项已经由生产代码提交 `55ab25f` 实现；它们不是尚待执行的功能计划。

## 3. 早期架构分析和替代路线

以下文档可以解释历史问题，但其中的模块边界、未来计划或兼容路径不再是当前权威：

- [2026-07-30 Agent 架构分析](2026-07-30-agent-architecture-analysis.md)
- [架构优化设计](../architecture-optimization-design.md)
- [Runtime workflow 与 pre-solve health 分析](../runtime-workflow-and-pre-solve-health-analysis.md)
- [Clean-source model backend 设计](../clean-source-model-backend-design.md)
- [早期 source refactor 实施计划](../foampilot-source-refactor-implementation-plan.md)
- [Phase B routing/semantic 实施计划](../phase-b-routing-semantic-implementation-plan.md)
- [Verified plan reuse 设计](../verified-plan-reuse-design.md)
- [Solver family self-checks](../solver-family-self-checks.md)
- [早期 controlled learning loop 设计](../superpowers/specs/2026-07-29-controlled-learning-loop-design.md)
- [早期 flexible pre-solve checks 设计](../superpowers/specs/2026-07-29-flexible-pre-solve-checks-design.md)

这些材料不得恢复第二套状态机、旧 TaskSpec authoring、`public-validation.json` 新写入路径、
模型生成命令或重复日志解析器。

## 4. 顺序演进设计记录

[`../design/README.md`](../design/README.md) 是 2026-08-04 至 2026-08-12 各阶段的原始索引。
下列文件记录已经实现的阶段设计，或保留将来工作边界：

- `preprocessing-design.md`
- `natural-language-task-builder-design.md`
- `knowledge-skills-design.md`
- `performance-v1-design.md`
- `agent-harness-evolution-v2-design.md`
- `desktop-ide-design.md`
- `desktop-ide-interactive-v1-design.md`
- `runtime-portability-execution-security-design.md`
- `execution-observability-liveness-design.md`
- `local-job-supervision-reliability-design.md`
- `recovery-resume-rerun-design.md`
- `distribution-asset-boundary.md`

路径均位于 [`docs/design/`](../design/)。它们用于功能细节追溯；当前跨模块依赖和唯一副作用
owner 仍以 `architecture.md` 为准。

## 5. 已执行实施计划

[`docs/plans/`](../plans/) 下的计划已经作为对应阶段的执行记录保留：

- `2026-08-04-stage-1-knowledge-skills.md`
- `2026-08-04-stage-2-preprocessing.md`
- `2026-08-04-stage-3-natural-language-task-builder.md`
- `2026-08-05-knowledge-skills-stage-1-1.md`
- `2026-08-05-performance-v1-implementation.md`
- `2026-08-06-agent-harness-p0-p1.md`
- `2026-08-06-desktop-a-run-inspector.md`
- `2026-08-06-knowledge-skills-stage-1-2.md`
- `2026-08-11-desktop-b-live-solve.md`
- `2026-08-11-execution-observability-liveness.md`
- `2026-08-11-runtime-portability-execution-security.md`
- `2026-08-12-local-job-supervision-reliability.md`
- `2026-08-12-recovery-resume-rerun.md`

旧 `docs/superpowers/plans/` 还包含 2026-07-29 的 controlled learning、flexible checks 和
OpenFOAM guard 计划，以及 2026-08-04 的 clean-source backend 计划。它们同样是完成过程记录，
不是新会话待办。

## 6. 阶段报告和证据快照

[`docs/reports/`](../reports/) 中的报告按时间分为：

| 日期 | 报告 | 正确用途 |
|---|---|---|
| 2026-07-29 | `standalone-real-gate` | 当时 wheel 的两个独立真实算例 gate |
| 2026-07-30 | `controlled-learning-15`、`extended-10-learning`、`delivery-readiness` | 当时题集、定向学习和交付边界 |
| 2026-07-31 | `stage-a-acceptance`、`stage-b-acceptance` | 早期分阶段验收 |
| 2026-08-04 | `official-corpus-30-baseline`、stage 1/2/3 报告 | 题库基线与前三阶段实现证据 |
| 2026-08-05 | Knowledge/Skills 1.1、Performance v1 | 对应阶段实现证据 |
| 2026-08-06 | Agent Harness、Desktop A、Knowledge/Skills 1.2、corpus harness v2 | 对应阶段实现与测试快照 |
| 2026-08-11 | Desktop B、`v0.2.0` release | 交互式求解和正式 tag 的发布记录 |
| 2026-08-12 | execution observability、local job supervision、recovery/resume/rerun | 三个可靠性阶段的完成记录 |

报告中的 “PASS” 必须按该报告自己的输入、日期和验收层解释。尤其：

- `v0.2.0` 报告不能证明当前未发布 `main` 已完成跨机验证；
- 2026-07-29/30 的 public validation 不能替代当前 `ResultReport` 或 qualification；
- Desktop 报告中的 offscreen 测试不能外推真实窗口管理器、Qt plugin 或人工交互门禁；
- 后续代码提交可能使旧的测试数量和源码哈希失效。

## 7. 发布记录

- [v0.2.0 发布与验证记录](../reports/2026-08-11-v0.2.0-release.md) 对应现有 tag
  `v0.2.0`；
- [`../../CHANGELOG.md`](../../CHANGELOG.md) 的 `[Unreleased]` 描述 tag 之后当前 `main` 的
  contract-first、观测验收和可靠性变化；
- 在另一台干净 Ubuntu + Foundation v10 上完成安装、preflight、自然语言求解和 Desktop
  实机门禁之前，不得把 `v0.2.0` 或当前 `main` 表述成跨机产品资格；
- 私有 evaluator/Knowledge/Skills 尚未物理分包，暂不进行公开发布。

## 8. 历史文档处理规则

- 减重任务不删除历史报告，也不批量移动文件；
- 只有逐字重复、没有独立证据、没有有效引用的材料，才能另开机械化文档任务删除；
- 修改历史报告只允许修复坏链接或加显著的 superseded 提示，不回写新结果；
- 新的当前事实只进入 `current-state.md`、`CHANGELOG.md`、现行功能文档或新的日期报告；
- 新会话如果只需要执行代码/测试减重，不应通读全部历史报告。
