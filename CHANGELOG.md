# Changelog

FoamPilot 的重要变更记录在此文件中。版本号遵循 Semantic Versioning；在 `1.0.0` 之前，
次版本可能调整仍在稳定过程中的接口。

## [Unreleased]

### Added

- 增加仅由已确认 `GeometryInput.region_roles` 激活的 Foundation v10 `pisoFoam` 多孔介质扩展，
  对 `explicitPorositySource`、`DarcyForchheimer`、cellZone、`d`/`f`、材料黏度和完整 patch
  manifest 执行确定性设计一致性检查。
- confirmation child 现在自包含 TaskSpec、公开资产、冻结设计、确认记录、summary、lineage 和
  模型使用量；`foampilot resume` 可从该 checkpoint 的 authoring 阶段继续且不重跑 intent/design。
- 增加 contract-first 求解链：provided polyMesh 权威事实、分阶段 intent/design/authoring、
  求解前 `AcceptancePlan`/`ObservationPlan`、单一 `RunFacts` 证据源以及确定性的
  `DerivedMetrics`/`ResultReport`。
- 增加 Foundation v10 流量、运动压差和区域平均量的系统拥有采集字典与 typed post-process
  命令；增加 `foampilot results` 及 Desktop 结果/验收视图。
- 增加跨 provided/generated mesh、region、稳态/瞬态、可压缩、传热、多相、failure/repair、
  cancellation/resume 的当前-schema replay matrix。
- 增加统一核心活动事件、模型/OpenFOAM 心跳、日志增长和增量残差事件。
- 增加工程内持久化本机 job、detached worker、进程身份校验、心跳、取消请求和 Desktop 重连。
- 增加确定性 job reconcile、身份安全的孤儿进程终止、`INTERRUPTED` 中立固化，以及
  `job reconcile`/`job recover-finalize` 命令。
- 增加跨 job 的严格模型阶段 resume、完整 `rerun` 与不可变 `lineage.json`；Desktop 只按
  恢复证据启用对应操作，并明确不支持通用 OpenFOAM 时间步 continuation。

### Changed

- 统一产品介绍和架构定义：FoamPilot 当前是面向 Foundation OpenFOAM 10 的大模型增强 CFD
  Workflow，而不是开放自主的 AI Agent；同时记录其最初的 Agent 目标及开发过程中向预定义
  状态机、确定性门禁和部分机械场景逻辑偏移的现状。`NativeAgent` 继续作为稳定公开类型名称，
  本次文档校准不改变运行行为或接口。
- Intent 观测请求现在从关闭的第一方注册表获得模型可见的 canonical
  `kind + quantity + dimension` 词表；quantity Schema 描述和 system prompt 明确要求机器标识符，
  仅对注册且语义唯一的 `Q`/`U`/`p` 与符号量纲别名做可审计归一化。未知值继续 fail closed，
  ObservationPlanner 对所有观测类型（包括 residual/continuity 的 `run_facts`）只接受正规化后的
  canonical 契约，通用 ModelGateway 不包含 CFD 词表。
- Intent uncertainty 改为 candidate-free 的 `design_required`/`information_required`/`conflict`；
  设计器负责产生工程候选，RiskGate 负责逐字段确认。原生冷路径模型预算集中定义，并保证允许
  两次传输的阶段确实容纳一次 schema correction。
- Foundation v10 多孔扩展将模型常见的聚合黏度、入口向量、各向同性阻力向量和结构化
  DarcyForchheimer 描述投影到唯一细粒度契约；缺少精确最小单元长度、cellZone 几何包络或
  不可唯一构造的内部采样面只记录为设计/报告限制，不再误阻断安全 authoring。
- 新运行不再生成 `public-validation.json`；原生日志只由 evidence 层解析一次，观测值与显式
  条件分别由 post-processing 和 acceptance 层处理。历史 public-validation 产物保持只读兼容。
- 模型返回的 acceptance 阈值只有与 TaskSpec 中同一条显式声明及数值匹配时才保留用户权威；
  其他阈值一律降级为未确认建议，不得影响 verdict。观测时间范围、验收 all/range 语义、
  多区域绑定、solver/field 量纲以及 signed/magnitude reduction 均改为显式契约。
- force/heat-flux 目前只保留类型化指标接口；由于通用采集所需的密度、相、壁面和物理语义尚未
  完整建模，ObservationPlanner 会明确返回 `UNAVAILABLE`，而不是生成不完整字典。
- Desktop 长任务不再由窗口进程持有；关闭窗口后继续运行，重新打开工程可恢复观察并显式取消。
- 活跃 run 改为增量读取 workflow/log，节流文件扫描、缓存 manifest 验证，并在后台线程构建
  projection。
- artifact manifest 改为原子、排他写入；被中断的 recover-finalize 可幂等完成，不会把中断
  错报为 solver failure 或成功。
- strict resume 现在强制执行跨 lineage 的累计 OpenFOAM wall budget，并把 runtime/isolation、
  OpenFOAM executable identity 与 bubblewrap identity 纳入兼容性指纹。
- job 状态持久化改为控制面关键写入；Desktop 孤儿终止、终态轮询和无 partial run 恢复动作按
  最终 reconcile 证据收紧。

### Verification boundary

- 本开发工作站已完成 Foundation OpenFOAM 10 的 generated mesh 与 provided polyMesh 实机门禁，
  后者验证了流量、运动压差、cellZone 平均速度、残差和连续性证据。
- 第二台干净 Ubuntu + Foundation v10 的跨机安装、自然语言求解与 Desktop 实机门禁仍为
  `NOT_RUN`；公开/私有 evaluator、Knowledge 和 Skills 的物理分包仍按既定决定暂缓。

## [0.2.0] - 2026-08-11

### Added

- 增加 PySide6 Desktop IDE，可在同一界面完成自然语言任务提取、TaskDraft 审阅、TaskSpec
  编译和规范 `foampilot solve`。
- 增加求解期间的 workflow、公开 Knowledge/Skill 引用、OpenFOAM 日志和残差曲线实时展示，
  并保留完成后的 case、报告和 artifact manifest 检查能力。
- 增加可序列化 Runtime 配置，以及 CLI、TOML、环境变量和有限自动发现的统一解析路径。
- 增加 `sandbox_required`、`sandbox_preferred` 和 `trusted_host` 三档执行策略，以及每次 run
  的 runtime、probe、风险扫描和 policy 决策证据。

### Changed

- 移除固定用户目录、固定 OpenFOAM 根目录和固定 Python executable 假设。
- OpenFOAM 命令、MPI launcher 和 Runner 上下文改为基于已发现规范路径的 typed execution。
- package version 改为从 `foampilot.__version__` 单点读取，避免源码版本与 wheel 元数据漂移。

### Security

- host fallback 在执行前扫描动态代码、`#calc`、`systemCall`、动态库、外部 include、变量展开、
  `timeActivatedFileUpdate` 和 Runner 上下文改写，并对未知风险 fail closed。
- `.foampilot` 保留为 Runner 内部命名空间，TaskSpec、公开资产和模型生成文件不得写入。
- `sandbox_required` 不允许静默退化为 host；qualification 固定要求 sandbox。

### Verification boundary

- 本版本在开发工作站上完成自动测试、Desktop offscreen 测试、wheel/sdist 构建、全新虚拟环境
  wheel 安装冒烟检查，以及受信任 host 的 Foundation OpenFOAM v10 最小真实门禁。
- 当前 Codex 执行环境无法通过 bubblewrap namespace probe，因此没有将该环境中的
  `sandbox_required` 失败描述为 sandbox 实机通过。
- 尚无第二台干净 Ubuntu + Foundation OpenFOAM v10 机器，跨机安装、preflight、自然语言求解
  和 Desktop 实机门禁均为未验证；本版本不是跨机器产品交付证明。
- 当前 wheel 仍包含仓库中现有 Knowledge、Skills 和 qualification/evaluator 数据。本版本只作为
  内部可追溯基线；在公开分发前，必须先完成资产分类和公开/私有包边界拆分。

[0.2.0]: https://github.com/zhaohaoran-suanhai/FoamPilot/releases/tag/v0.2.0
