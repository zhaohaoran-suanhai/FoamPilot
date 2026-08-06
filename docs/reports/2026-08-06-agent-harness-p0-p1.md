# FoamPilot Agent Harness v2：P0/P1 实施与验证报告

日期：2026-08-06
状态：P0/P1 已完成本轮实现与 gate；P2/P3 未实施
实施计划：[2026-08-06-agent-harness-p0-p1.md](../plans/2026-08-06-agent-harness-p0-p1.md)
设计依据：[agent-harness-evolution-v2-design.md](../design/agent-harness-evolution-v2-design.md)

## 1. 结论

FoamPilot 的规范 `NativeAgent.solve()/resume()` 主链已经从“完整上下文 + `RepairDecision`”升级为：

```text
TaskSpec / CapabilityProfile / AgentContext / WorkflowStore / budget
  -> AgentStatusSnapshot
  -> author
  -> native inspection / Runner / public validation
  -> deterministic FailureClassifier
  -> deterministic RepairScope
  -> updated AgentStatusSnapshot
  -> RepairPatch
  -> patch validation and application
  -> affected-stage reuse or safe full rerun
```

本轮没有增加 reviewer、模型调用、图运行时、MCP 或第二个 Agent。Runner、Evaluator、Gateway、
`ExecutionPlan v3` 和 attempt 不可变边界保持不变。

## 2. P0：确定性状态快照

新增 `AgentStatusSnapshot`，在 author 和 repair 请求前从已有事实源构造：

- 当前决策阶段与最后完成的 Workflow stage；
- 当前/最大 attempt；
- solver family、solver 和 plan region；
- logical request、transport attempt、模型时间和 OpenFOAM 执行时间余量；
- Knowledge ID、Skill name 与不可逆内容摘要；
- 最新结构化失败和本次允许操作；
- public asset、OpenFOAM target、protected path 数量与摘要。

protected path 原文不进入快照。事实源矛盾时在模型调用前返回：

```text
AGENT_STATUS_INCONSISTENT
无法从当前运行事实构造一致的 Agent 状态。
```

新增产物：

```text
agent-status-author-01.json
agent-status-repair-NN.json
```

`ModelAttemptTrace` 只保存状态文件相对路径和 SHA256，不保存状态正文、prompt 或响应正文。真实 gate
已确认 author/repair trace 都包含对应引用。

## 3. P1：确定性失败分类与 RepairScope

新增 `FailureClassifier`，不调用模型，当前覆盖：

- static inspection issue；
- mesh、initialization、solver、postprocess 和 public validation layer；
- missing dictionary keyword；
- missing case file / registry object；
- dimension mismatch；
- unknown function object；
- 缺少 mesh typed command；
- 含无效 option 的可移除 optional typed command；
- 低证据时的 `unclassified_native_failure`。

分类不会覆盖原始 CFD failure；`primary_failure` 与 provider/workflow blocker 仍分别保存。

`RepairScope` 只选择分类直接相关的文件、命令和当前 Knowledge ID。文件表示支持：

```text
full
matching_block
head_tail_excerpt
structure_only
metadata_only
```

大文件会降级表示，不再只因超过固定大小而终止整次修复。public asset 内容和 protected path 原文不进入
scope。新增产物：

```text
failure-classification-attempt-NN.json
repair-scope-attempt-NN.json
```

## 4. P1：RepairPatch 与命令操作

旧 `RepairDecision` 已从源码和规范运行路径删除。新的 `RepairPatch` 支持：

- 文件：`add`、`replace`；
- 命令：`insert_before`、`insert_after`、`replace`、`remove`。

补丁应用器验证 scope、public asset、protected path、anchor、step ID、stage 变化、命令预算、executable
与完整计划策略。应用后重新执行 normalizer 和 `validate_execution_plan()`，下一 attempt 仍执行完整 native
inspection。

单个精确 no-op 操作会被丢弃；如果同一补丁还有真实文件或命令变化，不会再因冗余 unchanged command
使整个正确修复作废。全补丁 no-op 仍以 `REPAIR_PATCH_INVALID` 有限停止。

既有非标准但已通过计划策略的命令顺序不会因纯文件修复被机械拒绝；补丁只禁止新增更多 stage
逆序。repair reuse 改为只读取实际 `RepairChangeSet`，不相信模型声明的重跑起点。

新增产物：

```text
repair-patch-attempt-NN.json
repair-patch-normalization-attempt-NN.json
```

## 5. 真实故障回归

本轮将 Knowledge/Skills 1.2 四题暴露的架构问题转成确定性回归：

1. 被 `#include` 引用且无扩展名的 OpenFOAM fragment 不再被错误要求 `FoamFile` header；
2. 未引用的普通 headerless native file 仍会被拦截；
3. OpenFOAM 日志中的 `system/fvSchemes/divSchemes` 会归一到已声明文件 `system/fvSchemes`；
4. `thermo:rho` 等 grouped field name 在 classification/scope 中保持完整；
5. “真实文件变化 + 冗余 unchanged command”只丢弃 no-op command；
6. command insert/remove 已进入同一 repair 闭环，不需要手工改 plan。

## 6. 验证结果

### 6.1 全量确定性测试

```text
572 passed, 7 skipped in 19.69s
```

其中包含：

- 状态快照、预算、脱敏与 hash；
- Gateway trace 引用；
- failure classifier 与 RepairScope；
- 全部 RepairPatch 操作；
- solve、repair、continuation 和 repair reuse；
- frozen artifact replay；
- Knowledge/Skills 1.2 回归。

### 6.2 OpenFOAM preflight

```text
status: PASS
bubblewrap_launch: unavailable (NETLINK_ROUTE permission)
execution_backend: audited typed host fallback, blocking check PASS
solver:icoFoam: PASS
```

该结果说明 bubblewrap 在当前宿主环境不可用，但流程没有等待交互授权，也没有形成环境 blocker。

wheel 构建与模块清单核验通过：

```text
/tmp/foampilot-agent-harness-p0-p1-wheel-20260806/foampilot-0.1.0-py3-none-any.whl
SHA256 0709e2ed322e718d9aea88ce1d733024502ad4ee4790e0005c35ecb073402725
size 482659 bytes
```

wheel 已确认包含 `status.py`、`failure.py`、`repair_scope.py` 与 `repair_patch.py`。

### 6.3 真实 Foundation OpenFOAM v10 gates

```text
real continuation: 1 passed
real command repair: 2 passed
```

真实 command repair 分别验证：

- 初始 plan 缺少 `blockMesh/checkMesh`，solver 因缺 mesh 失败，RepairPatch 在 solver 前插入两个命令，
  第二 attempt 完成 public validation；
- 完整求解后存在带非法 option 的 optional command，RepairPatch 删除该命令，受影响阶段安全重跑并通过。

真实 continuation gate 验证：solver dictionary failure → repair backend deferred → immutable child resume →
定向替换 `fvSchemes` → public validation pass。

## 7. 当前边界

P0/P1 提高的是状态准确性、失败归因、修复范围与命令可编辑性，不等同于已经提高所有 CFD 题目的
物理准确率。完整 case 仍由模型编写，低证据的 solver failure 仍可能落入通用分类；分类器不会为了获得
更具体标签额外调用模型。

P2 受控经验学习与 P3 实验 profile 尚未实施，因此整个 Agent Harness v2 规格不能标记为全部完成。
30 题冷路径基线现已完成，详见
[官方题库 30 题：Agent Harness v2 冷路径基线](2026-08-06-official-corpus-30-agent-harness-v2.md)。
基线后确定性修复和三题真实定向复测已经完成，详见同一报告的复测章节；20 题 holdout 尚未执行。
后续运行继续以新产物区分：

- case/physics 知识不足；
- deterministic workflow/inspection 问题；
- provider/environment blocker；
- repair scope 或 patch policy 问题。

## 8. 证据口径

本报告中的“通过”分别指：

- deterministic tests 通过；
- 真实 OpenFOAM 进程与 public validation 通过；
- 不代表 30+20 qualification 已完成；
- 不代表新的 Knowledge/Skills 已通过全题库物理评测。
