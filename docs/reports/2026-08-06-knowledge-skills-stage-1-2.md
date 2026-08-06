# Knowledge/Skills 阶段 1.2 实施与验证报告

日期：2026-08-06
状态：实施中

设计：[Knowledge 与 Skills 优化规格](../design/knowledge-skills-design.md)
计划：[Knowledge/Skills 阶段 1.2 实施计划](../plans/2026-08-06-knowledge-skills-stage-1-2.md)

## 1. 证据边界

本轮只使用冻结 run、公开 OpenFOAM 日志、正式 Knowledge 和 Foundation v10 公开源码。
四个失败均已选中正确 solver guide，但未加载 family Skill。改进目标是 Skill 路由与知识遵从，
不是 Runner、Gateway、evaluator 或新增逐题知识。

当前冻结基线为：既有 30 题 `30/30` 完成 generation、`28/30` 启动目标 solver、`23/30`
正常结束、`21/30` 通过 public validation；新增 20 题有效视图为 `19/20` generation、`19/20`
目标 solver 启动、`11/20` 正常结束并通过 public validation。两批环境/bubblewrap 阻断均为零。

## 2. RED：Knowledge 已选中但 family Skill 缺失

| solver | 已选 Knowledge | attempt-02 最早失败 |
| --- | --- | --- |
| `driftFluxFoam` | `of10.solver.driftfluxfoam-contract` | `div(tauDm)` 缺失 |
| `multiphaseEulerFoam` | `of10.solver.multiphaseeulerfoam-contract` | `phaseTransfer` table 缺失 |
| `reactingFoam` | `of10.solver.reactingfoam-contract` | 错写 `unityLewis` |
| `compressibleInterFoam` | `of10.solver.compressibleinterfoam-contract` | `alpha.liquid` solver entry 缺失 |

四个 `agent-context.json` 都只加载 `openfoam-author-native-case`。当前正式 solver guide 已经包含
上述失败对应的 reader contract，因此本轮先修复 family Skill 选择和成组知识遵从，不重复增加
相同 Knowledge。

映射、上下文、package 和 Skill validator 的 RED 聚焦组结果为：`10 failed, 28 passed`。10 个
失败全部对应四个缺失映射或尚不存在的 `openfoam-multiphase-coupled`，既有样本没有回归。

## 3. GREEN：family Skill 路由与原子清单

- `compressibleInterFoam` 选择 `openfoam-multiphase-vof`；
- `driftFluxFoam` 和 `multiphaseEulerFoam` 选择新的
  `openfoam-multiphase-coupled`；
- `reactingFoam` 选择 `openfoam-compressible-transient`；
- 通用 Skill 将 selected solver guide 中明确列出的 reader contract 转为生成前原子清单；
- VOF 和可压缩 Skill 分别增加可压缩 VOF 与反应流分支。

GREEN 聚焦组为 `63 passed`；四个相关 Skill validator 均为 `PASS`。正常 authoring 仍只发起
原有的一次完整 case generation，没有增加 reviewer 或额外模型请求。

## 4. 确定性与交付包验证

- Knowledge、上下文、Skill、leakage 和 protected-path 聚焦组：`119 passed`；
- wheel：`/tmp/foampilot-stage-1-2-wheel-HCNobi/foampilot-0.1.0-py3-none-any.whl`；
- wheel SHA-256：`60c7f312bb8432de09291456347f20f5df02a24fe1a3a8ea3db2fa20fc739fb8`；
- wheel 已确认包含 `openfoam-multiphase-coupled/SKILL.md` 与 `agents/openai.yaml`。

第一次聚焦验证命令引用了仓库中不存在的两个测试文件，因此 pytest 在收集前退出、没有运行测试。
更正为当前真实的 Knowledge model、TaskSpec、AgentContext 和 CLI leakage 测试后得到上述
`119 passed`。该命令错误没有被计为实现回归。

## 5. v1 四题真实 forward gate

Run root：`/tmp/foampilot-knowledge-skills-1-2-forward-20260806-v1`

| solver | generation | 目标 solver | normal/public | attempt-02 首个失败 |
| --- | --- | --- | --- | --- |
| `driftFluxFoam` | PASS | STARTED | FAIL/FAIL | alpha solver 缺少 `nLimiterIter` |
| `multiphaseEulerFoam` | PASS | STARTED | FAIL/FAIL | `div(phi,alpha.air)` 未匹配 |
| `reactingFoam` | PASS | STARTED | FAIL/FAIL | `chemistryProperties` 看不到 `reactions` 子字典 |
| `compressibleInterFoam` | PASS | STARTED | FAIL/FAIL | 缺少逐相 `nuEff` 黏性应力 operator |

四题都选择了预期 solver guide 和唯一 family Skill，protected path 未进入上下文；四题均生成、
通过网格/初始化阶段并启动目标 solver。上一轮的 `div(tauDm)`、`phaseTransfer`、`unityLewis` 和
alpha base entry 四个精确错误均未重现，但 reader 在更深一层暴露了仍未原子化的同族契约。
因此 v1 证明路由有效，不能证明 family authoring 已通过。

`driftFluxFoam` 的首次 model generation 在外层受限执行环境中无法初始化 `codex app-server`，
随后从 generation checkpoint 续跑成功；正式四题没有 FoamPilot Runner/bubblewrap blocker。

## 6. reader/source 根因与最小精修

从 Foundation v10 reader/source 和公开 family dictionary 得到以下确定事实：

- drift-flux `general` reader 将 `Vc` 按时间维读取；CMULES 对 `nLimiterIter` 使用必填 lookup；
- Euler-Euler 相分数通量名称由实际相名构造成 `div(phi,alpha.<phase>)`；含括号 key 的 regex
  必须转义字面括号，或直接逐相写精确 key；
- ReactionList 在当前 `chemistryProperties` 字典内读取 `reactions` 子字典，独立文件必须通过
  include 接入；`chemistryType.method` 缺省即为 `chemistryModel`；
- 可压缩 VOF 的 alpha entry 要覆盖 base/Final，黏弹性/层流分支会按实际相名与模型构造完整
  `nuEff`/`dev2(T(grad(U)))` 应力 operator。

这些事实形成 7 个新的 RED 断言，修改前为 `7 failed`；最小更新四个 solver guide 和三个
family Skill 后为 `7 passed`。更大的 Knowledge/Context/Skills 聚焦组为 `113 passed`，四个
相关 Skill validator 均为 `PASS`，`git diff --check` 通过。

一次组合验证命令误用了不存在的 `tests/test_knowledge_validation.py` 和 Python 模块入口，未收集
测试。更正为仓库真实测试文件和 `foampilot skill validate` 后取得上述结果；这仍记为验证命令
错误，不计作产品回归。

## 7. v2 四题真实 forward gate

Run root：`/tmp/foampilot-knowledge-skills-1-2-forward-20260806-v2`

| solver | generation | 目标 solver | normal/public | v2 结果 |
| --- | --- | --- | --- | --- |
| `driftFluxFoam` | PASS | STARTED | FAIL/FAIL | 分散相物性仍写 `constant`；repair 改错到 `phaseProperties` |
| `multiphaseEulerFoam` | PASS | STARTED | FAIL/FAIL | alpha 精确项已通过；repair 将 `thermo:rho` 简写为 `rho` |
| `reactingFoam` | PASS | STARTED | PASS/PASS | `YiFinal` 经一次定向 repair 后达到 0.01 s，5/5 checks 通过 |
| `compressibleInterFoam` | PASS | STARTED | FAIL/FAIL | 顶层 `sigma` 缺失；正确 repair 因冗余 no-op command 被策略拒绝 |

分层汇总：`4/4` generation、`4/4` Mesh/初始化、`4/4` target solver started、`1/4`
normal completion、`1/4` public validation、`0` environment/bubblewrap blocker。v1 的四个最早错误和
Task 5A 的 `Vc`/`nLimiterIter`、alpha phase key、reaction include、alpha base/Final 错误均未重现。

性能证据中，time-to-first-OpenFOAM 分别约为：drift-flux `200.4 s`、Euler-Euler `297.7 s`、
reacting `418.6 s`、compressible VOF `156.2 s`。网格与首次 solver 启动均小于 1 秒量级，
cold authoring 仍是求解前的主要耗时。

## 8. v2 根因与最后一轮 Knowledge-only 精修

- drift-flux mixture viscosity 实际从第一/分散相 `physicalProperties.<phase>` 的
  `viscosityModel` 读取，不从 `phaseProperties` 读取独立 selector；
- Euler-Euler 层流黏性项使用 `thermo:rho.<phase>`、`nuEff.<phase>` 和 `U.<phase>` 的完整
  grouped operator，不能把它简写成 `rho.<phase>`；
- reactingFoam 用统一 selector `Yi` 求解各 active species，并在 final corrector 请求
  `YiFinal`；
- compressibleInterFoam 的 constant surface-tension reader 需要 `phaseProperties` 顶层
  `sigma`；嵌套在自创子字典中的同名值不满足 contract。

上述四项形成第二组 7 个 RED，修改前为 `7 failed`，最小更新后为 `7 passed`；更大聚焦组
保持 `113 passed`，四个 Skill validator 均为 `PASS`。v3 将是最后一轮 Knowledge-only cold
gate；若仍出现逐关键词 reader 链，后续转入已批准的架构 P0/P1，不再继续堆知识文本。

## 9. v3 最终 Knowledge-only forward gate

Run root：`/tmp/foampilot-knowledge-skills-1-2-forward-20260806-v3`

| solver | generation/恢复 | 目标 solver | normal/public | 结果 |
| --- | --- | --- | --- | --- |
| `driftFluxFoam` | cold one-shot | STARTED | PASS/PASS | 首次直接达到 20 s，无 repair |
| `multiphaseEulerFoam` | cold one-shot | STARTED | PASS/PASS | 首次直接达到 2 s，无 repair |
| `reactingFoam` | cold + repair | STARTED | FAIL/FAIL | `YiFinal` 已解决；错误 transport 层级，repair 预算先被 include header 检查消耗 |
| `compressibleInterFoam` | parent timeout + child continuation | STARTED | PASS/PASS | child 修正 `p_rgh` 的 `rho thermo:rho` 后达到 0.5 s |

按 lineage 有效视图统计：`4/4` generation、`4/4` target solver started、`3/4` normal
completion、`3/4` public validation、`0` OpenFOAM environment/bubblewrap blocker。存在 `1` 个 backend
timeout parent，但它被不可变 child continuation 恢复，未被误记为 case/solver failure。

v3 time-to-first-OpenFOAM：drift-flux `228.9 s`、Euler-Euler `142.9 s`、reacting `359.9 s`
（首次静态失败后 repair 才执行 OpenFOAM）、compressible VOF child `358.4 s`；后者另有一个未产生
native attempt 的 timeout parent。实际 solver 时间分别约 `10.8 s`、`64.7 s`、`0.2 s`（失败）和
child lineage `122.7 s`。cold model authoring 仍是进入求解前的主要耗时。

## 10. 阶段结论与 P0/P1 冻结输入

本阶段证明了 solver guide + 唯一 family Skill 的动态路由能够改变全新生成行为：drift-flux 和
Euler-Euler 从连续 reader failure 提升为 one-shot public pass；compressible VOF 在 backend
timeout 后可通过 child continuation 和一次定向 repair 完成。四个历史最早错误均未重现，正常
authoring 没有增加额外 reviewer/model call。

同时，继续逐关键词扩写的边际收益已经下降。以下冻结 artifact 将作为 Agent Harness v2
P0/P1 的真实 fixtures：

- reacting v3：include fragment 被通用 `MISSING_FOAM_HEADER` 抢先阻断，导致唯一 repair 未用于
  真正的 `thermophysicalTransport` reader failure；
- compressible VOF v2：repair 正确修改 `phaseProperties.sigma`，但冗余提交未变化 command 被
  `NO_OP_REPAIR_COMMAND` 整体拒绝；
- drift-flux v2：repair 识别有效 model，却修改错误文件，说明 repair scope 缺少 reader-to-file
  定位；
- Euler-Euler v2：repair 展开 operator 时丢失 `thermo:rho` grouped name，说明 patch 需要更清晰
  的 evidence-bound scope；
- compressible VOF v3 parent/child：backend timeout、`native_status=null`、generation continuation
  和最终完成的完整 lineage；
- v3 四题 cold latency：用于状态快照、阶段耗时和 verified-plan warm path 的对照。

按最新确认的总体顺序，既有 30+20 大回归不在这里冒充完成；它延期到 P0/P1 实施后执行。阶段
1.2 收口后进入独立的 P0/P1 实施计划，不提前实现 P2/P3 或 IDE。

阶段切换前全仓确定性验证为 `526 passed, 5 skipped`，`git diff --check` 通过。最终 wheel：
`/tmp/foampilot-stage-1-2-wheel-v3-20260806/foampilot-0.1.0-py3-none-any.whl`，SHA-256 为
`b5743783e7201b6433e03c69e942468e04b301e50b70c1d86f1a9198535e4020`；已核对包含新增 family
Skill、OpenAI metadata 和四个更新后的 solver guide。
