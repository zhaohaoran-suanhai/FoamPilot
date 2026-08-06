# FoamPilot 快速开始

## 精简主路径的功能

FoamPilot 可以把完整的中文或英文 CFD 请求编译为公开 `TaskSpec`，再由 `foampilot solve`
执行一次原生 Foundation OpenFOAM v10 运行。已有结构化系统也可以直接提供 TaskSpec。

运行流程如下：

1. 校验公开任务；
2. 发现本机 OpenFOAM 环境；
3. 对声明的公开几何执行 hash、单位、surface/patch 和拓扑探测；
4. 根据公开证据生成由系统负责的 `CapabilityProfile`；
5. 按语义槽位最多检索一条有界公开知识，将无关槽位留空，并路由 Skills；
6. 通过具备重试、deadline 和熔断能力的 Gateway，发起一次逻辑模型请求，生成
   完整 CaseBundle；
7. 只对无歧义的 MPI wrapper 进行安全规范化，然后执行 typed policy；
8. 将声明的文件写入空的 attempt 目录；
9. 检查高置信度跨文件语义，并执行原生命令；
10. 生成结构化网格质量报告并执行 evaluator 公开检查；
11. 如果尝试预算允许，请求一次基于失败证据的定向修复；
12. 如果可重试的模型后端故障中断生成或修复，将运行固化为 `DEFERRED`，
    并保存 strict resume 元数据；
13. 为每个终态运行固化 artifact manifest。

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
python -m pip install -e ".[test]"
foampilot preflight --json
foampilot model doctor --json
```

当前本机配置使用：

- `/home/edwin/workplace/OpenFOAM-10`；
- `/usr/local/bin/bwrap`（推荐；`auto` 后端可降级）；
- `/home/edwin/feal-venv-py312/bin/python`。

如果 FoamPilot 本身运行在已经受限的开发沙箱中，bubblewrap 可能无法再次创建
namespace。`auto` 模式会把该探测记为非阻断并改用 audited host；preflight 和 step
产物会记录实际后端。host fallback 不具有 network namespace 隔离。

## 校验、生成计划、求解和报告

### 从自然语言生成 TaskSpec

先把问题定义完整写入 `request.md`。单位、物性、边界数值、初始条件和瞬态终止时间应明确；
几何附件必须以相对路径显式声明：

```bash
foampilot task draft \
  --request-file request.md \
  --asset geometry/body.stl \
  --asset-root . \
  --output task-draft.yaml \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json

foampilot task validate-draft task-draft.yaml --json

foampilot task compile task-draft.yaml \
  --output task.yaml \
  --json
```

不带附件时省略 `--asset` 和 `--asset-root`。`draft` 只把公开 request 和附件 metadata 交给
模型，不读取目标 tutorial，也不运行 OpenFOAM。完整且有明确证据的事实可以直接确认；缺少
高影响信息时返回 `TASK_REQUEST_INCOMPLETE` 或 `TASK_CONFIRMATION_REQUIRED`，并在 draft/review
中给出中文问题。当前 CLI 不包含交互式澄清表单；补充 request 或由上游 Agent/界面记录
`user_confirmation` 后，再生成并校验新的 draft。输出文件采用独占创建，不覆盖已有文件。
模型进程、网络或服务暂时不可用时，`draft` 返回退出码 3、稳定 backend code、中文 message 和
recovery，不会把问题记为 OpenFOAM failure。

TaskSpec 生成后进入与手写任务完全相同的规范路径：

```bash
foampilot validate task.yaml --json

foampilot solve task.yaml \
  --run-root /tmp/foampilot-native-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

### 直接使用已有 TaskSpec

```bash
foampilot validate examples/tasks/non-tutorial-side-driven-box.yaml --json

foampilot plan examples/tasks/non-tutorial-side-driven-box.yaml \
  --output /tmp/side-driven-plan.json \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json

foampilot solve examples/tasks/non-tutorial-side-driven-box.yaml \
  --run-root /tmp/foampilot-native-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json

foampilot report /tmp/foampilot-native-runs/RUN_DIR --json
```

`plan` 和 `solve` 的初始阶段都会针对整个 case bundle 发起一次逻辑模型请求。
一次逻辑请求内部可以包含次数受限的传输重试。

`solve` 只有在状态为 `PUBLIC_VALIDATION_PASS` 时返回 0；模型后端暂缓或环境
阻断返回 3；执行失败或验证失败返回 4。

### 显式性能复用

同一个规范 `TaskSpec` 需要重复演示或工程复算时，可以显式选择一个已验证 source run：

```bash
foampilot solve task.yaml \
  --reuse-verified-plan /tmp/foampilot-native-runs/SOURCE_RUN \
  --run-root /tmp/foampilot-native-runs \
  --json
```

该路径不创建 ModelGateway，也不发起 routing、generation 或 repair 模型请求。source 必须
拥有有效 artifact manifest，并有 manifested `blockMesh`/`checkMesh`/目标 solver 证据；
TaskSpec、公开资产、OpenFOAM 目标、solver 可用性和 MPI 预算必须严格兼容。拒绝不会静默切回
冷路径。

如果几何和网格依赖也完全相同，可以增加一个显式缓存根目录：

```bash
foampilot solve task.yaml \
  --reuse-verified-plan /tmp/foampilot-native-runs/SOURCE_RUN \
  --derived-cache /tmp/foampilot-derived-cache \
  --run-root /tmp/foampilot-native-runs \
  --json
```

缓存键包含几何事实、公开资产字节、mesh intent、网格字典和命令、region 以及工具版本。
命中后通过复制恢复 `polyMesh`，不会用可写 hardlink 连接 source；当前 run 仍执行
`checkMesh`、目标 solver 和公开验证。动态网格、多区域或依赖不明确时会保守 miss，不会猜测复用。

repair 也会根据实际修改文件选择最早重跑阶段。只改 `fvSchemes`/`fvSolution` 时可以复用
上一 attempt 的网格并从 solve 前检查继续；改初始场从 initialize 开始；改网格、patch、include、
动态网格或命令依赖时退回完整网格路径。每个 attempt 仍然独立且 parent 不会被修改。

## 模型后端暂缓和续跑

生成阶段中断时尚未产生 native 结果：

```json
{
  "workflow_state": "DEFERRED",
  "native_status": null,
  "terminal_blocker": {
    "domain": "backend",
    "code": "OVERLOADED",
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
    "domain": "backend",
    "code": "OVERLOADED"
  },
  "resume": {
    "allowed": true,
    "from_stage": "MODEL_REPAIR_STARTED"
  }
}
```

模型后端恢复后执行：

```bash
foampilot resume /tmp/foampilot-native-runs/PARENT_RUN \
  --run-root /tmp/foampilot-native-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

创建 child run 前，`resume` 会校验 parent manifest、兼容性指纹、可重试 blocker、
continuation 数量、传输尝试预算以及当前 OpenFOAM 能力。strict compatibility 或
输入被拒绝时返回 2；再次发生 backend/environment 暂缓时返回 3；native 执行
失败时返回 4。

修改代码、TaskSpec、公开资产、模型、backend policy、Knowledge 或 Skills 后，
不要使用 strict resume。应重新执行普通 `solve`，并将其记录为
`rerun_with_changes`，否则两个不同实验会被错误归入同一 lineage。

## 产物目录

每次运行包含：

```text
task.yaml
environment.json
geometry-facts.json              # 几何任务存在
capability-profile.json
agent-context.json
resume-compatibility.json
authored-execution-plan.json
plan-normalization.json
execution-plan.json
workflow-events.jsonl
model-attempts.jsonl
model-configuration.json
performance-context.json
performance-summary.json
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
  mesh-quality-report.json       # 已进入原生执行时存在
  mesh-cache.json                # 显式启用派生缓存时存在
  execution-reuse.json           # 命中网格或 repair 前序复用时存在
  public-validation.json
  case/
    ... Agent 生成的 OpenFOAM 文件 ...
    .foampilot/logs/
```

显式计划复用的 run 还包含 `plan-reuse.json`。`performance-summary.json` 区分
`cold`、`warm_plan`、`warm_mesh` 和 `repair_reuse`，并记录模型逻辑请求、传输次数、各 native
阶段耗时和 cache hit/miss。manifest 自身的 `build_seconds` 与 run 内性能摘要分开保存，避免
归档文件循环改写。

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
