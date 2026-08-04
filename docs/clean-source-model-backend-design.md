# FoamPilot 原地来源治理与模型后端重构设计

状态：已实施并通过提交前验收

日期：2026-08-04

实施基线：`901e338`（`feat: expand OpenFOAM qualification and Chinese guidance`）

验收结论：模型后端已切换为公开 CLI/OpenAI-compatible 契约，私有 OAuth/provider
实现与 legacy plan 路径已删除；合成 replay、来源审计、中文错误、wheel 隔离加载、
OpenFOAM preflight、模型 doctor 和一个真实非 tutorial `NativeAgent.solve()` 闭环均已
验证。最终许可证采用用户明确确认的双版权声明，该决定覆盖设计阶段对版权主体不作预判
的临时约束。

## 1. 决策摘要

`/home/edwin/workplace/FoamPilot` 是唯一主仓库。本次工作直接在该仓库中进行定向重构，
不创建新的产品仓库，不重写 Git 历史，也不把
`/home/edwin/workplace/FoamPilot-clean-source` 作为后续主线。

`FoamPilot-clean-source` 只作为只读参考。其中已经验证过的实现可以逐项审查后复用，
但不得整目录覆盖主仓库，也不得带入与来源治理无关的架构扩展。完成主仓库迁移与验证后，
是否删除该参考目录由用户另行决定。

此前工程相似性审计未发现达到既定阈值的大段源码文本复制。因此，本设计不预设现有代码
属于直接复制；需要处理的是私有模型协议、设计影响较重、来源说明不足或长期维护风险较高的
内容。工程审计用于形成可追踪证据，不替代正式法律审查。

## 2. 目标与非目标

### 2.1 目标

1. 保持 FoamPilot 当前 CFD 闭环、CLI、qualification 与 Git 主线连续。
2. 用公开、可维护的模型后端契约替换私有 Codex OAuth/Provider 实现。
3. FoamPilot 不读取第三方认证文件，不保存 token，不实现供应商私有认证协议。
4. 默认向用户提供中文模型错误说明与恢复建议，同时保留稳定英文错误码。
5. 定向审查代码、测试资产、文档、Knowledge 与 Skills 的来源。
6. 用合成资产替换来源不清晰或直接取自 tutorial 的冻结 fixture。
7. 以轻量、可重复的自动审计防止旧私有协议和不明来源内容重新进入仓库。

### 2.2 非目标

- 不创建新的 `FoamPilot-clean-source` 产品仓库或新的 root commit。
- 不迁移当前任务不需要的 ExecutionPlan 缓存、额外状态机或 qualification 扩展。
- 不建设插件市场、Python entry point 插件体系或常驻模型代理服务。
- 不改变 OpenFOAM Runner 的 typed command、安全边界和资源控制职责。
- 不增加 renderer、逐题硬编码或 tutorial 复制路径。
- 不以本次来源重构解决全部 CFD 字典准确性和物理精度问题。
- 不自动提交、推送、修改远程或删除参考目录。

## 3. 保持不变的 CFD 主流程

```text
TaskSpec
→ 能力路由与动态公开知识检索
→ Agent 编写完整原生 OpenFOAM case
→ 语义检查与原生检查
→ typed command 安全执行
→ 动态公开评测
→ 一次定向修复
→ 不可变产物与报告
```

`NativeAgent.solve()` 仍是规范执行入口。模型后端重构不得绕过 case 生成、Runner、评测、
repair 或 artifact state machine，也不得直接替代 OpenFOAM 求解步骤。

## 4. 模型后端边界

目标调用关系为：

```text
NativeAgent
→ ModelGateway
→ BackendRegistry
→ ModelBackend
   ├─ CommandBackend
   ├─ OpenAICompatibleBackend
   └─ ScriptedBackend
```

### 4.1 `ModelBackend`

统一表示一次结构化模型交换。后端只负责一次请求或一次外部进程调用，不负责跨请求 retry、
熔断、workflow 状态或 OpenFOAM 执行。

### 4.2 `CommandBackend`

通过固定 `argv` 和 JSON/JSONL 契约调用已安装、已认证的外部模型运行器。必须满足：

- 不拼接 shell 字符串；
- 不接收模型生成的命令；
- 不解析外部运行器认证文件；
- 子进程环境使用白名单；
- stdout 只接受约定的结构化响应；
- stderr 和 trace 在落盘前进行秘密信息脱敏；
- 探测和请求均有独立短超时。

只有外部运行器存在公开、稳定的机器接口时，FoamPilot 才提供内置预设；否则由用户配置
符合通用命令契约的桥接命令。

### 4.3 `OpenAICompatibleBackend`

支持公开兼容 API 与本地推理服务。凭据只允许通过命名环境变量传入，不接受明文 CLI key，
也不把凭据写入配置、日志、trace 或运行产物。

### 4.4 `ScriptedBackend`

仅用于单元测试、故障注入和冻结回放，不参与真实模型推理。

### 4.5 `BackendRegistry`

只保存后端 ID、类型、固定命令或公开 endpoint、model ID、能力、优先级、超时以及凭据环境
变量名称。首期采用显式轻量注册，不引入动态插件发现。

### 4.6 `ModelGateway`

继续统一负责：

- 逻辑请求 deadline；
- 有限 retry 与退避；
- 错误分类；
- circuit breaker；
- generation/repair trace；
- retryable interruption 的检查点恢复。

普通求解允许在过载、限流、网络中断、超时或外部进程中断后切换到已配置的下一后端。
Qualification 必须固定 backend 与 model，只允许同一后端内部有限 retry，禁止跨模型降级。

所有后端不可用时，workflow 返回 `DEFERRED` 并保留恢复入口，不得把模型 blocker 伪装成
网格、solver 或 validation failure。

## 5. 错误表达契约

英文 `code` 是稳定机器接口，中文 `message` 和 `recovery` 是默认人机界面。程序不得依据
中文文本分支。

```json
{
  "code": "BACKEND_UNAVAILABLE",
  "message": "未找到可用的模型后端。",
  "recovery": "请检查外部运行器或模型服务配置。",
  "backend_id": "local-command",
  "retryable": false
}
```

首期至少覆盖：

- `BACKEND_UNAVAILABLE`
- `BACKEND_MISCONFIGURED`
- `AUTH_FAILED`
- `RATE_LIMITED`
- `OVERLOADED`
- `NETWORK_UNAVAILABLE`
- `TIMEOUT`
- `PROCESS_INTERRUPTED`
- `SCHEMA_INVALID`
- `POLICY_REJECTED`

模型服务失败作为 `terminal_blocker`；OpenFOAM/case 的首要失败作为 `primary_failure`。
后发生的模型故障不能覆盖已经发生的 mesh、solver 或 validation 根因。

## 6. 来源治理

### 6.1 文件处理分类

- **保留**：没有实质相似性，来源清楚，且属于当前有效实现。
- **重写**：设计或实现受旧项目影响较重、来源说明不足，或维护风险较高。
- **删除**：私有 OAuth 协议、失效兼容代码、无继续保留价值且无法说明来源的资产。

### 6.2 定向范围

1. 完整重写模型接入边界，并在调用方迁移完成后删除旧 OAuth/Provider 实现。
2. 审查冻结 replay fixture；来源不清晰或直接取自 tutorial 的内容改为自有生成器合成。
3. OpenFOAM 事实性知识保留必要关键字，但解释重新组织并记录来源、版本和许可证。
4. 文档、prompts 与 Skills 只在确有来源风险时重写，不做无意义同义替换。
5. 增加精简的 `PROVENANCE.md` 与 `THIRD_PARTY_NOTICES.md`。
6. 增加轻量审计，检查旧类名、私有 endpoint、凭据路径、大段文本和高重合片段。

标准 OpenFOAM 字典关键字、API 字段和许可证正文的必要一致不视为实质复制。许可证版权主体
不得由实现者擅自填写或修改，必须由用户或正式法律审查决定。

## 7. 实施阶段

### 阶段 0：冻结当前基线（已完成）

- 基线提交：`901e338`；
- 全量测试：328 passed，3 skipped；
- wheel：成功构建并从临时安装目录加载；
- preflight：`PASS`，受限环境下正确选择 audited typed host fallback；
- `docs/superpowers/`、运行产物、缓存、凭据与本设计文档未进入基线提交；
- 基线只在本地 `main`，未推送。

### 阶段 1：模型边界回归测试

- 冻结现有调用方行为；
- 增加 backend 探测、输出 schema、超时、错误分类和脱敏测试；
- 增加普通模式降级与 qualification 固定后端测试；
- 在删除旧实现前证明新接口覆盖必需行为。

### 阶段 2：原地替换模型边界

- 在 `src/foampilot/models/` 内实现最小 backend 契约；
- 迁移 CLI、qualification、generation 与 repair 调用方；
- 保持 `NativeAgent.solve()` 与 workflow 状态机不变；
- 调用方和测试全部迁移后删除私有 OAuth/Provider 文件。

### 阶段 3：定向来源治理

- 审计测试 fixture、文档、Knowledge 和 Skills；
- 仅重写或删除实际存在风险的内容；
- 增加来源说明、第三方清单和轻量来源 gate；
- 不扩展算例题库或新增工作流组件。

### 阶段 4：独立性验证

- 运行全量 Python 测试；
- 构建、检查并从临时目录加载 wheel；
- 运行 `foampilot preflight --json`；
- 执行模型后端故障注入；
- 至少一个真实 OpenFOAM case 通过完整 `NativeAgent.solve()`；
- 运行来源审计；
- 检查包内不依赖 Foam-Agent 或 `FoamPilot-clean-source` 路径。

## 8. 验收标准

以下条件必须同时满足：

1. 主仓库不再包含私有 Codex OAuth 协议、认证文件解析或私有 endpoint。
2. 凭据不通过 CLI 明文传入，也不进入配置、日志、trace 或 artifact。
3. 模型错误具有稳定英文错误码、中文说明和中文恢复建议。
4. 普通求解具有有限后端恢复能力，qualification 固定 backend/model。
5. `NativeAgent.solve()`、Runner、评测、repair 与不可变产物闭环保持完整。
6. 来源不清晰的 fixture 已替换为可追踪的合成资产。
7. Knowledge、Skills 和文档只发生必要的定向重写。
8. 没有引入计划缓存、插件平台、renderer 或新状态机。
9. 全量测试、wheel、preflight、故障注入和真实 OpenFOAM 最小闭环通过。
10. 包内不依赖 Foam-Agent、`FoamPilot-clean-source` 或本机临时路径。
11. 用户已有修改得到保留；提交、推送和目录删除均需单独授权。

## 9. 参考目录退出条件

只有当主仓库完成模型边界迁移、来源审计、wheel 验证和真实 OpenFOAM gate 后，
`FoamPilot-clean-source` 才不再承担参考价值。届时先执行只读差异核对，再由用户明确决定是否
删除。该目录的存在与否不得成为 FoamPilot 安装、测试或运行的前置条件。
