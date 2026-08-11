# FoamPilot

FoamPilot 将公开 CFD 需求转换为原生 Foundation OpenFOAM v10 算例，优先在无网络沙箱中
执行求解，评估写出结果，并允许一次由证据限定范围的 repair。它是可独立安装的 Python
工具包和 CLI，不依赖 Foam-Agent、LangGraph、FAISS、MCP 或预先存在的 tutorial case。

FoamPilot 是围绕 OpenFOAM 构建的 Agent 工作流，不是 CFD 求解器。OpenFOAM 提供网格
utility 与数值求解器；FoamPilot 负责编写、编排、检查并记录算例。

## 能力边界

当前经过验证的运行目标是 Foundation OpenFOAM v10。ESI OpenFOAM 发行版及其他
Foundation 版本尚未完成 qualification。模型编写算例具有非确定性，因此一次成功 run
只能作为该次运行的证据，不能保证以后每个 prompt 都成功。

规范工作流如下：

```text
自然语言 + 显式公开附件（可选）
-> 带来源的 TaskDraft、确定性 review 与 TaskSpec 编译
公开 TaskSpec（也可直接提供）
-> 基于证据的 CapabilityProfile
-> 按槽位限制的公开知识与路由 Skills
-> 模型一次编写完整 ExecutionPlan v3
-> 安全 MPI 规范化、typed policy 与语义检查
-> bubblewrap 或 audited host 原生 OpenFOAM 执行
-> evaluator 负责的检查
-> 至多一次由证据限定范围的 repair
-> 可重试模型后端中断后的严格 child continuation
-> 不可变 artifact 与 SHA256 manifest
```

Agent 从空 case 目录开始工作。它可以使用公开 OpenFOAM 文档与通用知识，但不能读取
当前目标 tutorial、evaluator rule 或派生 reference value。

## 运行要求

- Python 3.12 或更高版本；
- Foundation OpenFOAM v10；
- bubblewrap（`bwrap`，推荐）；
- NumPy、Pydantic、PyYAML 与 PyVista；
- 已登录的 Codex CLI，或由无秘密 YAML 声明的 OpenAI-compatible 模型后端。

FoamPilot 不内置用户目录、Python 虚拟环境或 OpenFOAM 安装路径。运行时按
CLI > 环境变量 > 显式 TOML > `FOAMPILOT_RUNTIME_CONFIG` >
`$XDG_CONFIG_HOME/foampilot/runtime.toml` > 有限自动发现 > 默认值逐字段解析，
并把来源写入 `runtime-config-provenance.json`。

## 安装

```bash
python -m pip install -e ".[test]"
foampilot preflight \
  --openfoam-root /opt/OpenFOAM/OpenFOAM-10 \
  --execution-isolation sandbox_preferred \
  --json
foampilot model doctor --json
```

可冻结为 `~/.config/foampilot/runtime.toml`（或 `$XDG_CONFIG_HOME` 下同一路径）：

```toml
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
trusted_readonly_roots = ["/opt/site-openfoam-tools"]
```

支持的环境变量为 `FOAMPILOT_RUNTIME_CONFIG`、`FOAMPILOT_OPENFOAM_ROOT`、
`FOAMPILOT_EXECUTION_ISOLATION`、`FOAMPILOT_BUBBLEWRAP`、
`FOAMPILOT_MAX_MPI_RANKS` 和 `FOAMPILOT_ALLOW_DYNAMIC_CODE_ON_HOST`；布尔值只接受
小写 `true`/`false`。共享 CLI flags 为 `--runtime-config`、`--openfoam-root`、
`--execution-isolation`、`--bubblewrap`、`--max-mpi-ranks`、
`--allow-dynamic-code-on-host` 与可重复的 `--trusted-readonly-root`。

三档执行策略是：`sandbox_required` 必须通过完整 bubblewrap launch probe；
`sandbox_preferred` 仅在 namespace/bwrap 机制不可用且 case 风险为 low 时，才在首命令前
选择 host；`trusted_host` 明确选择宿主执行。audited host 与 bubblewrap 不具有相同安全性：
host 没有 network/filesystem namespace，typed argv 和资源限制也不能替代隔离。
qualification 固定要求 `sandbox_required`。

host 决策使用 fail-closed 静态审计：OpenFOAM 命令必须来自已发现的规范绝对路径，模型不能用
`-case`、distributed roots 或绝对 argv 改写 Runner 上下文；动态代码、`#calc`、`systemCall`、
`timeActivatedFileUpdate`、动态库、外部/变量 include、宏展开 type/library 以及无法分类的执行
directive 都会阻断默认 host 路线。`.foampilot` 是 Runner 专用命名空间，不能由 TaskSpec、公开
资产或模型生成文件写入。该审计是 host 降级门禁，不等价于 bubblewrap 隔离证明。

```bash
foampilot solve task.yaml \
  --run-root runs \
  --runtime-config ~/.config/foampilot/runtime.toml \
  --backend auto \
  --json
```

每个 run 固化 `runtime-config.json`、`runtime-config-provenance.json`、`preflight.json`、
`sandbox-probe.json` 与 `execution-policy.json`；每个 attempt 另存
`execution-risk-report.json`、probe 和 policy。`OPENFOAM_DISCOVERY_FAILED` 应通过显式 root
或 TOML 修复；`SANDBOX_REQUIRED_UNAVAILABLE` 需要安装/修复 bwrap 或 user namespace；
`HOST_DYNAMIC_CODE_BLOCKED` 表示高风险或未知 case 禁止 host fallback，应恢复 sandbox，
只有明确选择 `trusted_host` 且显式允许动态代码才可解除该 host 门禁。

## 交互式桌面 IDE

可选的 PySide6 工作台可以从自然语言 TaskDraft 或完整 TaskSpec 启动规范求解，
并在同一界面实时显示 workflow、模型实际收到的公开 Knowledge/Skill 引用、
OpenFOAM 残差、case、日志、公开验证和 artifact manifest：

```bash
python -m pip install -e '.[desktop]'
foampilot desktop
foampilot desktop --open-run /path/to/run-...
```

未安装 desktop extra 时，核心 CLI 与 Python API 不受影响。安装、首次求解、状态解释、
安全边界和 xcb 故障处理见 [Desktop IDE 使用说明](docs/desktop-ide.md)。

## 求解任务

从完整自然语言请求生成规范 TaskSpec：

```bash
foampilot task draft \
  --request-file request.md \
  --output task-draft.yaml \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
foampilot task validate-draft task-draft.yaml --json
foampilot task compile task-draft.yaml --output task.yaml --json
```

TaskBuilder 只提取带来源的事实并使用低风险确定性默认值。缺少单位、物性、边界值、初始条件、
终止时间或工程容差时不会猜测，也不会进入 case generation。当前 CLI 是三个可审计步骤，
尚未提供多轮聊天或交互式澄清表单。

校验公开 TaskSpec：

```bash
foampilot validate examples/tasks/non-tutorial-side-driven-box.yaml --json
```

运行完整 Agent 闭环：

```bash
foampilot solve \
  examples/tasks/non-tutorial-side-driven-box.yaml \
  --run-root /tmp/foampilot-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

验证已冻结结果：

```bash
foampilot report /tmp/foampilot-runs/RUN_DIR --json
```

在不修改 parent 的前提下，续跑可重试的 generation 或 repair 中断：

```bash
foampilot resume /tmp/foampilot-runs/PARENT_RUN \
  --run-root /tmp/foampilot-runs \
  --backend auto \
  --model-name gpt-5.6-sol \
  --json
```

对完全相同的 `TaskSpec`，可以显式复用一个已经通过完整性、网格和 solver 正常结束
检查的 ExecutionPlan；也可以显式启用内容寻址的几何/网格缓存：

```bash
foampilot solve TASK.yaml \
  --reuse-verified-plan /tmp/foampilot-runs/SOURCE_RUN \
  --derived-cache /tmp/foampilot-derived-cache \
  --run-root /tmp/foampilot-runs \
  --json
```

计划复用要求规范 TaskSpec、公开资产字节、OpenFOAM 目标、solver 和资源预算严格兼容；
拒绝时返回 `PLAN_REUSE_REJECTED`，不会暗中退回模型生成。网格命中只跳过网格生成命令，
当前 run 仍重新执行 `checkMesh`、目标 solver 和公开验证。未提供这两个参数时保持冷路径，
qualification 也不启用复用。

默认后端通过公开 `codex exec` 调用已登录的 Codex CLI；FoamPilot 不读取认证文件。
任务可以允许串行或有界 MPI 执行。模型声明 `mpi_ranks`；MPI launcher 由 Runner 而不是
模型负责。

## 公开知识与 Skills

Knowledge 与 Skills 属于 package data，从已安装 wheel 中仍可使用：

```bash
foampilot knowledge validate src/foampilot/knowledge/openfoam10 --json
foampilot knowledge search src/foampilot/knowledge/openfoam10 \
  "incompressible immiscible free surface" --formal --limit 8 --json

foampilot skill validate \
  src/foampilot/skills/openfoam-author-native-case --json
```

工具包包含一个通用原生算例编写 Skill、一个网格工作流 Skill，以及不可压缩压力速度耦合、
可压缩瞬态、VOF、浮力/CHT、固体力学和标量/势场六个物理族 Skill。运行时只装配通用 Skill、
至多一个物理族 Skill，并在任务声明 geometry/mesh 时附加网格 Skill。它们是公开行为指导，
不是确定性 case template。

Routing confidence 由系统负责。明确指定且已安装的 solver 可以高置信度路由；唯一兼容的
公开 solver-family candidate 可以中置信度路由；含糊或物理信息不完整的请求会在生成
case 前停止。模型可以建议 route candidate，但不能自行指定 confidence。

## 受控 qualification

FoamPilot 提供一套由 15 个 Foundation OpenFOAM v10 算例组成的 suite，覆盖
regression、development 与 holdout 角色。每题包含公开 TaskSpec、仅 evaluator 可见的
rule 和紧凑派生数值 reference。仓库不包含官方 tutorial 目录或大型求解结果。

```bash
foampilot qualify suite \
  --suite-file \
    src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root /tmp/foampilot-controlled-learning-15 \
  --workers 2 \
  --backend codex-cli \
  --model-name gpt-5.6-sol \
  --json
```

较小的 `foampilot qualify official-six` 命令继续作为六题 regression wrapper 提供。

最新的 30 题广度基线可以使用：

```bash
foampilot qualify suite \
  --suite-file \
    src/foampilot/qualification/data/suites/official-corpus-30-baseline-v1.yaml \
  --run-root /tmp/foampilot-official-corpus-30 \
  --workers 1 \
  --backend codex-cli \
  --model-name gpt-5.6-sol \
  --json
```

该 suite 由 15 个严格物理 qualification 算例和 15 个公开验证级广度算例组成。2026-08-03
冻结基线实现 30/30 generation、28/30 目标 solver 启动、20/30 solver 正常结束、18/30
公开验证通过和 17/30 suite `PASS`；backend/environment terminal blocker 均为 0。后 15 题
没有 evaluator-only golden 物理比较，因此不能把 17/30 解释成 30 题严格物理通过率。
详见 [30 题基线与受控学习报告](docs/reports/2026-08-04-official-corpus-30-baseline.md)。

2026-07-30 冻结的 15 题基线取得 11/15 严格 qualification 通过。15 题都进入了目标
solver，14 题进入公开验证；一个 CHT 算例在 solver run 中失败。之后针对四项由证据限定
范围的失败进行了修正并通过定向复测，但这些独立 run 不能表述为一次新的随机性 15/15
suite 结果。详见 [qualification 方法](docs/qualification.md) 与
[受控学习报告](docs/reports/2026-07-30-controlled-learning-15.md)。

独立 non-tutorial gate 达到 2/2 `PUBLIC_VALIDATION_PASS`：层流方腔首次 attempt
通过，两相液柱坍塌在一次由证据限定的时间步上限 repair 后通过。该结果验证 installed-wheel
求解路径，不代表 15 题物理 qualification。详见
[独立真实算例 gate 报告](docs/reports/2026-07-29-standalone-real-gate.md)。

## 离线受控改进

FoamPilot 可以把冻结的失败 run 转换为可审查 learning candidate；developer 应用一项
小改动后，再比较 qualification report：

```text
冻结的 solve/qualification
-> foampilot improve analyze
-> developer 应用一项 candidate change
-> 重新运行 qualification
-> foampilot improve compare
-> 显式 promotion decision
```

示例：

```bash
foampilot improve analyze RUN_DIR \
  --qualification-report BASELINE.json \
  --candidate-id of10-solver-family-rule \
  --lesson "General solver-family lesson" \
  --target knowledge \
  --development-case SOURCE_CASE \
  --output IMPROVEMENTS/candidate.yaml

foampilot improve compare BASELINE.json CURRENT.json \
  --candidate IMPROVEMENTS/candidate.yaml \
  --output IMPROVEMENTS/promotion.json \
  --json
```

该工作流完全离线，并且不会自动 promotion。Candidate 与 comparison 文件位于 run root
旁边，绝不写入不可变 run 或 package data。盲编写与 repair 阶段无法访问官方 example。
只有 artifact verification 和冻结 qualification 完成后，developer 才可以将 example
作为 teacher reference；candidate 记录目录 hash、泛化原则与 leakage family，而不是复制算例。

## 失败分层

FoamPilot 会报告：

- `REQUEST_INCOMPLETE`;
- `ROUTING_UNRESOLVED`;
- `BLOCKED_ENVIRONMENT`;
- `CASE_GENERATION_FAILED`;
- `GENERATION_INVALID`;
- `PLAN_INVALID`;
- `STATIC_INSPECTION_FAILED`;
- `SOLVER_FAILED`;
- `PUBLIC_VALIDATION_FAILED`;
- `PUBLIC_VALIDATION_PASS`.

RunSummary v2 还报告 workflow state（`COMPLETED`、`FAILED` 或 `DEFERRED`）、
可选 native status、primary failure 与 terminal blocker。因此，repair 阶段模型后端
中断时可以保留 `SOLVER_FAILED`，并独立报告可重试 backend blocker。

`PUBLIC_VALIDATION_PASS` 只覆盖公开任务声明的检查。独立 qualification 层仍可能根据
物理指标拒绝已完成求解，报告必须保留这一区别。

## 开发验证

```bash
PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests
python -m pip wheel . --no-deps --wheel-dir dist
```

真实 OpenFOAM 测试需要宿主机 runtime，并有意与确定性单元测试分离。

每个终态 run 都包含 `performance-summary.json`。该文件从 workflow、模型 trace 和原生
step 证据重建冷/热路径、首条 OpenFOAM 命令延迟、阶段耗时、模型请求次数和复用状态；
`artifact-manifest.json` 单独记录 manifest 构建耗时。自然语言提取发生在 solve run 之前，
因此 `foampilot task draft` 会在 draft 旁写独立的 `.performance.json`，不把提取时间伪装成
OpenFOAM 求解时间。

## 文档

- [架构、运行流程与功能边界](docs/system-overview.md)
- [架构说明](docs/architecture.md)
- [Desktop IDE 交互式求解工作台](docs/desktop-ide.md)
- [快速开始](docs/independent-agent-quickstart.md)
- [Agent 集成](docs/agent-integration.md)
- [知识治理](docs/knowledge-governance.md)
- [受控评测](docs/qualification.md)
- [15 题受控学习报告](docs/reports/2026-07-30-controlled-learning-15.md)
- [30 题官方题库衍生基线与受控学习报告](docs/reports/2026-08-04-official-corpus-30-baseline.md)
- [Performance v1 实施与验证报告](docs/reports/2026-08-05-performance-v1.md)
- [阶段 A 模型边界/workflow 验收](docs/reports/2026-07-31-stage-a-acceptance.md)
- [阶段 B routing/semantic 验收](docs/reports/2026-07-31-stage-b-acceptance.md)
- [交付就绪报告](docs/reports/2026-07-30-delivery-readiness.md)
- [独立真实算例 gate](docs/reports/2026-07-29-standalone-real-gate.md)
- [下一阶段顺序演进规格](docs/design/README.md)
- [Agent Harness 演进 v2 规格](docs/design/agent-harness-evolution-v2-design.md)
- [许可证](LICENSE)
- [来源与声明](NOTICE.md)
- [内容来源说明](PROVENANCE.md)
- [第三方内容声明](THIRD_PARTY_NOTICES.md)

FoamPilot 与 OpenFOAM Foundation 不存在从属关系，也未获得其背书。
