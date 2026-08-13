# FoamPilot 代码库减重与新会话交接设计

状态：**已批准，待实施**
日期：2026-08-13

## 1. 目标

本轮只收束已经完成的开发，不扩展 FoamPilot 功能。实施后应满足：

1. 当前 TaskBuilder/polyMesh 行为和公开 CLI 契约保持不变；
2. 大型实现文件按单一职责拆分，后续修改不必一次加载整条输入链；
3. 重复测试数据、重复模型响应和重复断言得到合并，同时保留不同风险语义的回归门禁；
4. 当前文档与历史材料分层，旧计划和旧结论不再与现行能力竞争权威；
5. 提供一份新对话可以直接使用的短交接文档，准确记录已验证能力、未验证边界和下一步候选；
6. 完成全量测试、发行物一致性和 wheel 独立安装验证后，在当前 `main` 分支提交。

“减重”以降低认知负担和重复维护为准，不以删除最多代码或减少最多测试数量为准。

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

TaskBuilder 输入链按以下职责拆分，模块只使用包内接口：

| 模块 | 唯一职责 |
|---|---|
| `taskbuilder/extraction_protocol.py` | 模型响应 schema、允许的 fact path 和系统提示词 |
| `taskbuilder/authority.py` | 重复事实归一化、用户文本证据绑定、来源降级规则 |
| `taskbuilder/provided_mesh.py` | 原生 polyMesh 确定性 geometry/mesh 事实和拓扑角色协调 |
| `taskbuilder/public_geometry.py` | STL/OBJ/GEO 等公开几何文件的确定性资产路线 |
| `taskbuilder/questions.py` | 输入问题过滤、规范 ID 和缺失输入问题生成 |
| `taskbuilder/extraction.py` | 调用模型、依次调用上述模块并组装 `TaskDraft` |

`extraction.py` 不再实现领域核验细节。各模块通过显式函数传递 `TaskFact`、`TaskQuestion`、
`PublicAsset` 和 `TaskIngressContext`，不得通过全局可变状态通信，也不得新增第二套 fact 解释器。

减重目标为 `extraction.py` 不超过约 300 行，新增职责模块原则上不超过约 400 行。该数值是
认知复杂度警戒线，不是通过机械拆分空壳文件规避的硬性质量指标。

## 5. 测试整理

### 5.1 结构

将巨型 extractor 测试按行为边界拆为：

- `tests/taskbuilder/conftest.py`：公共 fake gateway、响应构造器、资产和拓扑 fixture；
- `tests/taskbuilder/test_authority.py`：来源、证据、否定、数值和重复事实；
- `tests/taskbuilder/test_provided_mesh.py`：polyMesh、单位、维度、patch/zone；
- `tests/taskbuilder/test_public_geometry.py`：STL/OBJ/GEO、辅助附件和 mesh 冲突；
- `tests/taskbuilder/test_extraction.py`：模型调用边界、protected path 和最终草稿状态。

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

## 8. 验收门禁

实施完成必须提供以下新鲜证据：

1. `git diff --check`；
2. `python -m compileall -q src tests`；
3. TaskBuilder 聚焦测试通过；
4. 全量 deterministic/Qt-offscreen 测试通过；
5. 真实 porousBlockage 草稿仍只报告 `geometry.length_unit` 阻断；
6. 从干净 sdist 构建 wheel，发行物源码集合与工作树完全一致；
7. wheel 安装到临时目录后，从该目录成功导入新拆分模块并验证真实草稿；
8. `git status --short` 只包含本轮预期变更，提交后为空。

本轮不声称新的 OpenFOAM solver completion、物理验收或跨机 qualification。

## 9. 交接结果

最终提交应让下一次对话无需读取本轮长上下文即可回答：

- FoamPilot 当前实际能做什么；
- 哪些能力只在本机或测试中验证；
- polyMesh 输入为何会在单位未知时停止；
- 当前有哪些明确技术债；
- 推荐下一项工作是什么；
- 用哪些命令重新建立可信基线。
