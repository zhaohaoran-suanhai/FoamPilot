# AGENTS.md

本文是 Agent 在 FoamPilot 仓库工作的权威入口。操作步骤以本文件为准，数据契约以源码和
测试为准。

## 项目边界

FoamPilot 是面向 Foundation OpenFOAM v10 的独立 Agent 工具包。规范入口是
`foampilot` Python 包、CLI 和 `NativeAgent.solve()`。不要引入 Foam-Agent、LangGraph、
FAISS、MCP、Case renderer、`Allrun` 或第二套兼容状态机。

OpenFOAM 负责网格 utility 和数值求解。FoamPilot 负责 TaskSpec 校验、能力路由、公开
上下文、模型编写 case、typed command、安全执行、公开验证、有限 repair、qualification
和不可变证据。

## 规范命令

```bash
foampilot preflight --json
foampilot model doctor --json
foampilot validate TASK.yaml --json
foampilot plan TASK.yaml --output PLAN.json --backend auto --json
foampilot solve TASK.yaml --run-root RUNS --backend auto --json
foampilot resume RUNS/PARENT_RUN --run-root RUNS --backend auto --json
foampilot report RUN_DIR --json

foampilot qualify suite \
  --suite-file src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root RUNS/controlled-learning-15 --workers 2 \
  --backend codex-cli --model-name gpt-5.6-sol --json
```

源码树运行使用 `PYTHONPATH=src`，或安装到选定的 Python 3.12 环境。真实求解前必须分别
检查 OpenFOAM runtime 和模型后端；不要把二者的失败混为一类。

## TaskSpec 与 case 编写

- 将用户需求转换为保留几何、物理、工况、资源、输出和公开验收条件的最小 TaskSpec。
- 默认从空 case 开始；只有 TaskSpec 显式声明并通过校验的 public asset 可以进入工作目录。
- 使用 Foundation OpenFOAM v10 的文件名、量纲、边界条件、字典语法和 executable。
- 先根据任务事实与本机 executable 路由 solver family，再动态检索公开知识；TaskSpec
  不得预选 knowledge ID。
- `CapabilityProfile.confidence` 由确定性证据计算，不能接受模型自报 confidence。
- 模型返回完整 `ExecutionPlan` schema v3：region-aware `CaseManifest`、全部 case 文件和
  每条带 `stage` 的 typed command。
- 禁止读取或复制当前目标 tutorial，也不能向生成或 repair 模型暴露 evaluator rule、
  reference、golden value、受保护路径或目标 case 映射。
- 命令只能包含 executable 和 args。禁止 shell、重定向、命令替换、`Allrun`、`mpirun`、
  `orterun`、host file 和外部绝对路径；MPI launcher 由 Runner 构造。
- 不要添加只为制造评测证据的可选 function object。Evaluator 应优先读取 solver log 和
  写出场。

## 模型后端

- 默认 `codex-cli` 后端通过固定 argv 调用公开 `codex exec`；FoamPilot 不实现登录协议，
  也不读取其他工具的认证文件。
- OpenAI-compatible 配置只保存 endpoint、model、priority 和凭据环境变量名称，禁止保存
  key、token、password 或其他秘密值。
- 普通 solve 可以按注册优先级自动 failover；qualification 必须固定一个 backend/model，
  禁止自动切换实验条件。
- 所有失败保留稳定英文 `code`，并提供中文 `message` 与 `recovery`。
- trace 不保存 prompt、响应正文、HTTP header、凭据或环境变量值。
- 后端故障、OpenFOAM 环境故障、case 编写错误、solver 失败和 qualification 失败必须分别
  归因。

## 执行、repair 与恢复

- 真实求解先运行 `preflight`。bubblewrap 不可用时可以采用有明确记录的 audited-host
  fallback，不能等待交互权限对话。
- `author` 和 `public_asset` 字段必须在执行前存在；由 mesh、initialize 或 solver 创建的
  字段不得被错误地提前要求存在。
- 每个 attempt 独立保存，不得原地修改已固化目录。
- repair 只能使用公开失败报告、失败命令日志、当前 plan/file、动态知识和公开 Skill，并且
  只做任务预算内、与证据直接相关的最小修改。
- 可重试的 generation/repair 中断只能通过 child continuation 恢复。恢复前验证 parent
  manifest、兼容性指纹和 lineage budget。
- 代码、TaskSpec、资产、模型、backend policy、Knowledge 或 Skill 变化属于
  `rerun_with_changes`，不能伪装成 strict resume。
- OpenFOAM 返回 0 只说明进程正常结束，不证明收敛、守恒、网格无关或工程可用。
- 报告结论前验证 artifact manifest。

## Qualification 与离线改进

- qualification 复用同一条 `NativeAgent.solve()` 路径，官方目标和 reference 只允许
  evaluator 访问。
- `PUBLIC_VALIDATION_PASS` 与 qualification `PASS` 是不同证据层，不能互相替代。
- `foampilot improve analyze` 和 `foampilot improve compare` 只在冻结证据上离线运行，
  不会自动 promotion。
- 官方 example 只能作为事后 teacher reference，用于提取通用原则；不得复制完整 case、
  目标几何、patch 参数、golden value、tolerance 或官方路径到 Knowledge、Skills 或 prompt。

## 验证

完成代码修改前运行：

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

package data 变化时还要构建并检查 wheel；runtime 变化时运行 `preflight`、`model doctor`
和最小真实 OpenFOAM gate。报告时明确区分 deterministic tests、solver completion、public
validation 与 qualification。

## 仓库与来源安全

- 保留用户的无关改动；生成和删除操作使用经过核验的显式路径。
- 未经用户明确要求，不要 commit、push、创建 remote 或修改 remote。
- 不要提交 `.foampilot/`、求解时间目录、缓存、凭据、官方 tutorial 副本或临时对比仓库。
- 不修改 `LICENSE` 权属文字，除非项目所有者明确提供有权决定。
- 来源分类、合成资产和发布审计见 [PROVENANCE.md](PROVENANCE.md) 与
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
