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
公开 TaskSpec
-> 基于证据的 CapabilityProfile
-> 按槽位限制的公开知识与路由 Skills
-> 模型一次编写完整 ExecutionPlan v3
-> 安全 MPI 规范化、typed policy 与语义检查
-> bubblewrap 或 audited host 原生 OpenFOAM 执行
-> evaluator 负责的检查
-> 至多一次由证据限定范围的 repair
-> 可重试 provider 中断后的严格 child continuation
-> 不可变 artifact 与 SHA256 manifest
```

Agent 从空 case 目录开始工作。它可以使用公开 OpenFOAM 文档与通用知识，但不能读取
当前目标 tutorial、evaluator rule 或派生 reference value。

## 运行要求

- Python 3.12 或更高版本；
- Foundation OpenFOAM v10；
- bubblewrap（`bwrap`，推荐；不可用时 `auto` 后端可降级）；
- NumPy、Pydantic、PyYAML 与 PyVista；
- `requests`，以及默认在线 model provider 所支持的本地 Codex OAuth credential。

当前工作站配置使用：

```text
/home/edwin/workplace/OpenFOAM-10
/home/edwin/feal-venv-py312/bin/python
/usr/local/bin/bwrap
```

这些路径是显式 runtime 配置，并不表示 FoamPilot 依赖其最初拆分来源的代码仓库。

## 安装

```bash
python -m pip install -e ".[codex,test]"
foampilot preflight --json
```

默认工作站配置使用 `execution_backend=auto`：先做一次有界 bubblewrap 探测并缓存结果；
namespace 可用时使用无网络 bubblewrap，不可用时选择有 executable allowlist、资源限制和
完整日志的 audited host 后端。host 后端不提供 network namespace 隔离，`preflight` 会明确
报告所选后端与 fallback 原因，而不会因 bubblewrap 权限不足无限等待。

## 求解任务

校验公开 TaskSpec：

```bash
foampilot validate examples/tasks/non-tutorial-side-driven-box.yaml --json
```

运行完整 Agent 闭环：

```bash
foampilot solve \
  examples/tasks/non-tutorial-side-driven-box.yaml \
  --run-root /tmp/foampilot-runs \
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
  --model-name gpt-5.6-sol \
  --json
```

默认认证路径是 `~/.codex/auth.json`。任务可以允许串行或有界 MPI 执行。模型声明
`mpi_ranks`；MPI launcher 由 Runner 而不是模型负责。

## 公开知识与 Skills

Knowledge 与 Skills 属于 package data，从已安装 wheel 中仍可使用：

```bash
foampilot knowledge validate src/foampilot/knowledge/openfoam10 --json
foampilot knowledge search src/foampilot/knowledge/openfoam10 \
  "incompressible immiscible free surface" --formal --limit 8 --json

foampilot skill validate \
  src/foampilot/skills/openfoam-author-native-case --json
```

工具包包含一个通用原生算例编写 Skill，以及 benchmark、buoyant-flow 与
`rhoCentralFoam` solver-family Skills。它们是公开行为指导，不是确定性 case template。

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
  --model-name gpt-5.6-sol \
  --json
```

该 suite 由 15 个严格物理 qualification 算例和 15 个公开验证级广度算例组成。2026-08-03
冻结基线实现 30/30 generation、28/30 目标 solver 启动、20/30 solver 正常结束、18/30
公开验证通过和 17/30 suite `PASS`；provider/environment terminal blocker 均为 0。后 15 题
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
- `PLAN_INVALID`;
- `STATIC_INSPECTION_FAILED`;
- `SOLVER_FAILED`;
- `PUBLIC_VALIDATION_FAILED`;
- `PUBLIC_VALIDATION_PASS`.

RunSummary v2 还报告 workflow state（`COMPLETED`、`FAILED` 或 `DEFERRED`）、
可选 native status、primary failure 与 terminal blocker。因此，repair 阶段 provider
中断时可以保留 `SOLVER_FAILED`，并独立报告可重试 provider blocker。

`PUBLIC_VALIDATION_PASS` 只覆盖公开任务声明的检查。独立 qualification 层仍可能根据
物理指标拒绝已完成求解，报告必须保留这一区别。

## 开发验证

```bash
PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests
python -m pip wheel . --no-deps --wheel-dir dist
```

真实 OpenFOAM 测试需要宿主机 runtime，并有意与确定性单元测试分离。

## 文档

- [架构、运行流程与功能边界](docs/system-overview.md)
- [架构说明](docs/architecture.md)
- [快速开始](docs/independent-agent-quickstart.md)
- [Agent 集成](docs/agent-integration.md)
- [知识治理](docs/knowledge-governance.md)
- [受控评测](docs/qualification.md)
- [15 题受控学习报告](docs/reports/2026-07-30-controlled-learning-15.md)
- [30 题官方题库衍生基线与受控学习报告](docs/reports/2026-08-04-official-corpus-30-baseline.md)
- [阶段 A provider/workflow 验收](docs/reports/2026-07-31-stage-a-acceptance.md)
- [阶段 B routing/semantic 验收](docs/reports/2026-07-31-stage-b-acceptance.md)
- [交付就绪报告](docs/reports/2026-07-30-delivery-readiness.md)
- [独立真实算例 gate](docs/reports/2026-07-29-standalone-real-gate.md)
- [许可证](LICENSE)
- [来源与声明](NOTICE.md)

FoamPilot 与 OpenFOAM Foundation 不存在从属关系，也未获得其背书。
