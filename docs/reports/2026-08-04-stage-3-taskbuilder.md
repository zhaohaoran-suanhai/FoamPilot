# 第三阶段：自然语言 TaskBuilder 实施记录

## 结论范围

本阶段在规范 `TaskSpec -> NativeAgent.solve()` 主链之前增加了一个可选、可审计的任务构建边界：

```text
中文或英文自然语言 + 显式公开附件 metadata
  -> TaskExtractor
  -> 带来源的 TaskDraft
  -> 确定性 DraftValidator
  -> 确定性 TaskCompiler
  -> TaskSpec v2
  -> 既有 NativeAgent.solve()
```

TaskBuilder 不持有 Runner、不运行 OpenFOAM、不选择最终求解器族，也没有建立第二套状态机。
当前证据证明完整请求能够编译并进入一条真实 Foundation OpenFOAM v10 闭环；尚未完成规格中
计划的五类真实模型/几何 gate，因此不能据此宣称 surface、Gmsh 和多物理自然语言入口已经全部
通过原生求解验证。

## 已实现内容

- `TaskFact` 为每个事实保存 `source`、`evidence`、影响等级和确认状态；
- 事实来源区分用户原文、公开附件、用户确认、系统默认和模型推断；
- 用户原文与附件来源由确定性证据复核，不信任提取模型自报的 `confirmed`；
- 高/中影响模型推断不能直接成为 confirmed draft；
- Validator 区分 blocking、confirmable 和 advisory，并使用稳定英文 code、中文说明和恢复建议；
- 单位、公开资产、物理相态、物性、边界、瞬态终止时间、VOF 初始相分数、CHT region 和显式
  solver capability 具有确定性检查；
- Compiler 只填充 Foundation v10、资源预算和 `mesh.strategy=auto` 等可见低风险默认值；
- public checks 只来自已有 evaluator 支持的确定性 registry，不由模型自由发明；
- 未给 tolerance 的工程指标只作为观测输出，不参与 PASS 判定；
- `foampilot task draft`、`validate-draft` 和 `compile` 使用显式输入输出路径，并且不覆盖已有文件；
- 附件只向模型暴露相对路径、用途和 SHA256，单文件限制为 256 MiB，hash 采用有界流式读取；
- qualification 继续直接使用冻结 TaskSpec，不经过自然语言重述。

## 当前证据

| 证据 | 结果 | 能证明什么 |
| --- | --- | --- |
| TaskDraft、Validator、Compiler 和 CLI 单元测试 | 通过 | 来源、状态、默认值、错误语义和确定性 hash 可复现 |
| 8 个中英文语义 fixture | 通过 | 完整简单任务可编译；缺单位、热物性、VOF 初始相、固体材料、CHT region 或附件时不会虚构后继续 |
| fake Gateway 提取测试 | 通过 | 原文/附件来源会复核，虚构高影响事实会降级并要求确认，受保护路径被拒绝 |
| 自然语言真实 OpenFOAM gate | `1 passed`，约 1.9 s | request 经 TaskDraft、TaskSpec 和同一 `NativeAgent.solve()` 执行 `blockMesh -> checkMesh -> icoFoam`，达到 `PUBLIC_VALIDATION_PASS` |
| 在线 Codex CLI 提取探测 | 约 6.1 s 后 `TASK_EXTRACTION_DEFERRED / PROCESS_INTERRUPTED` | 当前外层只读开发沙箱阻止 Codex CLI 初始化；Gateway 两次有界传输后结束，CLI 保留稳定模型码与中文恢复说明，没有权限对话或长时间停滞 |
| 全仓确定性回归 | `471 passed, 4 skipped` | 三阶段新增契约未破坏既有生成、Runner、验证、续跑和 qualification 测试 |
| wheel 构建与隔离导入 | 通过 | 安装产物包含 preprocessing、TaskBuilder、mesh Skill 和 knowledge manifest，安装后的 `foampilot task` 命令面可用 |

真实 gate 使用完整英文瞬态侧驱方腔请求。Task extraction 和完整 case plan 使用冻结结构化模型
响应，以消除网络随机性；网格、检查和 `icoFoam` 则在本机 Foundation OpenFOAM v10 真实执行。
产物包含 `geometry-facts.json`、`mesh-quality-report.json`、规范 attempt 和通过哈希验证的
artifact manifest。该 gate 证明任务构建边界与真实求解主链正确连接，不证明在线模型每次都能
准确提取或编写 case。

`foampilot model doctor` 在当前环境能够快速通过 Codex CLI 的版本与登录状态探测，但它不是一次
计费的结构化生成请求。上述在线提取进一步证明当前外层沙箱的运行权限不足。普通宿主机需要以
真实 `task draft`/`plan` 作为深层模型 gate；FoamPilot 现在会把这类失败报告为 backend deferral，
而不是 `INTERNAL_ERROR` 或 CFD 失败。

## 缺口与后续 gate

- 尚未完成五个在线模型 request-to-solve gate；当前只有一个冻结模型响应、真实 OpenFOAM gate；
- 当前机器未发现 Gmsh，Gmsh 自然语言入口只能验证缺失能力归因，不能宣称原生网格通过；
- surface/snappy、多区域 CHT、VOF 和固体自然语言请求仍需分别执行真实原生 gate；
- CLI 暂无持续聊天会话和交互式确认表单。缺失信息会生成结构化问题，需要用户、上游 Agent 或
  未来 Desktop IDE 补充后重新生成/确认 draft；
- TaskBuilder 不替代工程师提供物性、单位、边界条件和工程验收标准，也不提高模型编写 case 的
  数值准确率保证。

## 稳定性结论

自然语言入口的失败发生在 solve run 创建之前，因此不会污染 mesh、solver 或 qualification
统计。模型服务过载仍由共享 Gateway 有界重试和分类；缺失高影响事实则立即返回任务域错误，
不会消耗 case generation 和 OpenFOAM 时间。完整 TaskSpec 产生后，所有任务继续走同一条
路由、生成、检查、Runner、公开验证、有限 repair 和不可变产物链。
