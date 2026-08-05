# 第三阶段：自然语言 TaskBuilder 实施计划

> 执行约束：第二阶段核心接口稳定后内联实施，不分派子代理。

**目标：** 把中文或英文自然语言 CFD 请求和显式声明附件转换为经过审查、可确定性复现的
规范 TaskSpec，同时不绕过 FoamPilot 的求解主链。

**架构：** 模型支持的 TaskExtractor 只生成带来源的 TaskDraft；确定性 DraftValidator 区分
blocking、confirmable 和 advisory；确定性 TaskCompiler 只增加低风险默认值和 registry 支持的
公开检查，最终输出第二阶段定义的规范 TaskSpec。

**技术栈：** Python 3.12、Pydantic v2、PyYAML、现有 ModelGateway、argparse CLI、pytest。

## 全局约束

- 第二阶段 TaskSpec 和前处理核心契约稳定后才开始；
- 不 commit、不 push；
- TaskBuilder 不得调用 Runner 或 OpenFOAM；
- 不推断高影响物理量、单位、边界、材料物性或工程容差；
- qualification 继续使用冻结 TaskSpec，绕过 TaskBuilder；
- 保留稳定英文 code，使用中文 message 和 recovery；
- 第四阶段 Desktop IDE 不在本计划中实施。

---

### 任务 1：TaskDraft 数据契约

**文件：**

- 新增 `src/foampilot/taskbuilder/__init__.py`；
- 新增 `src/foampilot/taskbuilder/models.py`；
- 新增 `src/foampilot/taskbuilder/messages_zh.py`；
- 新增 `tests/test_task_draft.py`。

**接口：**

- 定义 `FactSource`、`TaskFact`、`TaskAssumption`、`TaskQuestion`、`TaskDraftStatus`、
  `TaskDraft`、`DraftIssue` 和 `DraftReview`；
- 每个高影响事实必须有 source/evidence，模型推断不能把草稿直接标记为 confirmed。

- [x] 为合法草稿、重复 fact path、未确认模型推断、blocking 问题和中文错误编写先失败测试；
- [x] 实现严格 Pydantic 模型和跨字段不变量；
- [x] 增加稳定 code 到中文 message/recovery 的映射；
- [x] 运行 TaskDraft 测试。

### 任务 2：确定性 DraftValidator

**文件：**

- 新增 `src/foampilot/taskbuilder/validation.py`；
- 修改 `src/foampilot/taskbuilder/__init__.py`；
- 新增 `tests/test_task_draft_validation.py`。

**接口：**

- 提供 `validate_task_draft(draft, environment=None) -> DraftReview`；
- 缺单位、物性和边界属于 blocking，重要物理解读属于 confirmable，资源默认值属于 advisory。

- [x] 为几何单位、材料数据、稳态/瞬态意图、缺失附件、不支持 capability 和完整请求编写测试；
- [x] 在归一化 fact path 上实现确定性规则；
- [x] solver 建议只作为证据，不创建 CapabilityProfile；
- [x] 运行 Validator 测试。

### 任务 3：公开检查 registry 与 TaskCompiler

**文件：**

- 新增 `src/foampilot/taskbuilder/checks.py`；
- 新增 `src/foampilot/taskbuilder/compiler.py`；
- 修改 `src/foampilot/taskbuilder/__init__.py`；
- 新增 `tests/test_task_compiler.py`。

**接口：**

- 提供 `compile_task_draft(review: DraftReview) -> TaskCompilation`；
- `TaskCompilation` 包含规范 TaskSpec、假设和编译诊断；
- 基础检查来自确定性 registry，工程指标必须来自用户显式事实。

- [x] 覆盖通用检查、瞬态/VOF 检查、压降观测、缺容差和确定性 TaskSpec hash；
- [x] 实现 Foundation v10 和资源预算的低风险默认值；
- [x] 实现 registry 支持的 public checks 和 required outputs；
- [x] blocking 或未确认高影响问题存在时拒绝编译；
- [x] 运行 Compiler 和规范 TaskSpec 测试。

### 任务 4：模型支持的 TaskExtractor

**文件：**

- 新增 `src/foampilot/taskbuilder/extraction.py`；
- 修改 `src/foampilot/models/budgets.py`；
- 修改 `src/foampilot/taskbuilder/__init__.py`；
- 新增 `tests/test_task_extractor.py`。

**接口：**

- 增加 `ModelStage.TASK_EXTRACTION`；
- 提供 `extract_task_draft(request, assets, gateway, budget, trace) -> TaskDraft`；
- 模型输出必须通过 schema 校验，并保留 source/evidence 区分。

- [x] 覆盖中英文提取、虚构物性、伪造来源、受保护路径和模型错误映射；
- [x] 增加明确禁止虚构的系统提示和独立模型阶段；
- [x] 系统复核用户原文/附件来源，将无证据高影响断言降级为未确认推断；
- [x] Gateway trace 不保存 request/response 正文；
- [x] 在线模型进程异常在有界重试后返回稳定 code 和中文恢复说明。

### 任务 5：`foampilot task` CLI

**文件：**

- 修改 `src/foampilot/cli/main.py`；
- 修改 `tests/test_native_agent_cli.py`；
- 新增 `tests/test_taskbuilder_cli.py`。

**接口：**

- 增加 `foampilot task draft`、`validate-draft` 和 `compile`；
- draft 与 compilation 使用显式路径和独占创建；
- 三个命令通过真实 gate 前不增加 `ask` 快捷命令。

- [x] 覆盖所有子命令、退出码、已有文件和超大附件；
- [x] 增加 request、公开附件、backend/model 和 output 参数；
- [x] handler 只调用 TaskBuilder API；
- [x] 输出稳定 JSON 和简洁中文人类可读结果；
- [x] CLI、help 和仓库命令面快照测试通过。

### 任务 6：语义夹具与真实端到端 gate

**文件：**

- 新增 `tests/fixtures/taskbuilder/` 中英文语义夹具；
- 新增 `tests/test_taskbuilder_semantics.py`；
- 修改 `docs/independent-agent-quickstart.md`；
- 修改 `docs/agent-integration.md`；
- 新增 `docs/reports/2026-08-04-stage-3-taskbuilder.md`；
- 修改 `tests/test_repository_docs.py`。

- [x] 覆盖简单流动、传热、VOF、固体、CHT、surface 和 Gmsh 的完整/缺失事实；
- [x] 证明缺失高影响事实的夹具不会通过虚构继续编译；
- [x] 全仓确定性测试通过：`471 passed, 4 skipped`；
- [x] preflight 与浅层 model doctor 快速返回；
- [x] 一个冻结模型响应、真实 OpenFOAM 的 request-to-solve gate 通过；
- [ ] 完成五个在线模型 request-to-draft-to-TaskSpec-to-solve gate，覆盖 blockMesh、surface/Gmsh、
  瞬态和多物理；当前外层只读沙箱使 Codex CLI 深层提取返回 `PROCESS_INTERRUPTED`，不得伪称通过；
- [x] 已验证的真实 gate 使用规范 `NativeAgent.solve()` 产物，failure domain 保持分离；
- [x] 更新中文主文档和带证据范围的阶段报告；
- [x] wheel/package-data 构建与隔离导入验证通过；
- [x] 运行 `git diff --check` 并保持工作区未提交。
