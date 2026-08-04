# 30 题官方题库衍生算例基线与受控学习报告

## 结论

`official-corpus-30-baseline-v1` 已在 Foundation OpenFOAM v10 上完整执行 30 个算例。
模型从空 case 目录编写文件，目标 tutorial、evaluator rule 与 reference 未提供给 authoring
或 repair。

本轮最重要的结果不是 30 题全部通过，而是长批工作流可以连续运行：

- 30/30 完成模型生成；
- 28/30 启动目标求解器，目标求解器进入率 93.3%；
- 20/30 求解器正常结束；
- 18/30 通过公开验证；
- 17/30 获得 suite `PASS`；
- 原 15 题严格集合中 10/15 通过物理 qualification；
- provider deferred 为 0，environment blocked 为 0；
- 30/30 artifact manifest 校验无缺失、额外文件或哈希不一致。

因此，FoamPilot 当前已经具备对多 solver-family 进行长批盲编写与原生求解的能力，且不会因
bubblewrap 探测失败而在内部无界等待。但它还不是高准确率的通用 CFD 自动建模系统：case
字典完整性、复杂网格和可压缩/多区域数值稳定性仍是主要能力缺口。

## 评测口径

30 题由两部分组成：

- 15 个 `physics_qualification` 算例：公开检查通过后，还要与 evaluator-only 紧凑派生
  reference 比较；
- 15 个 `public_validation` 广度算例：用于检验新 solver-family 的生成、网格、初始化、
  目标求解器进入和公开结果检查，不宣称已经完成严格 golden 物理比较。

后 15 题扩展了层流、PIMPLE/PISO、多孔介质、浅水、电静力、固体力学、可压缩激波、浮力、
CHT 和两液混合等场景。它们不是逐题 renderer，也不会让模型复制现有 example。

基线配置为：

```text
protocol: official-corpus-30-baseline-v1
model: gpt-5.6-sol
suite workers: 1
OpenFOAM: Foundation v10
run root: /tmp/foampilot-official-corpus-30-baseline-20260803-v1
```

## 总体指标

| 指标 | 结果 |
| --- | ---: |
| task count | 30 |
| generation success | 30 |
| native execution started | 28 |
| mesh generation pass | 28 |
| checkMesh pass | 28 |
| target solver started | 28 |
| solver normal completion | 20 |
| public validation pass | 18 |
| strict physics qualification pass | 10/15 |
| suite PASS | 17 |
| suite FAIL_AGENT | 13 |
| provider deferred | 0 |
| blocked environment | 0 |
| logical model requests | 47 |
| transport attempts | 51 |

30 题累计墙钟约 5503.2 秒。模型阶段约 3989.9 秒，OpenFOAM 命令约 1403.3 秒；其余为
环境发现、路由、物化、检查、评测与落盘。单题墙钟中位数约 147.0 秒，P90 约 236.0 秒；
28 个进入原生执行的算例，从 run 开始到首个 OpenFOAM command 的中位数约 130.5 秒，
P90 约 164.6 秒。

51 次真实传输中有 4 次短暂 `PROVIDER_NETWORK_UNAVAILABLE`。ModelGateway 在同一逻辑
请求内完成重试，所有请求最终恢复，没有把任何题标为 `DEFERRED_PROVIDER`。

## 逐题结果

| 算例 | 级别 | 目标 solver | 正常结束 | 结果 |
| --- | --- | ---: | ---: | --- |
| laminar-cavity | strict | 是 | 是 | PASS |
| potential-cylinder | strict | 是 | 是 | PASS |
| rans-pitzdaily | strict | 是 | 是 | 公开通过，压力指标未过 |
| multiphase-dam-break | strict | 是 | 是 | alpha boundedness 未过 |
| compressible-shock-tube | strict | 是 | 否 | FPE |
| buoyant-cavity | strict | 是 | 是 | PASS |
| scalar-transport-pitzdaily | strict | 是 | 是 | PASS |
| laminar-planar-poiseuille | strict | 是 | 是 | PASS |
| porous-angled-duct | strict | 是 | 是 | PASS |
| compressible-blocked-channel | strict | 是 | 是 | PASS |
| cht-cooling-cylinder | strict | 是 | 否 | solid 温度失稳 |
| srf-rotor | strict | 是 | 是 | PASS |
| mhd-hartmann | strict | 是 | 是 | PASS |
| multiphase-capillary-rise | strict | 是 | 是 | PASS |
| solid-plate-hole | strict | 否 | 否 | solver output alias 被误拦 |
| laminar-cavity-graded | public | 是 | 是 | PASS |
| laminar-cavity-clipped | public | 是 | 是 | PASS |
| laminar-planar-couette | public | 是 | 否 | FPE |
| laminar-planar-contraction | public | 是 | 是 | PASS |
| pimple-blocked-channel | public | 否 | 否 | solver output alias 被误拦 |
| piso-porous-blockage | public | 是 | 是 | PASS |
| shallow-water-square-bump | public | 是 | 是 | PASS |
| electrostatic-charged-wire | public | 是 | 是 | PASS |
| solid-beam-end-load | public | 是 | 否 | solid Cp 字典缺失 |
| rhopimple-shock-tube | public | 是 | 是 | PASS |
| rhocentral-oblique-shock | public | 是 | 否 | 缺少 div(tauMC) |
| rhocentral-forward-step | public | 是 | 否 | FPE |
| buoyant-benard-cells | public | 是 | 否 | 缺少 rhoFinal |
| buoyant-hot-room-boussinesq | public | 是 | 否 | thermo temperature inversion 失败 |
| two-liquid-lock-exchange | public | 是 | 是 | phase boundedness 未过 |

## 失败分层

### 工作流误拦

两个算例把 solver 写出字段声明为描述性 manifest alias，例如 `D-final -> 100/D` 或
`U.0.03 -> 0.03/U`。旧规则强制 `field.name == path.basename`，但该关系对尚未生成的
solver output 不影响 OpenFOAM 安全性或可执行性，最终导致 2 题在求解前停止。

这是一项检查器缺陷，不应通过给模型增加题目知识来规避。

### 求解完成但公开检查未过

- dam break 的 `alpha.water` 最小值约 `-1.44e-5`，小于公开下界；液体体积误差仍低于 0.2%；
- two-liquid lock exchange 的相分数最大值约 `1.00000143`，略高于上界。

这些题已正常求解，问题属于 VOF 有界性/公开容差，不是工作流进入失败。本轮没有放宽
evaluator 容差。

### 严格物理指标未过

`rans-pitzdaily` 正常结束并通过公开验证，但 pressure-change 相对误差约 0.134，高于 0.1
限制。因此公开通过数比 suite `PASS` 多 1。

### 字典契约或数值稳定失败

其余失败均已进入目标 solver。可直接复用的根因包括：

- `solidEquilibriumDisplacementFoam` 直接读取顶层 `Cp`、`kappa`、`E`、`nu`、`alphav`
  属性，通用 mixture thermo 不能替代；
- `rhoCentralFoam` 在 `divSchemes/default none` 时需要 `div(tauMC)`；
- `buoyantFoam` 最终 pressure correction 会查找 `rhoFinal`；
- CHT solid diffusion number 接近 200 时，能量到温度反演出现负温度；
- 可压缩激波与 Couette/forward-step 中还存在初始/边界条件或时间步导致的 FPE。

## bubblewrap 与长批运行

全量基线的 148 个 native step 都记录为 bubblewrap 后端并正常返回或在 typed timeout/solver
退出时结束，没有出现内部权限等待。原因是该 suite 在允许 namespace 的宿主执行环境中运行。

在不允许创建 network namespace 的嵌套环境中，真实 preflight 曾得到：

```text
bwrap: loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted
```

现在 `execution_backend=auto` 只探测一次并缓存结果；bubblewrap 不可用时，该检查为非阻断，
Runner 自动选择有 executable allowlist、cwd、资源限制和日志记录的 audited host 后端。独立
`blockMesh -> checkMesh -> icoFoam` gate 已验证 host fallback。需要明确的是，host 后端不提供
bubblewrap 的 network namespace 隔离。

外层 Codex/托管执行器仍可能在启动拥有模型网络权限的整个长批命令之前等待宿主授权。这不在
FoamPilot 状态机内部，也不会显示为 bubblewrap step；本地终端运行或预授权稳定的 CLI 前缀
可以消除这类启动等待。报告必须把外层工具授权与 FoamPilot runtime failure 分开。

## 受控学习与定向复测

基线后只实施了小范围改动：

1. solver 创建的输出允许使用描述性 manifest alias，region/path 检查仍保留；
2. solid 契约补充顶层 `Cp` 等属性结构；
3. rhoCentral 契约补充 `div(tauMC)`、守恒/派生场 solver，以及
   `waveTransmissive` 的 `gamma`、`fieldInf` 语义；
4. buoyant 契约补充 `rhoFinal`；
5. CHT 契约要求未经尺度证据时从 `maxDi <= 1` 开始；
6. 生成 prompt 要求落实已经路由且适用的 `content.rules`，但不增加模型审查阶段。

6 题定向复测获得 3 个 `PASS`：

- solid plate hole 从静态误拦变为一次严格通过；
- solid beam 从 `Cp undefined` 变为一次公开通过；
- Bénard cells 从 `rhoFinal undefined` 变为一次公开通过。

另外 3 题都进入了 OpenFOAM 原生执行，但未通过：

- pimple blocked channel 越过原静态误报后，新生成的 blockMesh arc 语法错误；
- CHT 新生成多块网格的相邻 block face 数不一致，未能验证新 maxDi；该次生成仍写了
  `maxDi 200`，说明仅有知识文本还不能保证模型遵循；
- rhoCentral 经过两轮通用契约补充后，字典缺项被逐步消除，最终失败由 IO error 前移到
  solver FPE，后续应作为可压缩数值稳定专题处理。

定向复测证明若干修正有效，但不能替代新的 30 题同快照全量运行。

## 结论边界

- 30/30 generation 与 0 environment/provider terminal blocker 证明长批工作流可以持续推进；
- 28/30 目标求解器进入率证明当前流程已基本达到演示所需的“快速、稳定进入求解”目标；
- 20/30 正常结束和 17/30 suite PASS 表明准确性仍需继续提升；
- 新增 15 题只有公开验证级别，不能表述为 30 题全部具备 golden 物理 qualification；
- 模型生成仍具有随机性，同一 TaskSpec 的下一次结果可能在网格或字典细节上不同；
- 下一阶段优先级应是通用 blockMesh 几何一致性、rhoCentral 初始/边界稳定性和 CHT
  time-step/mesh 契约，不应增加更多审批、renderer 或逐题硬编码。

## 产物

基线机器报告保存在：

```text
/tmp/foampilot-official-corpus-30-baseline-20260803-v1/
  official-corpus-30-baseline-v1-report.json
  official-corpus-30-baseline-v1-report.md
```

定向复测报告保存在：

```text
/tmp/foampilot-targeted-learning-6-20260803-v1/
  targeted-learning-6-v1-report.json
  targeted-learning-6-v1-report.md
```

这些运行产物不纳入 Git；仓库只保存可复现 suite、TaskSpec、通用知识、Skills、代码和本报告。
