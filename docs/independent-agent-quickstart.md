# FoamPilot 快速开始

## 精简主路径的功能

FoamPilot 可以把完整的中文或英文 CFD 请求编译为公开 `TaskSpec v3`，再由 `foampilot solve`
执行一次原生 Foundation OpenFOAM v10 运行。已有结构化系统也可以直接提供 TaskSpec。

运行流程如下：

1. 校验公开任务；
2. 发现本机 OpenFOAM 环境；
3. 对声明的公开几何执行 hash、单位、surface/patch 和拓扑探测；
4. 根据公开证据生成由系统负责的 `CapabilityProfile`；
5. 按语义槽位最多检索一条有界公开知识，将无关槽位留空，并路由 Skills；
6. 分别生成 `SimulationIntent` 和不含文件/命令的 `CaseDesignProposal`；
7. 用确定性 Requirement Resolver/RiskGate 决定是否冻结 `CaseDesign`；
8. 只有 `READY_TO_AUTHOR` 才由 Case Author 生成不含命令的完整 CaseBundle；
9. CaseVerifier 检查设计一致性，PlanCompiler 确定性生成 ExecutionPlan v4 并执行 typed policy；
10. 将声明的文件写入空的 attempt 目录；
11. 检查高置信度跨文件语义，并执行原生命令；
12. 生成结构化网格质量报告并执行 evaluator 公开检查；
13. 如果尝试预算、失败分类和冻结数值 envelope 允许，请求一次不含命令的定向修复；
14. 如果可重试的模型后端故障中断生成或修复，将运行固化为 `DEFERRED`，
    并保存 strict resume 元数据；
15. 为每个终态运行固化 artifact manifest。

规范权限分为两个结构：

```text
CaseBundle（模型输出）
  manifest = {solver, family, regions, fields, patches, models, ...}
  files[]  = {path, content}

ExecutionPlan v4（系统编译）
  compiled_from_design_sha256
  compiler_identities
  manifest + files[]
  commands[] = {step_id, stage, executable, args, mpi_ranks, timeout_seconds}
```

求解器、物理模型、数值方法和时间方案在 CaseDesign 阶段冻结；Case Author 只负责把设计写成
一致的 OpenFOAM 字典。Case Author 不生成命令；命令顺序、executable、MPI 和超时由注册的
第一方 contributor 与 PlanCompiler 确定。

当前 authoring 输入只接受 `TaskSpec v3`；`TaskSpec v2` 只用于历史 run 的只读展示。
Case Author 前的 RiskGate 不接受模型自报 confidence，也没有 accept-all 或 continue-anyway。

## 安装和预检

```bash
git clone git@github.com:zhaohaoran-suanhai/FoamPilot.git
cd FoamPilot
python -m pip install -e ".[test]"
mkdir -p /path/to/writable/codex-home
CODEX_HOME=/path/to/writable/codex-home codex login
export CODEX_HOME=/path/to/writable/codex-home
foampilot preflight --json
foampilot model doctor --json
```

内置 `codex-cli` profile 要求 `CODEX_HOME` 是已经存在的绝对可写目录；未设置时使用
`$HOME/.codex`。应在一次性安装配置中对同一个状态根完成登录，此后每个 FoamPilot 任务不再
需要额外认证输入。FoamPilot 不读取、复制或链接 Codex 认证文件。`model doctor` 只证明本地
状态根、executable 和登录状态，不发起计费模型请求，也不证明稍后的网络仍然可用。

`--ephemeral` 只禁止保存 Codex session rollout，状态根仍需可写。状态根问题返回不可重试的
`BACKEND_MISCONFIGURED`，登录问题返回 `AUTH_FAILED`，真实传输受阻返回可重试的
`NETWORK_UNAVAILABLE`；三者不得都归入 `PROCESS_INTERRUPTED`。

先写入可复制的 Runtime 配置；不要依赖开发机路径：

```toml
# ~/.config/foampilot/runtime.toml
schema_version = 1
[openfoam]
distribution = "foundation"
version = "10"
root = "/opt/OpenFOAM/OpenFOAM-10"
[execution]
isolation = "sandbox_preferred"
bubblewrap = "auto"
max_mpi_ranks = 4
allow_dynamic_code_on_host = false
trusted_readonly_roots = []
```

也可用 `FOAMPILOT_OPENFOAM_ROOT`、`FOAMPILOT_EXECUTION_ISOLATION` 等环境变量或共享 CLI
flags 覆盖。验证命令为：

```bash
foampilot preflight \
  --openfoam-root /opt/OpenFOAM/OpenFOAM-10 \
  --execution-isolation sandbox_preferred \
  --json
```

`sandbox_preferred` 只允许 low-risk case 在 bwrap/namespace 机制不可用时回退；
`sandbox_required` 完全禁止 host；`trusted_host` 明确接受宿主权限执行。audited host 与
bubblewrap 不具有相同安全性，前者没有 network/filesystem namespace。检查
`runtime-config.json`、`execution-risk-report.json` 和 `execution-policy.json` 可确认请求策略、
case 风险和实际 backend。qualification 只能使用 `sandbox_required`。

遇到 `OPENFOAM_DISCOVERY_FAILED` 时设置明确的 v10 root；遇到
`SANDBOX_REQUIRED_UNAVAILABLE` 时修复 bwrap/user namespace；遇到
`HOST_DYNAMIC_CODE_BLOCKED` 时恢复 sandbox，不要把高风险 case 静默放到 host。

## 校验、生成计划、求解和报告

### 从自然语言生成 TaskSpec

先把物理意图写入 `request.md`，并以相对路径显式声明几何附件。几何长度单位属于输入权威，
必须由用户文本或显式确认给出；物性、边界数值、初始条件和瞬态控制可以由用户明确指定，也可
交给后续 CaseDesigner 提出受 RiskGate 约束的候选：

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

不带附件时省略 `--asset` 和 `--asset-root`。`draft` 只把公开 request、附件 metadata 和系统
解析出的紧凑输入事实交给模型，不读取目标 tutorial，也不运行 OpenFOAM。完整且有明确证据的
事实可以直接确认；只有输入权威缺口才在这里返回 `TASK_REQUEST_INCOMPLETE`。solver、物性和
时间控制等设计缺口由 solve 阶段的 CaseDesigner/RiskGate 产生规范的
`CONFIRMATION_REQUIRED` 或 `INFORMATION_REQUIRED`。当前 CLI 不包含交互式澄清表单；补充
request 或由上游 Agent/界面记录 `user_confirmation` 后，再校验和编译 draft。输出文件采用
独占创建，不覆盖已有文件。
模型进程、网络或服务暂时不可用时，`draft` 返回退出码 3、稳定 backend code、中文 message 和
recovery，不会把问题记为 OpenFOAM failure。

已有 Foundation OpenFOAM 原生网格必须把完整 `polyMesh` 目录声明为一个原子资产；不要逐个
声明 points、faces、owner、neighbour 或 zone 文件：

```bash
foampilot task draft \
  --request-file request.md \
  --asset-dir mesh/openfoam/constant/polyMesh \
  --asset-install-path constant/polyMesh \
  --asset-root . \
  --output task-draft.yaml \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

在首次 TaskDraft 模型调用前，`PolyMeshInspector` 已验证必需成员和可选 zones，并产生不声明
长度单位的 `PolyMeshTopologyFacts`。模型只看到结构化点/面/单元数量、未缩放 bounds、patch
和 zone 事实，不能读取或覆盖原始网格成员。提示词没有长度单位时，TaskDraft 会只询问这一项，
而不会再要求用户重复提供 patch/zone、solver、物性或时间控制。单位确认后，编译的 TaskSpec
使用 `geometry.mode: openfoam_mesh` 和 `mesh.strategy: provided`；solve 阶段原子化安装目录，
再保存 `asset-bundles.json`、单位感知的 `input-mesh-facts.json` 和受控 `checkMesh` 生成的
`pre-authoring-mesh-facts.json`。完整手写示例见 `examples/tasks/provided-poly-mesh.yaml`，求解
时需传入 `--public-asset-root`：

```bash
foampilot solve examples/tasks/provided-poly-mesh.yaml \
  --public-asset-root . \
  --run-root /tmp/foampilot-native-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

如果 solve 在 authoring 前返回 `CONFIRMATION_REQUIRED` 或 `INFORMATION_REQUIRED`，它是求解前
设计状态，不是 CFD 求解失败。先查看具体问题：

```bash
foampilot questions /tmp/foampilot-native-runs/PARENT_RUN --json
```

只有 confirmable 问题可按 `questions.json` 的 exact candidate 确认；information-required
问题必须补充新的权威事实，不能绕过。答案文件示例：

```yaml
schema_version: 1
answers:
  - question_id: confirm-materials-fluid-nu
    candidate_id: water-like-nu
    confirmed_value: {value: 1.0e-6, unit: m2/s}
```

```bash
foampilot confirm /tmp/foampilot-native-runs/PARENT_RUN \
  --answers answers.yaml \
  --run-root /tmp/foampilot-native-runs \
  --json
```

系统逐字段验证 candidate ID/value、parent manifest 和 proposal hash，随后创建含任务、公开
资产快照、冻结设计、确认记录、summary 和 lineage 的不可变 child；确认命令本身不启动
OpenFOAM。使用该 child 继续 authoring：

```bash
foampilot resume /tmp/foampilot-native-runs/CONFIRMATION_CHILD \
  --run-root /tmp/foampilot-native-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

该续跑会重新检查环境、资产和扩展身份，但复用冻结 CaseDesign，不重新调用 Intent Interpreter
或 CaseDesigner。随后由 CaseVerifier 与 PlanCompiler 形成带设计 hash 和 compiler identities
的 ExecutionPlan v4。

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

`plan` 和 `solve` 使用完全相同的 intent/design/author/verifier/compiler 链；两者都会针对整个
CaseBundle 发起一次逻辑 author 请求。`plan` 在 materialize 和 Runner 前以 `PLAN_READY` 正常
结束并输出 ExecutionPlan v4。一次逻辑请求内部可以包含次数受限的传输重试。

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
拥有有效 artifact manifest、一致的 CaseDesign/CaseBundle/conformance/compiler identities/
ExecutionPlan v4 authority chain，以及 manifested `blockMesh`/`checkMesh`/目标 solver 证据；
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

创建 child run 前，`resume` 会按 checkpoint 类型校验 parent manifest、冻结设计或兼容性指纹、
可重试 blocker、continuation 数量、传输尝试预算以及当前 OpenFOAM 能力。strict compatibility 或
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
asset-bundles.json                # 公开资产的原子 manifest
input-mesh-facts.json             # provided polyMesh 的生成前静态权威事实
pre-authoring-mesh-facts.json     # provided polyMesh 的受控 checkMesh 事实
simulation-intent.json            # 独立意图解释
resolved-requirements.json        # 确定性完整性/冲突/能力解析
case-design-proposal.json         # 不含原生文件和命令的设计提议
risk-decision.json                # 程序所有的四态门禁
questions.json                    # 需要补充或逐字段确认时存在
case-design.json                  # 仅 READY_TO_AUTHOR 后存在
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
