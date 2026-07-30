# FoamPilot 新增 10 个官方场景串行复测与受控学习报告

- 评测日期：2026-07-30
- OpenFOAM：Foundation OpenFOAM v10
- 模型：`gpt-5.6-sol`
- 执行策略：题目严格串行；仅在单题执行计划内使用 2 或 4 个 MPI ranks
- 代码状态：本地 `main` 加本报告所述未提交改动

## 结论

本轮使用相同的 10 个公开 TaskSpec 重新生成 OpenFOAM case，没有读取或
复制目标 tutorial。按每题最近一次有效 OpenFOAM 尝试统计：

- 6/10 取得 `PUBLIC_VALIDATION_PASS`；
- 通过题为 decompression tank、two-liquid lock exchange、porous
  blockage、solid beam、cyclic pipe 和 SRF mixer；
- 其余 4 题均有先前有效失败证据，但最终 v5 重试被远端模型服务
  `server_is_overloaded` 阻断，没有形成可用于验证最新修正的新 case；
- 六个通过 run 的 artifact manifest 均已重新校验，`manifest_issues`
  全部为空；
- 222 个源码测试通过，2 个条件测试跳过；36 条知识清单匹配；主机
  preflight 通过。

因此，本报告是“6/10 当前复测通过、4/10 未完成最终资格验证”，不是
10/10 通过声明。历史上 charged wire 曾有冻结 run 通过，但新一轮重新
生成的有效尝试没有通过，故不计入本轮 6/10。

## 逐题结果

| # | 场景 | 求解器 | 本轮判定 | 最强复测证据 |
| --- | --- | --- | --- | --- |
| 1 | Decompression tank | `rhoPimpleFoam` | PASS | v2 首次生成通过全部公开检查 |
| 2 | Charged wire | `electrostaticFoam` | 未通过 | v2 已生成并到达原生前处理，二维退化块/边界面不匹配；v5 为 0 次有效尝试的服务过载 |
| 3 | Two-liquid lock exchange | `twoLiquidMixingFoam` | PASS | v2 首次生成，2-rank MPI 求解和重构通过 |
| 4 | Porous blockage | `pisoFoam` | PASS | v5 首次生成通过；正确加载 `explicitPorositySourceCoeffs` 和 cellZone |
| 5 | Bénard cells | `buoyantFoam` | 未完成资格验证 | v4 网格通过并启动求解，因 `thermophysicalTransport` 层级错误退出；规则已修正，v5 无有效尝试 |
| 6 | Blocked channel | `pimpleFoam` | 未完成资格验证 | v4 完整生成，被静态检查错误要求 `.inc` 片段含 `FoamFile` 头；检查器已修正，v5 无有效尝试 |
| 7 | Beam end load | `solidEquilibriumDisplacementFoam` | PASS | v2 首次生成通过全部公开检查 |
| 8 | Cyclic pipe | `simpleFoam` | PASS | v5 首次生成通过，普通 `checkMesh` 路径不再被额外严格标志误阻断 |
| 9 | SRF annular mixer | `SRFSimpleFoam` | PASS | v2 首次生成，4-rank MPI 求解和重构通过 |
| 10 | Compressible square bend | `rhoSimpleFoam` | 未通过 | v4 构造 112,000-cell `Mesh OK` 网格、完成分解并启动 4-rank 求解，早期数值发散；保守启动规则已补，v5 无有效尝试 |

## 串行执行与产物

TaskSpec：

`/tmp/foampilot-extended-10-20260730/tasks`

主要复测目录：

- v2 全量串行：`/tmp/foampilot-extended-10-20260730/retest-10-serial-v2`
- v3 定向复测：`/tmp/foampilot-extended-10-20260730/retest-10-serial-v3-targeted`
- v4 定向复测：`/tmp/foampilot-extended-10-20260730/retest-10-serial-v4-targeted`
- v5 最终未决题复测：`/tmp/foampilot-extended-10-20260730/retest-10-serial-v5-final`

最终测试 wheel：

`/tmp/foampilot-extended-10-20260730/dist-retest-serial-v5/foampilot-0.1.0-py3-none-any.whl`

SHA-256：

`99dc7c28851e5024276f9ca725ce05fb765eb9c725cbfd3b03b138ac06500c6e`

每道题只在前一道 CLI 返回结构化终态后启动。MPI 只出现在题内
`execution-plan.json`，没有并发启动两个题目。模型端过载是本轮主要
耗时来源，不能与 OpenFOAM 求解失败混为一类。

## 六个通过 run

| Task | Run |
| --- | --- |
| `compressible-decompression-tank` | `retest-10-serial-v2/run-20260730T085744975211Z-919a122e` |
| `two-liquid-lock-exchange` | `retest-10-serial-v2/run-20260730T090616844163Z-2415e87d` |
| `solid-beam-end-load` | `retest-10-serial-v2/run-20260730T092310254355Z-2686386f` |
| `srf-annular-mixer` | `retest-10-serial-v2/run-20260730T092919236469Z-4b88776e` |
| `incompressible-porous-blockage` | `retest-10-serial-v5-final/run-20260730T101704313504Z-729d54ef` |
| `rans-cyclic-pipe` | `retest-10-serial-v5-final/run-20260730T102756560847Z-09fadae9` |

对以上六个目录重新执行 `foampilot report RUN_DIR --json`，状态均为
`PUBLIC_VALIDATION_PASS`，且 `manifest_issues` 均为空。

## 本轮学习与轻量改动

### 模型传输可观测性

- Agent prompt 中的环境快照由完整路径和命令对象压缩为版本、可执行
  程序名、MPI/Gmsh 可用性和 rank 上限；代表性 prompt 从 62,279
  字符降到 25,635 字符。
- 模型重试退避调整为 5、15、45、90 秒。
- SSE error 事件保留服务端错误码和消息。最终错误明确显示
  `server_is_overloaded`，不再被泛化成无信息的 transport failure。

### 通用 OpenFOAM 契约

- `electrostaticFoam`：dimensionedScalar 的内部重复名称，以及 `phi`
  和 `rho` 的字段量纲。
- 二维 blockMesh：每个 boundary face 必须对应完整 block cell face；
  退化 hex 的点合并必须一致。
- `twoLiquidMixingFoam`：相分数场即使名为 `alpha.<phase>`，求解器查找
  的对流项仍是 `div(phi,alpha)`。
- Foundation v10 区域初始化：原生 `setFields` 不接受
  `-time constant`。
- `pisoFoam`/`pimpleFoam` 显式多孔阻力：`cellZone`、Darcy-Forchheimer
  系数和坐标系必须位于 `explicitPorositySourceCoeffs`。
- laminar 热输运：`thermophysicalTransport` 顶层使用
  `laminar { model Fourier; }`。
- `rhoSimpleFoam`：压力限制使用 `minFactor/maxFactor`；高负荷压缩稳态
  启动优先 bounded upwind，并采用已验证的稳健松弛基线，再考虑高阶。

### 流程与评测器修正

- 默认只运行普通 `checkMesh`；只有任务明确要求时才增加严格几何标志。
- 静态检查不再把 `.inc` include 片段当成独立 OpenFOAM 字典，避免
  强制不存在的 `FoamFile` 头。
- 日志进度解析同时支持 `Time =`、`Iteration =` 和 `Iteration:`。
- final-time 与请求输出目录使用浮点容差解析，避免把
  `9.99999999996` 和 `10` 判成不同状态。

这些改动没有新增题目专用评测器、固定 golden、MCP 或新的主流程分支。

## 失败证据与边界

### Charged wire

最新有效复测中，物性文件的内部名称问题已经消失，但 Agent 构造的
退化块和 front/back boundary face 不匹配，`blockMesh` 失败。二维
几何契约已补齐，但 v5 最终请求没有获得模型响应，因此不能声明已修复。

### Bénard cells

v4 的 `blockMesh` 与 `checkMesh` 成功，`buoyantFoam` 启动后报：

`keyword laminar is undefined in dictionary constant/thermophysicalTransport`

Agent 写成了通用包装字典；Foundation v10 的 run-time selector 需要
顶层 `laminar` 子字典。知识规则和回归测试已更新，真实 v5 复测未形成
case。

### Blocked channel

v4 的 case 使用 `.inc` 片段表达较大的非均匀体积分数字段。旧检查器
对 13 个片段报告 `MISSING_FOAM_HEADER`，这是流程误报，不是 OpenFOAM
语法错误。检查器现把 `.inc` 视为 include 片段，红绿回归测试通过；
由于 v5 无有效模型响应，本题仍不能计为通过。

### Compressible square bend

v4 已证明执行闭环能够：

- 构造 112,000-cell 三维网格；
- `checkMesh` 报告 `Mesh OK`；
- 完成 `decomposePar`；
- 启动 4-rank `rhoSimpleFoam`。

求解在第 2 个稳态迭代附近出现压力线性系统 FPE。Agent 已正确写出
`minFactor/maxFactor`、`rhoInlet`、`profile turbulentBL`、`transonic`
和 `consistent`，但同时采用 linearUpwind/limitedLinear 与较弱松弛，
偏离稳健的保守启动组合。通用知识已补齐，v5 没有形成新 case。

## 自动化验证

最终源码状态的全量验证输出：

- `pytest -q`：222 passed，2 skipped；
- `foampilot knowledge validate ... --json`：36 entries，0 issues，PASS；
- `foampilot preflight --json`：Python、OpenFOAM-10、tutorial root、
  bubblewrap 网络隔离启动和 `icoFoam` 全部通过；
- 六个通过 run 的 artifact manifest：0 issues；
- v5 wheel：构建成功。

跳过项是仓库既有条件测试，不是本轮新增失败。

## 后续最小动作

当模型服务稳定时，只需按顺序复测 charged wire、Bénard cells、
blocked channel 和 compressible square bend。无需改变当前

`公开 TaskSpec → 动态知识检索 → Agent 完整写 case → 安全执行 → 公共验证 → 一次最小修复`

主流程。只有这四题取得新的 `PUBLIC_VALIDATION_PASS` 后，才能把当前
6/10 更新为更高通过率。
