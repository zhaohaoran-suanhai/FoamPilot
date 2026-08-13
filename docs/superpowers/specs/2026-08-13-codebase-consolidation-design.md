# FoamPilot 代码库减重与新会话交接设计

状态：**已批准、实施并通过最终交付门禁**
日期：2026-08-13

## 1. 目标

本轮只收束已经完成的开发，不扩展 FoamPilot 功能。实施后应满足：

1. 当前 TaskBuilder/polyMesh 行为和公开 CLI 契约保持不变；
2. 大型实现文件按单一职责拆分，后续修改不必一次加载整条输入链；
3. 重复测试数据、模型响应和拓扑构造收敛到显式 factory，同时保留不同风险语义和对应断言；
4. 当前文档与历史材料分层，旧计划和旧结论不再与现行能力竞争权威；
5. 提供一份新对话可以直接使用的短交接文档，准确记录已验证能力、未验证边界和下一步候选；
6. 全程直接在当前 `main` 分支工作，不创建分支或 worktree；完成全量测试、发行物一致性和
   wheel 独立安装验证后才提交。

“减重”以降低认知负担和重复维护为准，不以删除最多代码或减少最多测试数量为准。

本设计从属于 [FoamPilot 现行架构与程序职责规范](../../architecture.md)。该架构规范定义系统
流程、package/文件职责、输入输出、唯一副作用所有者和禁止边界，是所有减重判断的第一权威。
如果某项清理无法证明符合架构规范第 9 节的等价性规则，则该清理不进入本轮。

## 2. 不在本轮范围内

- 不继续运行新的 CFD 求解或 qualification；
- 不新增 Desktop、后处理、远程/HPC 或 repair 功能；
- 不改变 `TaskDraft v2`、`TaskSpec v3`、CLI 参数、退出码或公开错误码；
- 不改变 Foundation OpenFOAM 10 的产品边界；
- 不删除历史发布证据、来源审计或安全回归；
- 不通过放宽校验来缩短实现；
- 不把项目材料写入 `/home/edwin/workplace/learning`。

## 3. 当前问题

### 3.1 TaskBuilder 输入实现过度集中

`src/foampilot/taskbuilder/extraction.py` 同时包含：

- 模型响应 schema 和系统提示词；
- 重复事实归一化；
- 用户文本证据和否定语义核验；
- provided polyMesh 权威事实协调；
- STL/OBJ/GEO 等公开文件几何协调；
- 输入问题重建；
- 模型调用与最终 `TaskDraft` 组装。

这些职责变化原因不同，集中在一个约 1200 行文件中会扩大每次修改的上下文，并提高不相关
回归的概率。

### 3.2 测试保护有效，但表达重复

`tests/test_task_extractor.py` 中多次内联构造相同的模型后端、资产、polyMesh 拓扑和响应对象。
大量行数用于 fixture，而不是新的行为语义。另一方面，以下测试不能因为“看起来相近”而合并
删除：

- 中文、英文否定与互斥值；
- 科学计数法和嵌套字段证据绑定；
- 模型伪造 `user_confirmation`/`public_asset`；
- 文件和目录 symlink 逃逸；
- polyMesh 原子资产、multi-region 和 manifest；
- patch/zone 名称与拓扑一致性；
- `openfoam_mesh + provided` 的交叉契约；
- 未知长度单位正常阻断；
- surface/Gmsh/provided 的合法与冲突组合；
- Desktop 与 CLI 对同一 DraftReview 的一致投影。

### 3.3 文档存在多代口径

主文档、设计、实施计划和完成报告都保存在主阅读路径中。部分旧文档仍把物性、边界数值和
终止时间描述成 TaskBuilder 输入门禁，这与现行的“输入权威和工程设计分阶段”边界冲突。
历史材料仍有审计价值，但不应被新会话误认为当前规格。

## 4. 目标代码结构

TaskBuilder 输入链按以下职责拆分，模块只使用包内接口。文件数量和行数服从职责边界；如果某项
移动会让目标文件同时承担两个变化原因不同的职责，应停止该项移动并报告，而不是为了满足行数
目标继续拆分或合并。

| 模块 | 唯一职责 |
|---|---|
| `taskbuilder/extraction_protocol.py` | 模型响应 schema、允许的 fact path 和系统提示词；不含输入问题策略 |
| `taskbuilder/authority.py` | 重复事实归一化、用户文本证据绑定、来源降级规则 |
| `taskbuilder/provided_mesh.py` | 原生 polyMesh 确定性 geometry/mesh 事实和拓扑角色协调 |
| `taskbuilder/public_geometry.py` | STL/OBJ/GEO 等公开几何文件的确定性资产路线 |
| `taskbuilder/questions.py` | 输入问题路径策略、过滤、规范 ID 和缺失输入问题生成 |
| `taskbuilder/extraction.py` | 调用模型、依次调用上述模块并组装 `TaskDraft` |
| `taskbuilder/projection.py` | validation、compiler、questions 共用的权威事实投影；不重建问题 |

`extraction.py` 不再实现领域核验细节。各模块通过显式函数传递 `TaskFact`、`TaskQuestion`、
`PublicAsset` 和 `TaskIngressContext`，不得通过全局可变状态通信，也不得新增第二套 fact 解释器。

每个文件的职责边界固定如下：

- 只有 `extraction.py` 可以持有 `ModelGateway`、`ModelRequest`、预算、trace 和整条串行调用顺序；
- `extraction_protocol.py` 只定义 transport schema 与 prompt，不读取资产、不判定来源权威；
- `authority.py` 只把模型候选转换为带 provenance 的 `TaskFact`，不协调网格路线、不生成问题；
- `provided_mesh.py` 只消费已经验证的 `PublicAsset` 与 `TaskIngressContext` 拓扑事实，不读取、
  staging 或修改原始 polyMesh；
- `public_geometry.py` 只消费 deterministic ingress 已验证的文件资产 metadata，不自行检查文件；
- `questions.py` 只根据已协调 facts/questions/assets 生成最终输入问题，不调用模型、不读文件、
  不替代 `validate_task_draft()`；
- `projection.py` 只做纯权威投影，不生成问题、不产生 I/O；
- 所有新模块都不得 import runtime、plans、agent、workflow、Desktop、CLI 或 qualification。

`_INPUT_QUESTION_PATHS` 当前在 extraction 与 validation 重复。减重后由 `questions.py` 中唯一的
`INPUT_QUESTION_PATHS` 持有，extraction 和 validation 共同引用；不得把确定性问题策略放进模型
transport protocol。`projection.py` 应提供 facts iterable 到 compilable map 的纯 helper，避免
questions 再实现一套来源筛选。

上述拆分只改变 `taskbuilder` 内部组织，对应架构规范中 `taskbuilder/extraction.py` 的薄编排目标。
TaskDraft、DraftReview、TaskSpec、错误码、来源权威和 CLI/Desktop 投影保持不变。实施计划必须为
每个移动的函数记录“原文件 -> 新文件 -> 架构职责 -> 等价测试”映射。

减重目标为 `extraction.py` 不超过约 300 行，新增职责模块原则上不超过约 400 行。该数值是
认知复杂度警戒线，不是通过机械拆分空壳文件规避的硬性质量指标。

## 5. 测试整理

### 5.1 结构

遵循仓库现有“根目录测试文件 + `tests/support/` 显式帮助代码”结构，不新增嵌套
`tests/taskbuilder/conftest.py`：

- `tests/support/taskbuilder.py`：公共 fake gateway、响应构造器、文件/目录资产和可覆写拓扑 factory；
- `tests/test_taskbuilder_extraction_protocol.py`：transport schema 与 prompt；
- `tests/test_taskbuilder_authority.py`：来源、证据、否定、数值和重复事实；
- `tests/test_taskbuilder_provided_mesh.py`：polyMesh、单位、维度、patch/zone；
- `tests/test_taskbuilder_public_geometry.py`：STL/OBJ/GEO、辅助附件和 mesh 冲突；
- `tests/test_taskbuilder_questions.py`：输入问题路径、过滤和规范 ID；
- `tests/test_taskbuilder_extraction.py`：模型调用边界、protected path、串行顺序和最终草稿状态。

生产代码拆分期间保留 `tests/test_task_extractor.py` 作为不移动的黑盒回归集。只有全部生产模块
完成拆分、45 个既有场景仍通过且职责审计通过后，才在独立步骤中移动测试。拆分前后比较去掉
文件名前缀后的测试函数名和参数 ID，不能只比较总数。

CLI、compiler、validator 和 `TaskSpec` 测试保留在原文件，除非存在完全相同的输入和断言。

### 5.2 删除准则

只有满足以下全部条件的测试才允许删除或合并：

1. 输入合同相同；
2. 覆盖的风险语义相同；
3. 失败时定位到同一责任模块；
4. 合并后的参数化用例仍显示每个风险场景名称；
5. 删除前后聚焦测试与全量测试均通过。

测试总数不是验收指标。允许测试数量基本不变，但测试源码和 fixture 重复必须明显下降。

## 6. 文档权威与归档

### 6.1 当前权威阅读路径

新建 `docs/current-state.md`，并把新会话的阅读顺序固定为：

1. `docs/current-state.md`：当前状态、证据边界、技术债和下一步候选；
2. `AGENTS.md`：仓库工作规则和禁止路线；
3. `docs/architecture.md`：当前模块与数据流；
4. `docs/independent-agent-quickstart.md`：当前 CLI 使用方式；
5. 具体功能的现行设计文档。

`README.md` 只链接这一阅读路径和少量用户入口，不再陈列大批阶段报告。

### 6.2 历史材料

新建 `docs/archive/README.md`，按“旧架构分析、已完成计划、阶段报告、发布记录”分类索引历史
文档，并明确它们不是当前规格。历史文件原则上不删除；只有内容已经被逐字包含、没有独立证据
价值且没有有效引用时才允许删除。

本轮不批量移动所有历史文件，以避免制造大量失效链接。归档状态由索引和当前文档入口决定；
后续如要物理迁移目录，应作为独立、机械化的文档任务执行。

### 6.3 口径修正

必须修正 `AGENTS.md` 中旧的 TaskBuilder 规则：TaskBuilder 阻断的是用户/资产才能提供的输入
权威缺口；solver、物性候选、边界数值、时间控制和工程容差由后续 CaseDesigner/RiskGate
处理。当前状态文档还必须如实记录：

- 本机 Foundation OpenFOAM 10 已有真实闭环证据；
- 新 polyMesh 输入路线已完成真实草稿验证；
- 该次草稿因用户未声明长度单位而正常终止，未形成新的求解结果；
- 第二台干净 Ubuntu + Foundation v10 跨机门禁仍为 `NOT_RUN`；
- Desktop 实机端到端门禁仍不能从单元测试外推。

## 7. 兼容与错误处理

- 所有原有公开 import 继续从 `foampilot.taskbuilder` 导出；
- 内部模块名称不成为稳定公开 API；
- 模型返回非法 schema、事实来源不可信、资产冲突、拓扑角色不存在或单位未知时，错误状态和
  中文恢复说明保持不变；
- 代码移动后只能有一个权威实现，禁止在 `extraction.py` 保留兼容副本；
- 不使用宽泛 `except` 或 silent fallback 掩盖拆分造成的 import/验证错误。
- 任何拆分若需要目标文件承担架构表未声明的职责，必须停止并报告架构冲突；不能借等价重构
  修改 `docs/architecture.md` 来追认越界实现。

## 8. 实施顺序

本轮采用 characterization-first 的等价重构顺序：

1. 在当前 `main` 建立新鲜 focused/full baseline，并保存45个 extractor 场景的规范化 ID；
2. 只抽取 `tests/support/taskbuilder.py`，保持生产代码和测试文件位置不变；
3. 保持原黑盒测试不移动，依次拆 protocol、authority、provided mesh、public geometry、
   question policy/projection 和薄 orchestrator；
4. 每移动一个生产职责，立即运行原 `tests/test_task_extractor.py` 与该职责的邻接测试，并执行
   import/职责审计；
5. 生产拆分完全稳定后才移动测试文件，再比较规范化场景 ID；
6. 最后运行 deterministic real-asset extractor gate、全量、发行物和 wheel 临时安装门禁。

这是既有行为的 characterization refactor，不人为制造“先失败再通过”的新功能测试。任何红灯都
表示迁移回归或已有基线变化，必须当步解决，不能积累到最终全量测试。

## 9. 验收门禁

实施完成必须提供以下新鲜证据：

1. `git diff --check`；
2. `python -m compileall -q src tests`；
3. TaskBuilder 聚焦测试通过；
4. 全量 deterministic/Qt-offscreen 测试通过；
5. 使用真实 porousBlockage polyMesh、冻结模型响应和重构后的 `extract_task_draft()` 重新生成
   draft，仍只报告 `geometry.length_unit` 阻断；旧 YAML 的 `validate-draft` 只能作为补充证据；
6. 从干净 sdist 构建 wheel，发行物源码集合与工作树完全一致；
7. wheel 安装到临时目录后，从该目录成功导入新拆分模块并验证真实草稿；
8. `git status --short` 只包含本轮预期变更，提交后为空。

此外必须检查：

- `docs/architecture.md` 的文件级目录仍覆盖全部生产 Python 文件；
- package import-boundary 测试仍通过；
- side effect owner 没有增加；
- 删除/合并清单中的每一项都有架构职责与保留实现映射。
- 45 个既有 extractor 场景的规范化测试函数名和参数 ID 全部保留；
- 只有 `extraction.py` import 模型 gateway/预算/trace，其他新模块保持纯计算或只消费已验证事实；
- `INPUT_QUESTION_PATHS`、来源投影和每个移动符号都只有一个权威定义。

本轮不声称新的 OpenFOAM solver completion、物理验收或跨机 qualification。

## 10. 交接结果

最终提交应让下一次对话无需读取本轮长上下文即可回答：

- FoamPilot 当前实际能做什么；
- 哪些能力只在本机或测试中验证；
- polyMesh 输入为何会在单位未知时停止；
- 当前有哪些明确技术债；
- 推荐下一项工作是什么；
- 用哪些命令重新建立可信基线。
