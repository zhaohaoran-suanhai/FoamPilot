# FoamPilot 恢复、Resume 与 Rerun 语义规格

状态：已确认，按三项串行任务中的第三项实施。本文依赖
[核心执行可观测性与活性规格](execution-observability-liveness-design.md)和
[本机任务监督与 Desktop 可靠性规格](local-job-supervision-reliability-design.md)，明确 attach、
orphan recovery、strict resume、rerun 和未来 OpenFOAM continuation 的边界。

## 1. 背景与问题定义

“恢复任务”可能指完全不同的操作：重新连接仍在运行的 worker、固化一个异常中断的 run、
重试模型 generation/repair、使用相同输入重新求解，或从 OpenFOAM 时间目录继续计算。若把这些
操作都叫作 resume，界面会错误承诺工作复用，并可能修改不可变证据或在不完整 case 上继续执行。

FoamPilot 已有 strict resume：只接受 manifest 有效、兼容性指纹一致、terminal blocker
可重试且 `from_stage` 为 generation/repair 的 parent，并创建新的 child run。该能力必须保留，
不能扩展成通用进程恢复。

## 2. 目标

1. 为每种恢复操作定义唯一名称、前置条件、复用范围和新产物；
2. Desktop 只在证据满足时启用对应操作；
3. active、unresponsive、orphaned、cancelled 和 finalized run 不被混淆；
4. parent run/attempt 保持不可变，恢复操作始终留下 lineage；
5. worker 崩溃后优先保存证据和清理进程，不伪装成 solver failure；
6. strict resume 继续保持当前 generation/repair 的窄边界；
7. OpenFOAM continuation 在单独验证前明确显示为不支持。

## 3. 非目标

- 不承诺恢复模型进程内部状态或重新连接任意已经失去父进程的 CLI；
- 不从缺失、损坏或未验证的 artifact 推断成功；
- 不原地修改 finalized run 或 attempt；
- 不把修改 TaskSpec、asset、模型、Knowledge、Skill 或 runtime policy 伪装成 strict resume；
- 本阶段不实现通用 OpenFOAM continuation；
- 不实现远程 scheduler resume、checkpoint migration 或跨机恢复。

## 4. 固定术语

| 操作 | 是否创建新 run | 含义 |
| --- | --- | --- |
| `attach` | 否 | 重新连接仍由有效 worker 监督的同一 job/run |
| `reconcile` | 否 | 只读核验 receipt、lock、heartbeat、PID identity 与 artifact 状态 |
| `recover-finalize` | 否 | 在原 owner 和全部受监督进程确认消失后，把未终态 run 固化为 interrupted |
| `strict resume` | 是 | 从已验证 parent 的可重试 generation/repair checkpoint 创建 child run |
| `rerun` | 是 | 使用相同规范输入开始一次完整新运行 |
| `rerun_with_changes` | 是 | 输入、模型、资产、Knowledge/Skill 或 policy 改变后的完整新运行 |
| `OpenFOAM continuation` | 是 | 未来从经过验证的时间目录继续特定 solver；本阶段不支持 |

`attach` 不消耗 model attempt 或 resume budget。`recover-finalize` 不执行模型或 OpenFOAM。

## 5. Reconcile 决策表

Desktop 或 CLI 恢复入口必须先执行确定性 reconcile：

| receipt/进程/产物 | 结论 | 允许操作 |
| --- | --- | --- |
| worker identity 匹配、lock 持有、heartbeat 新鲜 | `RUNNING` | attach、请求取消 |
| worker identity 匹配、heartbeat 过期 | `UNRESPONSIVE` | 只读 attach、诊断、请求取消 |
| worker 消失、已记录 child identity 仍匹配且存活 | `ORPHANED_ACTIVE` | 只读观察、受控终止；禁止接管 workflow |
| worker/child 均消失、无 terminal summary | `ORPHANED_STOPPED` | recover-finalize、rerun |
| terminal summary 与 manifest 有效 | `FINALIZED` | report；按 eligibility 决定 resume/rerun |
| terminal artifact 损坏或 manifest 无效 | `EVIDENCE_DAMAGED` | 安全只读、rerun；禁止 resume |

仅凭 PID 存在不能判断 running；必须同时核对 boot ID、process start token、job ID、writer
lock 和路径边界。reconcile 不向未知进程发送信号。

## 6. Recover-finalize

`recover-finalize` 只在以下条件全部满足时执行：

1. job writer lock 可安全取得；
2. 原 worker identity 已确认不存在；
3. 已记录的全部 child process group 已确认退出，或先按本机监督规格完成受控终止；
4. run 路径、task 和现有 checkpoints 可安全读取；
5. run 尚无 terminal summary/manifest。

恢复工具追加 recovery workflow event，写入 `interruption.json`，并生成：

- `WorkflowState.INTERRUPTED`；
- failure domain `workflow`；
- 稳定 code `WORKER_INTERRUPTED` 或 `HOST_RESTARTED`；
- 最后确认的 stage/step、heartbeat、日志 offset 和进程清理证据；
- 不声称 OpenFOAM step 成功或 public validation pass。

随后使用现有 ArtifactStore 固化 manifest。`INTERRUPTED` 是中立终态，不进入自动 repair，
也不等同于 `FAILED` 或 `CANCELLED`。

recover-finalize 一律写入 `resume.allowed=false`。硬 kill、断电或未知退出不能转换为可重试
generation/repair；用户可以从已保存的规范输入执行 rerun。现有 strict resume 仍只接受已经
由正常终止路径写出 eligibility 的 finalized parent。

## 7. Strict resume

保留当前正式边界：

- parent summary/manifest 有效；
- `resume.allowed=true`；
- terminal blocker retryable；
- `from_stage` 仅为 `MODEL_GENERATION_STARTED` 或 `MODEL_REPAIR_STARTED`；
- TaskSpec、public asset、backend/model、backend policy、package、plan schema、Knowledge、
  Skills、OpenFOAM target 和 executable compatibility 通过现有 fingerprint；
- lineage continuation 和 transport attempt 预算未超限。

strict resume 创建不可变 child run，并记录 parent run ID 与 manifest SHA256。它可以复用经过
验证的 repair evidence，但不能复用未知状态的 OS 进程或未完成 OpenFOAM command。

Desktop 的按钮名称使用“恢复模型生成”或“恢复模型修复”，同时展示 from stage、parent、剩余
预算和拒绝原因，不使用含糊的“继续求解”。

## 8. Rerun 与 rerun_with_changes

### 8.1 Rerun

`rerun` 使用同一规范 TaskSpec 和选择的 runtime/backend 配置开始完整新 run。它不复用 active
进程，不继承 prior success，也不消耗 strict resume continuation budget。新 run 记录
`relation=rerun_same_input` 和 parent manifest，但独立执行 preflight、generation、inspection、
OpenFOAM 和 validation。

### 8.2 Rerun with changes

以下任一变化必须进入 `rerun_with_changes`：

- TaskDraft/TaskSpec；
- geometry 或 public asset 内容；
- backend/model 或 backend policy；
- package/code revision；
- Knowledge 或 Skill；
- execution policy、OpenFOAM target 或不兼容 executable 集合；
- 后续人工 case revision。

新 run 保存变更前后 hash 和可公开的 change category；不复制 secret、prompt 或 evaluator 私有
证据。未选择显式 plan-reuse/derived-cache 能力时，完整重跑，不进行隐式复用。

## 9. Lineage 契约

现有 `parent_run` 继续服务 strict resume。新增统一 `lineage.json` 只描述关系，不改变父产物：

```text
schema_version = 1
relation = strict_resume | rerun_same_input | rerun_with_changes | openfoam_continuation
parent_run_id
parent_manifest_sha256
created_at
input_hash_before
input_hash_after
change_categories
reused_evidence_paths
```

`recover-finalize` 在同一未固化 run 内完成，因此不创建 child lineage；其行为记录在
`interruption.json` 和 workflow event 中。任何引用 parent 的操作必须先验证 parent manifest。

## 10. OpenFOAM continuation 的延期边界

未来 continuation 不能只检查“存在最新时间目录”。实施前需要按 solver family 单独证明：

- 最新时间目录完整且字段可读；
- `startFrom/startTime`、`endTime`、write interval 和 function objects 变化受控；
- decomposition、processor 数量和 `decomposeParDict` 兼容；
- 是否需要 `reconstructPar`；
- dynamic mesh、lagrangian、multiregion、chemistry 等附加状态完整；
- 续算前后的 residual、守恒和 public validation 可以正确拼接；
- continuation 生成新 run/attempt，不修改原 finalized evidence。

在上述 solver-specific gate 建立前，Desktop 对求解阶段中断只提供 attach、取消、
recover-finalize 和 rerun，不显示“从最后时间步继续”。

## 11. Desktop 操作矩阵

| 当前状态 | 主操作 | 明确禁止 |
| --- | --- | --- |
| running | attach、cancel | resume、rerun 覆盖当前 run |
| unresponsive | inspect、cancel | 直接判 failed |
| orphaned active | inspect、terminate orphan | 接管未知 exit status 后继续 workflow |
| orphaned stopped | recover-finalize、rerun | strict resume，除非随后正式 eligibility 通过 |
| cancelled | rerun | 自动 repair、假装 continuation |
| interrupted | rerun；条件满足时 strict resume | 修改原 run |
| finalized retryable generation/repair | strict resume、rerun | OpenFOAM continuation |
| finalized success/failure | report、rerun | 无证据的 resume |

所有禁用操作都显示稳定 code、中文原因和恢复建议。

## 12. 测试与验收

- running worker 重连只执行 attach，不创建新 run；
- stale heartbeat 不被误判 solver failure；
- worker dead/child alive 被判 `ORPHANED_ACTIVE`，不能 strict resume；
- orphan process identity 不匹配时拒绝 kill；
- worker/child 全部消失后 recover-finalize 生成 `INTERRUPTED` 和有效 manifest；
- recover-finalize 重复调用幂等，不产生第二个 summary；
- manifest invalid、fingerprint changed、budget exhausted 均拒绝 strict resume；
- generation/repair strict resume 继续通过现有 continuation tests 与真实 gate；
- rerun_same_input 和 rerun_with_changes 的 lineage/hash 正确；
- Desktop 对每个状态只启用操作矩阵允许的动作；
- 所有恢复路径保留原 parent/attempt 不可变。

## 13. 完成定义

本规格完成表示用户能够明确知道“重新连接、固化中断证据、恢复模型请求、重新完整运行”之间的
区别，并且每项操作都有可验证的前置条件和 lineage。

它不表示 FoamPilot 已支持通用 OpenFOAM 断点续算。该能力只有在独立 solver-specific 规格和
真实 continuation gate 获得批准后才能进入实现。
