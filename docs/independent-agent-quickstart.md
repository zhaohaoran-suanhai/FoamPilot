# FoamPilot 快速开始

## 精简主路径的功能

`foampilot solve` 将一份面向自然语言需求的公开 `TaskSpec` 转换为一次原生
Foundation OpenFOAM v10 运行。FoamPilot 是独立的 Python 工具包。

运行流程如下：

1. 校验公开任务；
2. 发现本机 OpenFOAM 环境；
3. 根据公开证据生成由系统负责的 `CapabilityProfile`；
4. 按语义槽位最多检索一条有界公开知识，将无关槽位留空，并路由 Skills；
5. 通过具备重试、deadline 和熔断能力的 Gateway，发起一次逻辑模型请求，生成
   完整 CaseBundle；
6. 只对无歧义的 MPI wrapper 进行安全规范化，然后执行 typed policy；
7. 将声明的文件写入空的 attempt 目录；
8. 检查高置信度跨文件语义，并执行原生命令；
9. 执行由 evaluator 负责的公开检查；
10. 如果尝试预算允许，请求一次基于失败证据的定向修复；
11. 如果可重试的 provider 故障中断生成或修复，将运行固化为 `DEFERRED`，
    并保存 strict resume 元数据；
12. 为每个终态运行固化 artifact manifest。

规范的 `ExecutionPlan` v3 结构为：

```text
manifest   = {solver, family, regions, fields, patches, models, ...}
files[]    = {path, content}
commands[] = {step_id, stage, executable, args, mpi_ranks, timeout_seconds}
```

求解器选择、字典结构、数值方法、初始化和后处理仍由 Agent 决定。确定性代码在
执行前检查结构、安全和高置信度语义约束，但不会替 Agent 审查完整 CFD 策略。

## 安装和预检

```bash
git clone git@github.com:zhaohaoran-suanhai/FoamPilot.git
cd FoamPilot
python -m pip install -e ".[codex,test]"
foampilot preflight --json
```

当前本机配置使用：

- `/home/edwin/workplace/OpenFOAM-10`；
- `/usr/local/bin/bwrap`（推荐；`auto` 后端可降级）；
- `/home/edwin/feal-venv-py312/bin/python`。

如果 FoamPilot 本身运行在已经受限的开发沙箱中，bubblewrap 可能无法再次创建
namespace。`auto` 模式会把该探测记为非阻断并改用 audited host；preflight 和 step
产物会记录实际后端。host fallback 不具有 network namespace 隔离。

## 校验、生成计划、求解和报告

```bash
foampilot validate examples/tasks/non-tutorial-side-driven-box.yaml --json

foampilot plan examples/tasks/non-tutorial-side-driven-box.yaml \
  --output /tmp/side-driven-plan.json \
  --model-name gpt-5.6-sol \
  --json

foampilot solve examples/tasks/non-tutorial-side-driven-box.yaml \
  --run-root /tmp/foampilot-native-runs \
  --model-name gpt-5.6-sol \
  --json

foampilot report /tmp/foampilot-native-runs/RUN_DIR --json
```

`plan` 和 `solve` 的初始阶段都会针对整个 case bundle 发起一次逻辑模型请求。
一次逻辑请求内部可以包含次数受限的传输重试。

`solve` 只有在状态为 `PUBLIC_VALIDATION_PASS` 时返回 0；provider 暂缓或环境
阻断返回 3；执行失败或验证失败返回 4。

## Provider 暂缓和续跑

生成阶段中断时尚未产生 native 结果：

```json
{
  "workflow_state": "DEFERRED",
  "native_status": null,
  "terminal_blocker": {
    "domain": "provider",
    "code": "PROVIDER_OVERLOADED",
    "retryable": true
  },
  "resume": {
    "allowed": true,
    "from_stage": "MODEL_GENERATION_STARTED"
  }
}
```

修复阶段中断时会保留原始 native 根因：

```json
{
  "workflow_state": "DEFERRED",
  "native_status": "SOLVER_FAILED",
  "primary_failure": {"domain": "solver"},
  "terminal_blocker": {
    "domain": "provider",
    "code": "PROVIDER_OVERLOADED"
  },
  "resume": {
    "allowed": true,
    "from_stage": "MODEL_REPAIR_STARTED"
  }
}
```

provider 恢复后执行：

```bash
foampilot resume /tmp/foampilot-native-runs/PARENT_RUN \
  --run-root /tmp/foampilot-native-runs \
  --model-name gpt-5.6-sol \
  --json
```

创建 child run 前，`resume` 会校验 parent manifest、兼容性指纹、可重试 blocker、
continuation 数量、传输尝试预算以及当前 OpenFOAM 能力。strict compatibility 或
输入被拒绝时返回 2；再次发生 provider/environment 暂缓时返回 3；native 执行
失败时返回 4。

修改代码、TaskSpec、公开资产、模型、provider policy、Knowledge 或 Skills 后，
不要使用 strict resume。应重新执行普通 `solve`，并将其记录为
`rerun_with_changes`，否则两个不同实验会被错误归入同一 lineage。

## 产物目录

每次运行包含：

```text
task.yaml
environment.json
capability-profile.json
agent-context.json
resume-compatibility.json
authored-execution-plan.json
plan-normalization.json
execution-plan.json
workflow-events.jsonl
model-attempts.jsonl
model-configuration.json
summary.json
artifact-manifest.json
checkpoints/
  active-plan-attempt-01.json
  public-validation-attempt-01.json
  repair-evidence-attempt-01.json
attempt-01/
  execution-plan.json
  generation-trace.json
  static-inspection.json
  run-result.json
  public-validation.json
  case/
    ... Agent 生成的 OpenFOAM 文件 ...
    .foampilot/logs/
```

child run 还包含 `continuation.json`；其 summary 会记录 parent run ID 和 parent
manifest SHA256。应分别验证 parent 和 child：

```bash
foampilot report /tmp/foampilot-native-runs/PARENT_RUN --json
foampilot report /tmp/foampilot-native-runs/CHILD_RUN --json
```

失败的 attempt 不会被覆盖。修复后的 attempt 会根据修改后的计划在新目录中重新
物化。安全修复可以增加缺失的生成字典，但不能越出 case 目录、覆盖公开资产、
引用受保护路径、引入新的命令步骤或绕过资源策略。

## 评测边界

Evaluator 负责 `public_checks`；`TaskSpec.agent_payload()` 会排除这些检查以及所有
受保护路径。Agent 只能收到公开物理需求、必需输出、验收描述、环境清单、由系统
路由的 capability profile、按槽位限制的公开知识以及路由后的 Skills。

官方目标 case 和 golden result 始终只对 evaluator 可见。正式 benchmark 可以在
运行结束后把冻结产物与私有参考进行比较，但这些比较不得影响 case 生成或 repair。

15 题分角色评测协议和报告边界见
[受控评测](qualification.md)。
