# 官方题库 30 题：Agent Harness v2 冷路径基线

日期：2026-08-06
状态：30 题基线、基线后确定性修复和三题真实定向复测已完成

## 1. 执行口径

- suite：`official-corpus-30-baseline-v1`
- backend：`codex-cli`
- model：`gpt-5.6-sol`
- worker：1
- OpenFOAM：Foundation v10
- run root：`/tmp/foampilot-agent-harness-official-30-20260806-v2`
- 原始报告：`official-corpus-30-baseline-v1-report.json`

该轮通过规范 `NativeAgent.solve()` 串行执行。目标 tutorial 不进入 Agent 上下文，Runner、公开验证与
私有物理评测边界保持不变。

## 2. 结果

| 指标 | 结果 |
|---|---:|
| PASS | 21/30 |
| FAIL_AGENT | 9/30 |
| DEFERRED_BACKEND | 0/30 |
| BLOCKED_ENVIRONMENT | 0/30 |
| case 生成成功 | 30/30 |
| 目标 solver 启动 | 28/30 |
| solver 正常结束 | 22/30 |
| 公开验证通过 | 21/30 |
| 严格物理评测通过 | 11/15 |
| repair run | 8 |
| repair 后成功 | 3 |

性能证据：

| 指标 | 结果 |
|---|---:|
| 冷路径进入 OpenFOAM P50 | 142.854 s |
| 冷路径进入 OpenFOAM P95 | 259.948 s |
| 单题端到端 P50 | 184.781 s |
| 单题端到端 P95 | 619.299 s |
| 累计模型时间 | 5312.326 s |
| 累计 OpenFOAM 时间 | 2361.039 s |
| logical model requests | 42 |
| transport attempts | 48 |

## 3. 未通过题目

| case | native status | attempt | 基线终态 |
|---|---|---:|---|
| `multiphase-dam-break` | `PUBLIC_VALIDATION_FAILED` | 2 | repair 预算耗尽 |
| `buoyant-cavity` | `SOLVER_FAILED` | 1 | repair scope 路径受 MPI 日志污染 |
| `compressible-blocked-channel` | `STATIC_INSPECTION_FAILED` | 1 | `postProcess` 阶段元数据错误 |
| `mhd-hartmann` | `SOLVER_FAILED` | 1 | 修复补丁无有效变化 |
| `pimple-blocked-channel` | `STATIC_INSPECTION_FAILED` | 1 | `postProcess` 阶段元数据错误 |
| `piso-porous-blockage` | `SOLVER_FAILED` | 2 | repair 预算耗尽 |
| `rhocentral-forward-step` | `SOLVER_FAILED` | 2 | repair 预算耗尽 |
| `buoyant-hot-room-boussinesq` | `SOLVER_FAILED` | 2 | repair 预算耗尽 |
| `two-liquid-lock-exchange` | `SOLVER_FAILED` | 2 | repair 预算耗尽 |

这里的 9 个失败不能统一归因于 CFD 知识不足。两题发生在目标 solver 前，是确定性元数据归一化
缺口；`buoyant-cavity` 的 repair 越界是日志路径解析缺口；其余题目仍需在后续复测中区分 case
字典、数值稳定性、物理验证与 repair 能力。

## 4. 基线后已完成的轻量修复

### 4.1 已知 utility command 的阶段归一化

`blockMesh`、`checkMesh`、`postProcess` 等无歧义工具命令使用共享的确定性 stage 表。模型把
`postProcess` 错标成 `solve` 时，FoamPilot 保留原始 authored plan，同时在规范 plan 中改为
`postprocess` 并记录 normalization evidence。未知命令和 solver stage 仍由原有策略检查，不扩大
命令白名单。

### 4.2 native evaluator 日志只解析一次

一次 validation 现在只对每个 step 日志解析一次，再供所有 public check 复用。失败 step 继续走
快速失败分类，不扫描全部日志。该修复针对 `cht-cooling-cylinder` 产生超大日志后，评测器按检查项
重复解析导致的额外时间和内存开销，不改变验证标准。

### 4.3 清洗 MPI rank 对 case 路径的污染

`buoyant-cavity` 的真实日志指向 `constant/pRef` 缺少 `dimensions`，但 MPI 交错输出形成了
`constant/pRef[14]`。分类器现在从 case 路径中排除 rank 标记，RepairScope 因而精确包含已有的
`constant/pRef`；没有放宽到无关文件或任意 `add_file`。

## 5. 代码验证

```text
572 passed, 7 skipped in 19.69s
git diff --check: PASS
```

新增回归覆盖：

- utility command stage 归一化且不修改原始 evidence；
- 每个 native log 在一次 validation 中只解析一次；
- failed-step 快速路径不进行完整日志解析；
- MPI rank 粘连后的 `constant/pRef[14]` 还原为 `constant/pRef`。

## 6. 三题真实定向复测

复测使用 Foundation v10、`codex-cli/gpt-5.6-sol` 和规范 qualification 路径，目标 tutorial
仍不进入 Agent 上下文。首轮 suite 位于：

```text
/tmp/foampilot-agent-harness-targeted-retest-3-20260806-v1
```

`compressible-blocked-channel` 首次 generation 在 420 秒请求期限处结束为
`DEFERRED_BACKEND/TIMEOUT`。随后以相同 qualification policy 进行一次独立冷路径重跑：

```text
/tmp/foampilot-agent-harness-targeted-retest-compressible-20260806-v2
```

最终有效证据如下：

| case | 最终结果 | solver 进入 | solver 正常结束 | 严格物理评测 |
|---|---|---:|---:|---:|
| `compressible-blocked-channel` | PASS | 是 | 是 | PASS |
| `buoyant-cavity` | PASS | 是 | 是 | PASS |
| `pimple-blocked-channel` | FAIL_AGENT | 是 | 否 | 不适用 |

### 6.1 `compressible-blocked-channel`

- 模型生成：258.238 s；
- 首个 OpenFOAM 命令：258.765 s；
- OpenFOAM：41.683 s；
- `blockMesh`、`checkMesh`、初始化和 `rhoPimpleFoam` 全部完成；
- public validation 与 total mass、primitive profiles 等严格物理指标通过。

Agent 再次把初始化用 `postProcess` 错标为 `initialize`，normalizer 将其改为
`postprocess` 并记录证据。此前的静态检查阻断没有复现。

### 6.2 `buoyant-cavity`

- 模型生成：156.151 s；
- 首个 OpenFOAM 命令：156.683 s；
- 10 核 OpenFOAM：27.601 s；
- `buoyantFoam` 到 1000 iteration 正常结束；
- public validation 与 wall heat balance、profiles、mean Nusselt 严格物理指标通过。

本轮 Agent 首次就生成了带正确 dimensions/value 的 `constant/pRef`，因此没有触发 repair。它证明
浮力题主流程和压力参考契约可以一次通过；MPI rank 路径清洗的精确 repair 分支仍由确定性回归覆盖，
本次真实算例没有直接触发该分支。

### 6.3 `pimple-blocked-channel`

首次定向复测的证据如下：

阶段归一化生效，`postProcess` 不再造成 static inspection failure，`pimpleFoam` 在两个 attempt
中都实际启动。第一次 solver failure 是 scalarTransport function object 缺少必需的
`diffusion`；scoped repair 正确增加 `diffusion constant`。第二次进入 time loop 后发生 SIGFPE。

根据 stack trace、Foundation v10 `volumeFractionSource` 实现和冻结评测后的官方 example 审计，
直接原因是 Agent 把 `alpha.volume` 在自由流区初始化为 1。该模型内部包含
`alpha/(1-alpha)`，因此在 `alpha=1` 处除零。官方语义是障碍占据体积分数：自由流区应为 0，
并且全场必须严格小于 1。该失败属于 case/Knowledge 能力缺口，不是 stage normalizer、Runner、
bubblewrap 或 evaluator 阻断。

轻量修正后的全新 cold-path 复测已通过：

- Knowledge 与 Skill 明确 `alpha.<volumePhase>` 是障碍物占据比例，自由流为 0，全域严格小于 1；
- Maxwell 专用 solver contract 只在任务出现明确 Maxwell/viscoelastic 证据时激活；
- 通用瞬态不可压契约补齐 Foundation v10 `model Stokes` 与 scalarTransport `diffusion` 语义；
- solver-family 状态校验接受 `incompressible-transient PIMPLE` 这类包含路由族标识的扩展描述，但仍拒绝 token 不相容的真实冲突。

最终 run 为
`/tmp/foampilot-pimple-volume-fraction-fix-20260806-v3/run-20260806T145627612349Z-e0fba6fc`：

- 单次模型请求，无 retry、无 repair；
- 模型生成 283.440 s，目标 solver 6.726 s；
- `blockMesh`、`checkMesh`、`postProcess` 初始化和 `pimpleFoam` 全部返回 0；
- `pimpleFoam` 正常到达 0.03 s，U、p 和 tracer 保持有限，7 项公开验证全部通过；
- 产物 manifest 完整性问题为 0。

## 7. 复测发现的架构缺口

对首次超时的 qualification run 执行公开 `foampilot resume` 时，strict compatibility 发现
`backend_policy_sha256` 不一致：qualification parent 使用 pinned qualification gateway，而当前
resume CLI 创建 normal gateway。完整性检查的拒绝是正确的，但公开 CLI 目前无法严格续跑
qualification parent。为避免绕过证据边界，本轮采用同 policy 的独立 qualification rerun。

该问题应作为后续轻量 CLI 修复项，不应通过关闭 fingerprint 校验解决。

## 8. 尚未执行

- 30 题全量重跑；
- `cht-cooling-cylinder` 超大日志性能定向复测；
- 20 道额外官方算例串行测试；
