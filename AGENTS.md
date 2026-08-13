# AGENTS.md

本文是 Agent 在 FoamPilot 仓库工作的权威入口。操作步骤以本文件为准，数据契约以源码和
测试为准。

## 项目边界

FoamPilot 是面向 Foundation OpenFOAM v10 的独立 Agent 工具包。规范入口是
`foampilot` Python 包、CLI 和 `NativeAgent.solve()`。不要引入 Foam-Agent、LangGraph、
FAISS、MCP、Case renderer、`Allrun` 或第二套兼容状态机。

OpenFOAM 负责网格 utility 和数值求解。FoamPilot 负责 TaskSpec 校验、能力路由、公开
上下文、分阶段模型推理、确定性 typed command 编译、安全执行、单一证据提取、显式验收、
有限 repair、qualification 和不可变证据。可选 TaskBuilder 可以在 run 创建前把完整自然语言请求编译为相同 TaskSpec；
它不得直接运行 OpenFOAM。

## 规范命令

```bash
foampilot preflight --json
foampilot model doctor --json
foampilot task draft --request-file REQUEST.md --output DRAFT.yaml --json
foampilot task validate-draft DRAFT.yaml --json
foampilot task compile DRAFT.yaml --output TASK.yaml --json
foampilot validate TASK.yaml --json
foampilot plan TASK.yaml --output PLAN.json --backend auto --json
foampilot solve TASK.yaml --run-root RUNS --backend auto --json
foampilot solve TASK.yaml --run-root RUNS \
  --reuse-verified-plan RUNS/SOURCE_RUN --derived-cache CACHE_ROOT --json
foampilot resume RUNS/PARENT_RUN --run-root RUNS --backend auto --json
foampilot report RUN_DIR --json
foampilot results RUN_DIR --json
foampilot questions RUN_DIR --json
foampilot confirm RUN_DIR --answers ANSWERS.yaml --run-root RUNS --json

foampilot qualify suite \
  --suite-file src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root RUNS/controlled-learning-15 --workers 2 \
  --backend codex-cli --model-name gpt-5.6-sol --json
```

源码树运行使用 `PYTHONPATH=src`，或安装到选定的 Python 3.12 环境。真实求解前必须分别
检查 OpenFOAM runtime 和模型后端；不要把二者的失败混为一类。

## TaskSpec 与 case 编写

- 新的 authoring 输入只接受 `TaskSpec v3`。`TaskSpec v2` 仅用于历史 run 的只读报告，禁止
  重新进入 authoring、resume 或 qualification。
- 完整自然语言请求可以经过 `TaskDraft -> DraftReview -> TaskCompiler`，但高影响模型推断不能
  冒充 `user_confirmation`。TaskBuilder 只阻断必须由用户或资产提供的输入权威缺口，例如未知
  几何长度单位、未声明资产或维度冲突；solver、物性候选、边界数值、时间控制和工程容差交给
  后续 CaseDesigner/RiskGate，不得在 TaskBuilder 中重复追问或猜测。
- 将用户需求转换为保留几何、物理、工况、资源、输出和公开验收条件的最小 TaskSpec；
  qualification 继续直接使用冻结 TaskSpec。
- 默认从空 case 开始；只有 TaskSpec 显式声明并通过校验的 public asset 可以进入工作目录。
- geometry/mesh 任务必须在路由前生成 GeometryFacts；STL/OBJ 单位、patch/region role 和
  工程网格阈值只能来自明确任务事实，不能由模型或 probe 猜测。
- 使用 Foundation OpenFOAM v10 的文件名、量纲、边界条件、字典语法和 executable。
- 先根据任务事实与本机 executable 路由 solver family，再动态检索公开知识；TaskSpec
  不得预选 knowledge ID。
- `CapabilityProfile.confidence` 由确定性证据计算，不能接受模型自报 confidence。
- 正常 live solve 必须依次形成 `SimulationIntent -> ResolvedRequirements ->
  CaseDesignProposal -> RiskDecision`。只有 `READY_TO_AUTHOR` 才能冻结 `CaseDesign` 并进入
  case 编写；`INFORMATION_REQUIRED`、`CONFIRMATION_REQUIRED` 和
  `CAPABILITY_UNAVAILABLE` 都是设计阶段终态，不是 CFD 求解失败。
- `CONCRETE_CONFIRMATION_REQUIRED` 必须绑定问题、字段、候选 ID 和完整候选值。禁止
  accept-all、continue-anyway 或高影响风险 override；模型不能自报 confidence 取得放行。
- `foampilot confirm` 只能从 manifest 有效的 parent 创建不可变 child 并逐项记录
  `ConfirmationRecord`。确认命令本身不启动 OpenFOAM。
- Case Author 只接受冻结的 `CaseDesign`、权威网格事实和有界公开上下文，只返回包含
  region-aware `CaseManifest` 与全部 case 文件的 `CaseBundle`；禁止返回或修改命令。
- `CaseVerifier` 必须先证明 CaseBundle 与冻结设计一致；随后系统 `PlanCompiler` 根据已冻结的
  第一方扩展身份确定性生成 `ExecutionPlan v4` typed commands。模型没有执行权限。
- `foampilot plan` 走同一条设计、编写、验证和编译路径，但在 materialize/Runner 前以
  `PLAN_READY` 正常结束。
- 禁止读取或复制当前目标 tutorial，也不能向生成或 repair 模型暴露 evaluator rule、
  reference、golden value、受保护路径或目标 case 映射。
- 命令只能包含 executable 和 args。禁止 shell、重定向、命令替换、`Allrun`、`mpirun`、
  `orterun`、host file 和外部绝对路径；MPI launcher 由 Runner 构造。
- 不要添加只为制造评测证据的可选 function object。ObservationPlanner 应优先复用
  `RunFacts` 和写出场，只按已确认观测要求添加系统拥有的采集配置。
- quantity/dimension、signed/magnitude、time selection 与 multi-region binding 必须冻结在
  观测契约中；field-backed 指标在求值前要核对实际 OpenFOAM field header dimensions。
- 模型不能自证 acceptance 的用户权威；阈值必须与 TaskSpec 的显式 acceptance statement
  逐条且数值一致，未通过 authority audit 的条件只能进入 uncompiled。
- OpenFOAM 日志只由 evidence 层解析一次；PostProcessor 只消费 `RunFacts` 和已声明的结构化
  观测输出，AcceptanceEvaluator 只计算显式且已确认的条件。

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

- 真实求解先运行 `preflight`。Runtime 配置来自统一 CLI/TOML/环境变量/有限发现 resolver；
  禁止恢复固定用户路径或固定 Python executable。
- `sandbox_required` 禁止 host；`sandbox_preferred` 只允许 low-risk case 因 bwrap/namespace
  机制不可用而在首命令前降级；`trusted_host` 必须显式选择。audited host 与 bubblewrap
  不具有相同安全性，qualification 必须使用 `sandbox_required`。
- 每个 run/attempt 保留 `runtime-config.json`、`execution-risk-report.json`、
  `execution-policy.json` 和 sandbox probe；repair 后必须重扫，运行中不得切换 backend。
- `author` 和 `public_asset` 字段必须在执行前存在；由 mesh、initialize 或 solver 创建的
  字段不得被错误地提前要求存在。
- 每个 attempt 独立保存，不得原地修改已固化目录。
- repair 只能使用公开失败报告、失败命令日志、当前设计/bundle、动态知识和公开 Skill，并且
  只做任务预算内、与证据直接相关的最小修改。repair 模型返回不含命令的
  `RepairProposal`；变更必须落在冻结的 `NumericalRepairEnvelope` 字段、方向、范围、文件和
  dictionary keyword 内，再重新通过 CaseVerifier 与 PlanCompiler。
- `repair_policy.automatic_numerical_repair` 默认开启但可关闭。自动 repair 只允许已分类的数值
  不稳定；物理、能力、求解器、网格和 envelope 外变更不得泛化放行，必须失败并给出重新确认或
  rerun 的具体原因。
- 可重试的 generation/repair 中断只能通过 child continuation 恢复。恢复前验证 parent
  manifest、兼容性指纹和 lineage budget。
- 代码、TaskSpec、资产、模型、backend policy、Knowledge 或 Skill 变化属于
  `rerun_with_changes`，不能伪装成 strict resume。
- OpenFOAM 返回 0 只说明进程正常结束，不证明收敛、守恒、网格无关或工程可用。
- 报告结论前验证 artifact manifest。
- `WorkflowCoordinator` 只能推进状态、持久化 checkpoint 和处理取消；不得自行解析领域日志。
- OpenFOAM 文本只由 evidence 层解析一次并冻结为 `RunFacts`；CLI 与 Desktop 必须消费同一个
  `WorkflowProjection`，实时残差从 `metrics.jsonl` 读取，不得恢复独立日志解析器。
- 失败报告使用 `FailureReport`，严格区分直接观察、确认原因和 hypothesis；模型诊断不能成为
  terminal authority。
- 计划复用只接受完全相同的规范 TaskSpec 和严格兼容 source；拒绝不得静默回退模型生成。
- 派生缓存必须使用内容寻址依赖键，命中仍重新运行当前 `checkMesh`，不得把缓存结果当作物理验证。
- repair 阶段复用只能跳过由修改集合证明不受影响的前序命令；依赖不明确时完整重跑。
- qualification 默认禁用计划和派生缓存；warm-path 性能不能计入盲编写准确率。

## Qualification 与离线改进

- qualification 复用同一条 `NativeAgent.solve()` 路径，官方目标和 reference 只允许
  evaluator 访问。
- 当前 `RUN_COMPLETED`/`ResultReport.PASS` 与 qualification `PASS` 是不同证据层，不能互相
  替代；`PUBLIC_VALIDATION_PASS` 只用于历史产物兼容。
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
