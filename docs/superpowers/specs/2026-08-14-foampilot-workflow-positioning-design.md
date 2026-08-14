# FoamPilot Workflow 定位文档校准设计

状态：待项目所有者审阅

日期：2026-08-14

## 1. 目的

统一 FoamPilot 的产品介绍和架构术语，使文档准确反映当前实现：FoamPilot 的一级定义是
**面向 Foundation OpenFOAM 10 的大模型增强 CFD Workflow**，而不是采用开放式自主控制范式的
AI Agent。

此次工作只校准文档，不修改生产代码、公共 API、CLI、Schema、artifact、状态机或运行能力。

## 2. 规范定义

文档应统一采用以下定义：

> FoamPilot 是面向 Foundation OpenFOAM 10 的大模型增强 CFD Workflow。确定性工作流拥有阶段
> 顺序、命令编译、执行权、恢复、证据和验收；大模型只在预先规定的语义阶段中解释任务、形成
> 设计、编写 case 和提出有限修复。

FoamPilot 当前属于 Workflow 范畴，因为：

- 主阶段和状态转换由程序预先定义；
- 模型不能自主增删阶段或决定任意下一步动作；
- 模型不能自由选择、构造或执行系统命令；
- 风险门禁、执行、证据和验收权威均属于确定性程序；
- repair 是预算和冻结 envelope 内的有限分支，不是开放式自主循环。

FoamPilot 具有大模型推理能力，但该能力不改变其当前控制范式。允许使用
“workflow-controlled model reasoning”或“工作流控制的大模型推理”等辅助表述；不得把当前系统
描述为能够自主规划、自由调用工具或开放式行动的通用 AI Agent。

## 3. 术语边界

### 3.1 应采用的术语

- 一级产品类型：`大模型增强 CFD Workflow`；
- 控制骨架：`确定性工作流`、`规范状态机`；
- 模型职责：`受控推理阶段`、`语义推理槽位`；
- 外部集成：`外部 Agent 调用 FoamPilot Workflow`。

### 3.2 允许保留的 Agent 名称

不得对代码和公共接口做机械重命名：

- `NativeAgent` 保持现名，作为当前规范编排入口的稳定 API；
- 文件名、类型名、artifact 字段和 CLI 不因本次文档校准改变；
- “外部 Agent”仍用于描述调用 FoamPilot 的上游系统；
- 历史报告中的 Agent 表述保持历史原文，不进行全仓库替换。

介绍 `NativeAgent` 时应说明：该名称不表示 FoamPilot 使用 LangGraph、ReAct 或其他开放式自主
Agent 控制范式。

## 4. 文档修改范围

### 4.1 `README.md`

- 将首页一级定义改为“大模型增强 CFD Workflow”；
- 在规范流程前简要说明 Workflow 与模型推理的职责分工；
- 保留不依赖 LangGraph、Foam-Agent、FAISS 和 MCP 的事实；
- 避免把单次成功运行表述为通用自主能力。

### 4.2 `docs/architecture.md`

- 在“产品和能力边界”中写入规范定义；
- 增加“Workflow 定义与自主性边界”小节；
- 将“一条状态机”“模型无执行权”“冻结后单向流动”等现有不变量与 Workflow 定位显式关联；
- 说明 `NativeAgent` 是编排入口名称，不是外部 Agent framework；
- 保持本文作为现行职责规范的权威地位。

### 4.3 `docs/system-overview.md`

- 将“单 Agent CFD 工作流”改为“大模型增强 CFD Workflow”；
- 用当前真实数据流说明固定工作流和受控模型阶段；
- 明确系统不依赖 LangGraph，且不具备开放式工具选择权；
- 不改变现有功能、组件或能力边界描述。

### 4.4 `docs/agent-integration.md`

- 保留文件路径，避免破坏已有链接；
- 将文档定位改为“外部 Agent 集成 FoamPilot Workflow”；
- 明确外部 Agent 不得复制或绕过 FoamPilot 的状态机、Runner 和证据链；
- 保留现有 CLI/Python 集成合同。

### 4.5 `docs/current-state.md`

- 追加 2026-08-14 定位校准记录；
- 明确这是术语和架构说明更新，不是运行能力变化；
- 记录当前实现仍是固定 Workflow，知识驱动架构重构尚未设计或实施。

### 4.6 `CHANGELOG.md`

- 在当前未发布部分记录 Workflow 一级定义的文档校准；
- 不宣称新增 Agent 或 CFD 能力。

## 5. 不在本次范围内

此次文档更新不得：

- 重命名 `NativeAgent` 或其他公共 API；
- 修改工作流阶段、模型调用、Runner、RiskGate 或 evidence；
- 把多孔扩展迁移到知识库；
- 定义新的知识库 Schema 或 physics capability 机制；
- 把未来知识驱动目标描述为已经实现；
- 对历史文档和报告进行机械术语替换；
- 引入 LangGraph 或任何新的 Agent framework。

物理知识从机械代码迁移到知识库属于后续独立架构设计，不与本次术语校准混合。

## 6. 一致性要求

更新后应满足：

1. README、架构规范和系统概览均以“大模型增强 CFD Workflow”为一级定义；
2. 不再出现“FoamPilot 基于 LangGraph”或“当前是开放式自主 Agent”的暗示；
3. `NativeAgent`、外部 Agent 和模型推理阶段的含义彼此区分；
4. Workflow 定位不削弱现有安全、证据和 fail-closed 不变量；
5. 当前能力与未来知识驱动方向明确分开；
6. 文档链接、命令示例和公共 API 名称保持有效。

## 7. 验证

实施后至少执行：

- 搜索核心文档中的 `Agent`、`Workflow`、`LangGraph` 和 `自主`，人工检查上下文；
- `pytest -q tests/test_repository_docs.py`；
- `python -m compileall -q src tests`；
- `git diff --check`；
- 检查本次 diff 只包含批准的文档和必要的文档测试调整。

本次不需要重新运行真实 OpenFOAM case，因为没有生产代码、运行合同或 case 资产发生变化。
