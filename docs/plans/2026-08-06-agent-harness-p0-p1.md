# FoamPilot Agent Harness v2：P0/P1 实施计划

状态：P0/P1 已完成；30+20 题库 gate 按既定顺序进入下一阶段
日期：2026-08-06
依据：[Agent Harness 演进 v2 规格](../design/agent-harness-evolution-v2-design.md)
范围：只实施 P0 状态快照与 P1 定向修复；不实施 P2、P3、MCP、IDE 或第二套状态机

## 1. 实施原则

- `NativeAgent.solve()` 与 `resume()` 继续作为唯一编排入口；
- Runner、Evaluator、Gateway 与 `ExecutionPlan v3` 的职责不变；
- 正常成功路径不增加模型调用；
- 状态、失败分类、修复范围和补丁校验全部由确定性代码完成；
- 旧 `RepairDecision` 在新闭环接管后删除，不长期保留双路径；
- 每一项先写失败测试，再实现最小代码，再运行受影响回归；
- 不提交、不推送，保留工作区中已有的 Knowledge/Skills 1.2 改动。

## 2. Task 1：P0 状态快照契约与构造器

### RED

- 新增 `tests/test_agent_status.py`；
- 覆盖 author/repair 两种快照、预算余量、最近完成阶段、能力与 region、上下文 ID；
- 覆盖 protected path 只暴露数量/摘要；
- 覆盖 stage、attempt、failure 不一致时返回 `AGENT_STATUS_INCONSISTENT`；
- 覆盖相同事实源得到稳定 SHA256。

### GREEN

- 新增 `src/foampilot/agent/status.py`；
- 为 `ModelBudgetLedger` 增加只读预算统计，不改变既有预留语义；
- 从 `WorkflowStore` 读取最后事件与最后完成阶段；
- 构造 `AgentStatusSnapshot`，不复制 TaskSpec、文件正文或 protected path 原文。

### Gate

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_agent_status.py tests/test_model_budgets.py tests/test_workflow_store.py
```

## 3. Task 2：P0 模型边界、产物与 trace 引用

### RED

- author prompt 必须包含一份状态快照；
- repair prompt 必须包含更新后的状态快照；
- 每次调用前落盘 `agent-status-author-01.json` 或 `agent-status-repair-01.json`；
- 模型 trace 只记录状态产物相对路径和 hash，不记录状态正文；
- continuation 重建 repair 快照时继承 parent attempt，但使用 child 的事件序列和 lineage 预算。

### GREEN

- 为 `ModelRequest` 增加脱敏 `context_artifacts` 引用；
- 扩展 `ModelAttemptTrace`，保存引用，不保存 prompt/正文；
- `author_case_bundle()` 与 `request_repair()` 接收状态快照；
- orchestrator 在 `MODEL_*_STARTED` 事件之后、实际请求之前构造并写入快照；
- 构造失败以中文 `message/recovery` 和稳定 code `AGENT_STATUS_INCONSISTENT` 结束。

### Gate

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_native_case_generation.py \
  tests/test_native_repair.py tests/test_native_agent_state_machine.py tests/test_continuation.py
```

## 4. Task 3：P1 失败分类器

### RED

- 新增 `tests/test_failure_classifier.py`；
- 覆盖 static inspection、mesh、initialization、solver、postprocess、public validation；
- 覆盖 OpenFOAM 常见日志：missing keyword、unknown function object、missing field/grouped field、
  dimension mismatch、command not found/failed；
- 覆盖低证据时的 `unclassified_native_failure`，不得猜测高置信度；
- 覆盖分类与原报告矛盾时 `FAILURE_CLASSIFICATION_INVALID`。

### GREEN

- 新增 `src/foampilot/agent/failure.py`；
- 输出稳定 taxonomy、确定性 confidence、证据、scope hint 和允许操作；
- 每次失败落盘 `failure-classification-attempt-NN.json`；
- 分类只辅助 repair，不覆盖 `primary_failure`。

## 5. Task 4：P1 RepairScope

### RED

- 新增 `tests/test_repair_scope.py`；
- missing keyword 只选相关字典/块和失败命令；
- mesh failure 只选网格及必要的 patch 同步字段；
- 大文件按 `matching_block`、`head_tail_excerpt`、`structure_only` 或 `metadata_only` 降级；
- scope 中不得出现 public asset 内容、protected path、无关 case 全量文件；
- 证据不足时显式返回 `REPAIR_SCOPE_UNRESOLVED`。

### GREEN

- 新增 `src/foampilot/agent/repair_scope.py`；
- 使用 classification、manifest、命令 stage、Knowledge ID 和当前文件构造范围；
- prompt 只装配 scope 选中的表示；
- 落盘 `repair-scope-attempt-NN.json` 并记录排除文件数。

## 6. Task 5：P1 RepairPatch 与命令操作

### RED

- 将 repair 单元测试迁移到 `RepairPatch`；
- 覆盖文件 add/replace；
- 覆盖命令 insert_before/insert_after/replace/remove；
- 覆盖 anchor 不存在、重复 step、stage 逆序、public asset、protected path、shell/外部路径；
- 覆盖补丁应用后完整 normalizer/policy/schema 检查；
- 覆盖“文件确有变化 + 冗余 unchanged command”不会使整次正确修复作废；
- 覆盖全补丁 no-op 和重复 failure fingerprint 的有限停止。

### GREEN

- 用 `RepairPatch`、`FileOperation`、`CommandOperation` 替换 `RepairDecision`；
- 新增确定性 patch normalizer：丢弃单个精确 no-op 操作，但至少保留一个实际变化；
- `RepairPatchApplier` 返回新 plan 与真实 change set；
- repair reuse 只依据真实 change set 计算最早重跑阶段；
- 删除旧 repair 应用和校验路径。

## 7. Task 6：编排集成与真实缺陷回归

### RED / replay

- continuation、state machine、repair reuse 和冻结 artifact replay 全部通过新契约；
- 新增 include fragment 回归：被 `#include` 引用的 headerless 文件不是独立 OpenFOAM object；
- 新增错误文件定向回归：missing keyword 的 scope 不允许无证据修改其他物性文件；
- 新增 grouped field 回归：scope/证据保留完整字段名，不在摘要阶段丢失 `thermo:rho`。

### GREEN

- solve 与 resume 统一使用 classifier → scope → status → model → patch → apply；
- 保存 `failure-classification-*`、`repair-scope-*`、`agent-status-*`、`repair-patch-*`；
- 所有异常提供稳定英文 code 和中文 `message/recovery`；
- include-aware inspection 只放宽真实 include fragment，不放宽普通字段/字典头检查。

## 8. Task 7：验证与阶段报告

### 确定性 gate

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
git diff --check
```

如 package data 变化，重新构建 wheel 并核验文件清单；仅 Python 源码变化仍至少执行 wheel build。

### 真实 gate

1. 一个缺少 initialization command 的公开小 case，验证 `insert_before`；
2. 一个含多余 command 的公开小 case，验证 `remove`；
3. 重放 Knowledge/Skills 1.2 四题中的最小失败夹具；
4. 记录目标 solver 进入率、修复前耗时、scope 字节数、实际重跑起点和模型调用数。

### 收尾

- 在 `docs/reports/` 新增 P0/P1 实施与验证报告；
- 更新设计文档状态与索引；
- 再进入先前延后的 30 题复测与 20 题 holdout；
- 不把 P0/P1 的通过等同于 CFD 物理准确率提升。

## 9. 完成定义

P0/P1 只有同时满足以下条件才算完成：

- 正常成功路径没有新增模型调用；
- author/repair 状态快照可重建、脱敏并进入 artifact manifest；
- failure classification 与 RepairScope 不依赖模型；
- repair 支持 add/replace 文件以及 insert/replace/remove command；
- 旧 `RepairDecision` 不再是运行时规范路径；
- include fragment、冗余 no-op command、修错文件和 grouped field 四类真实回归有测试；
- deterministic suite、artifact replay 和至少两个真实 OpenFOAM repair gate 有明确结果；
- 未实施 P2/P3/IDE，未引入第二套状态机或额外 Agent。
