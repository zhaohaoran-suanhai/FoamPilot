# Knowledge/Skills 阶段 1.1 实施与验证报告

日期：2026-08-05
状态：本轮完成
设计：[阶段 1 Knowledge 与 Skills 优化规格](../design/knowledge-skills-design.md#12-阶段-11基于-30-题证据的轻量修正)
计划：[Knowledge/Skills 阶段 1.1 实施计划](../plans/2026-08-05-knowledge-skills-stage-1-1.md)

## 1. 证据边界

本轮只使用已经冻结的 Agent run、公开 OpenFOAM 日志、Foundation OpenFOAM v10 源码、当前
Knowledge/Skills 和独立测试。没有向 authoring 或 repair 模型暴露目标 tutorial、golden value、
私有 evaluator、目标路径或评测 tolerance。

基线来自：

```text
/tmp/foampilot-performance-v1-cold-30-normalized-20260805-v1
```

基线汇总为：29/30 完成 case generation、27/30 启动目标 solver、21/30 solver 正常结束、
20/30 public validation pass；qualification 为 16 `PASS`、13 `FAIL_AGENT`、1
`DEFERRED_BACKEND`。

## 2. 专用 Knowledge 激活

### RED

`of10.physics.volume-fraction-source` 没有 `activation_terms`，只因 solver executable overlap
便进入普通 Maxwell/PIMPLE 和不含固定体积分数模型的 rhoPimple 激波管上下文。新增拒绝测试得到
`2 failed`。

### GREEN

该条目现在只接受具体公开证据：`volume fraction source`、`volumeFractionSource`、
`solidEquilibriumEnergySource` 或 `alpha.volume`。没有使用过宽的 `source`、`alpha`、
`blockage` 或 solver name。

验证结果：

```text
拒绝普通 Maxwell/rhoPimple 样本 + 接受显式 alpha.volume 样本：3 passed
完整 tests/test_knowledge_retrieval.py：28 passed
```

## 3. 多相 RED

- `interFoam`：`fvSolution/solvers/alpha.<phase>` 只有 alpha controls 时，原生日志报告
  `keyword solver is undefined`。
- `twoLiquidMixingFoam`：`phaseProperties` 先后报告 `Dab` 与 `alphatab` 未定义，说明 repair
  在逐关键词追赶 reader。
- 两个失败均发生在 case 字典/字段读取层，改进目标是 Knowledge 与
  `openfoam-multiphase-vof` Skill，不是 Runner、backend 或 evaluator。

### GREEN

- `interFoam` contract 和 Skill 现在要求 `alpha.<phase>` solver entry 同时包含
  `solver`、`smoother`、`tolerance`、`relTol` 与 alpha controls，并显式覆盖
  `pcorr/pcorrFinal`、`p_rgh/p_rghFinal`、`U/UFinal`。
- `twoLiquidMixingFoam` contract 和 Skill 现在按 reader 顺序要求 `phaseProperties` 同时声明
  有量纲的 `Dab` 与无量纲的 `alphatab`。

确定性验证结果：

```text
多相 Knowledge + Skill 聚焦测试：47 passed
openfoam-multiphase-vof skill validate：PASS
```

## 4. 可压缩 RED

- `rhoCentralFoam`：守恒 `(rho|rhoU|rhoE)` 可使用 `diagonal`；派生 `(U|e)` 被配置为
  `diagonal` 时，Foundation v10 将其按 symmetric matrix 拒绝。
- `rhoPimpleFoam`：symmetric pressure matrix 与 `DILU` 组合被 runtime 拒绝。
- 两个 `rhoCentralFoam` repair 修正字典后进入推进，但随后出现 negative initial
  temperature，说明字典兼容和 thermo-positive startup 是两个独立 gate。

### GREEN

- `rhoCentralFoam` contract 与 Skill 现在区分 `(rho|rhoU|rhoE)` 守恒显式场和 `(U|e)`
  派生隐式场；`diagonal` 只用于实际 diagonal 更新，派生 symmetric matrix 使用
  `smoothSolver` 等有效 solver。
- `rhoPimpleFoam` contract 与 Skill 现在按 runtime matrix type 选择 preconditioner：
  symmetric pressure 路径不使用只注册于 asymmetric 路径的 `DILU`。
- 初始 `p/T/rho/energy` 正值一致性与字典 compatibility 成为两个顺序 gate；负温度不会再被
  误当成 linear-solver 错误。

确定性验证结果：

```text
可压缩新增契约聚焦测试：3 passed
完整 Knowledge + Skill 聚焦组：48 passed
openfoam-compressible-transient skill validate：PASS
```

## 5. 浮力 RED

- 冻结的 buoyant run 两次报告 `Maximum number of iterations exceeded`；repair 只修改
  `fvSolution` 后，同一 energy-to-temperature inversion 错误再次出现。
- 只调整 relaxation 或 linear solver 不能证明 thermo inversion 已修复；必须先保存并验证首个
  失败前的参考状态、temperature extrema、energy、`p/rho` 与 thermo package。

### GREEN

- thermo playbook 与 `openfoam-buoyant-cht` Skill 现在要求先验证参考状态、初始/边界
  `p/T/rho/energy` 和 thermo package 可反演，并保存失败前的 temperature extrema；状态 gate
  通过后才调整松弛、时间步或格式。
- 规则不包含题目温度、热源位置、专用 relaxation 值或 Boussinesq case 模板。

确定性验证结果：

```text
浮力新增契约聚焦测试：2 passed
完整 Knowledge + Skill 聚焦组：50 passed
openfoam-buoyant-cht skill validate：PASS
```

## 6. Maxwell/PIMPLE RED

- 冻结 Couette run 中 actual Courant 从小值增长到大于 2，stress residual 先恶化后 FPE；
  repair 只修改 `fvSchemes` 后复现。
- 这表明补齐 operator 后仍需按日志定位首次失稳，并在时间控制、stress convection、outer
  coupling 或 relaxation 中一次只改变一个原因族；TaskSpec 显式固定量不能被静默修改。

### GREEN

- Maxwell contract 与不可压缩 pressure-velocity Skill 现在固定以下正向 repair 顺序：确认模型
  operators/outer coupling，读取 actual Courant 与 sigma stress residual history，定位首次恶化，
  然后只修改时间控制、stress convection、outer coupling 或 relaxation 中一个有证据的原因族。
- TaskSpec 固定的 `deltaT`、物性和边界不可静默改变；若约束不可行，必须报告 constraint
  conflict。

确定性验证结果：

```text
Maxwell 新增契约聚焦测试：2 passed
完整 Knowledge + Skill 聚焦组：52 passed
openfoam-incompressible-pressure-velocity skill validate：PASS
```

## 7. Target 与 holdout 结果

### 多相 target

- `multiphase-dam-break`：初次 authoring 已具有完整 `alpha.water` entry 和 `UFinal`；
  `blockMesh`、`checkMesh`、`setFields`、`interFoam` 全部返回 0，solver 正常运行到 1 s。
  public validation 仅因 `alpha.water` 最小值约 `-4.10e-5` 超出严格 `-1e-6` 下限而失败。
- `two-liquid-lock-exchange`：初次 authoring 同时具有 `Dab` 与 `alphatab`；第一次只缺
  `fvSchemes` 中的黏性散度项，定向 repair 后 `twoLiquidMixingFoam` 返回 0 并运行到 100 s。
  public validation 仅因写出场最大值约 `1.000000119` 高于严格上界 1 而失败。

### 多相 holdout

- `multiphase-capillary-rise`：完整生成后正常通过网格与初始化并启动 `interFoam`，没有出现
  `alpha.water` solver-entry reader 错误；强表面张力驱动下，第三个极小时间步附近出现相分数
  过冲并以 FPE 结束，repair 后仍发散。该结果证明启动契约可迁移，但不证明数值稳定性通过。

对应 run：

```text
/tmp/foampilot-knowledge-skills-1-1/multiphase-dam-break-v2/run-20260805T135129424768Z-932eadd8
/tmp/foampilot-knowledge-skills-1-1/two-liquid-lock-exchange/run-20260805T135849235179Z-f6c84e6f
/tmp/foampilot-knowledge-skills-1-1/multiphase-capillary-rise-holdout/run-20260805T140439913429Z-e232e279
```

三个 run 的 artifact manifest 均通过验证；没有 bubblewrap/权限阻断。

### Maxwell/PIMPLE target 与 holdout

- `laminar-planar-couette` target：初始 plan 已完整包含 Maxwell operators、`vanAlbada` stress
  convection、`(U|sigma)` coverage、3 个 outer correctors，并保持 TaskSpec 固定
  `deltaT=0.005`。首次 cyclic translation vector 符号错误被 mesh-scoped repair 修正；随后
  `pimpleFoam` 正常到 25 s，public validation `PASS`。
- `laminar-planar-poiseuille` holdout：首次 authoring 与执行一次通过，Maxwell `sigma` 与体力源
  保持完整，solver 正常到 25 s，public validation `PASS`。
- `laminar-planar-contraction` holdout：conformal multi-block mesh、完整 Maxwell operators、
  `vanAlbada` stress convection、`(U|sigma)` coverage 与 4 个 outer correctors 一次生成正确；
  solver 正常到 0.25 s，public validation `PASS`。

对应 run：

```text
/tmp/foampilot-knowledge-skills-1-1/laminar-planar-couette/run-20260805T150718913018Z-4e2899ab
/tmp/foampilot-knowledge-skills-1-1/laminar-planar-poiseuille-holdout/run-20260805T151448049485Z-a1f5c37a
/tmp/foampilot-knowledge-skills-1-1/laminar-planar-contraction-holdout/run-20260805T151820453802Z-918592f3
```

三个 run 的 artifact manifest 均通过验证；没有 bubblewrap/权限阻断。

### 可压缩 target 与 holdout

- `rhopimple-shock-tube`：首次 plan 已为 symmetric pressure 使用 `PCG+DIC`，`DILU` 仅用于
  asymmetric `U/e`；一次 `timePrecision` repair 后 solver 正常运行到 0.007 s，public
  validation `PASS`。
- `rhocentral-oblique-shock`：初始 plan 正确使用守恒场 `diagonal`、派生场
  `smoothSolver`；修复 `waveTransmissive` 向量语法后推进到约 0.878 s，随后因
  `T0=-0.048` 结束。矩阵 compatibility 已通过，剩余为 thermo/数值稳定性失败。
- `rhocentral-forward-step`：同样正确分组 solver，solver 启动并推进；降低 Courant 前后均在
  早期出现负温度，未回退到 matrix runtime-list 错误。
- `compressible-shock-tube` holdout：首次 authoring 与执行一次通过，运行到 0.007 s；公开
  质量守恒归一化误差为 0，public validation `PASS`。
- `compressible-blocked-channel` holdout：第一次生成用时约 265 s 后因同 region 重复 field
  identity 被 ExecutionPlan schema 拒绝；限定的一次复跑中，专用 volume-fraction Knowledge
  正确激活，repair 只纠正 `postProcess` command stage，随后运行到 0.03 s 并 public
  validation `PASS`。第一次结构失败作为模型输出稳定性缺口保留。

对应 run：

```text
/tmp/foampilot-knowledge-skills-1-1/rhopimple-shock-tube/run-20260805T141231498330Z-0fbcf0d2
/tmp/foampilot-knowledge-skills-1-1/rhocentral-oblique-shock/run-20260805T141710607402Z-649ff066
/tmp/foampilot-knowledge-skills-1-1/rhocentral-forward-step/run-20260805T142258099696Z-7e00d641
/tmp/foampilot-knowledge-skills-1-1/compressible-shock-tube-holdout/run-20260805T142732078643Z-8527859f
/tmp/foampilot-knowledge-skills-1-1/compressible-blocked-channel-holdout/run-20260805T143026335095Z-7c609a34
/tmp/foampilot-knowledge-skills-1-1/compressible-blocked-channel-holdout-rerun/run-20260805T143627519541Z-8674239c
```

上述 run 的 artifact manifest 均通过验证；没有 bubblewrap/权限阻断。复杂 case 的冷生成仍为
主要求解前耗时（blocked-channel 复跑 generation 约 294.8 s），与 Knowledge/Skill 正确率优化
分开处理。

### 浮力 target 与 holdout

- `buoyant-hot-room-boussinesq` target：网格、初始化通过并进入 `buoyantFoam`；steady energy
  update 发生 thermo inversion。repair 上下文选中新增 playbook，并显式核对
  `p=100000`、`p_rgh=0`、`pRefValue=0`、Boussinesq reference 与初温；判定参考状态可反演后，
  只将 `h` relaxation 从 0.3 降为 0.1，但相同 inversion 仍出现。诊断顺序改进，数值问题未解决。
- `buoyant-benard-cells` holdout：首次 authoring 与执行一次通过，网格、温度扰动初始化和
  `buoyantFoam` 均返回 0，运行到 1000，public validation `PASS`。
- `buoyant-cavity` holdout：首次 16-rank solver 因未限定的 `alphatWallFunction` runtime type
  失败；repair 只改为 `compressible::alphatWallFunction`，随后 16 核 solver 正常到 1000 并
  reconstruct，cumulative continuity 约 `-5.38e-12`，public validation `PASS`。

对应 run：

```text
/tmp/foampilot-knowledge-skills-1-1/buoyant-hot-room-boussinesq/run-20260805T144510006691Z-ee2a3d87
/tmp/foampilot-knowledge-skills-1-1/buoyant-benard-cells-holdout/run-20260805T145122649639Z-d043faa9
/tmp/foampilot-knowledge-skills-1-1/buoyant-cavity-holdout/run-20260805T145431848046Z-045610b0
```

三个 run 的 artifact manifest 均通过验证；没有 bubblewrap/权限阻断。

## 8. 最终验证与结论

### 8.1 有效 30 题复测

权威 run root：

```text
/tmp/foampilot-knowledge-skills-1-1-official-30-20260805-v2
```

对应 suite report：

```text
official-corpus-30-baseline-v1-report.json
official-corpus-30-baseline-v1-report.md
```

汇总结果：

| 指标 | 结果 |
|---|---:|
| suite `PASS` | 20/30 |
| `FAIL_AGENT` | 10/30 |
| `DEFERRED_BACKEND` | 0/30 |
| `BLOCKED_ENVIRONMENT` | 0/30 |
| case generation 成功 | 30/30 |
| `checkMesh` 通过 | 30/30 |
| 目标 solver 启动 | 28/30 |
| solver 正常结束 | 23/30 |
| public validation 通过 | 21/30 |
| 严格 physics qualification 通过 | 11/15 |

`rans-pitzdaily` 已通过 public validation，但未通过严格 pressure-change physics 指标，因此
suite 仍记为 `FAIL_AGENT`。其余失败按主要层次为：

- 启动前/初始化链：`compressible-blocked-channel`、`piso-porous-blockage`；
- solver/数值或字典：`multiphase-dam-break`、`compressible-shock-tube`、
  `pimple-blocked-channel`、`rhopimple-shock-tube`、两个 rhoCentral shock case、
  `buoyant-hot-room-boussinesq`；
- 严格 physics：`rans-pitzdaily`。

本轮累计模型时间约 `6122.4 s`，OpenFOAM 命令时间约 `1180.8 s`。冷路径首次 OpenFOAM
命令 P50/P95 为 `171.2/311.9 s`，端到端 P50/P95 为 `210.3/410.3 s`。没有
bubblewrap、权限或环境阻断。

曾在外层受限 sandbox 中产生过一个全部 deferred 的同名预跑 root；它不包含有效 Runner
证据，不纳入以上统计。

### 8.2 新增 20 个官方题族

新增 suite：

```text
src/foampilot/qualification/data/suites/official-corpus-extra-20-v1.yaml
```

原始单次 run root：

```text
/tmp/foampilot-knowledge-skills-1-1-extra-20-20260805-v2
```

原始结果为 `10 PASS / 9 FAIL_AGENT / 1 DEFERRED_BACKEND`，但其中 7 题没有形成有效能力
测试：capability registry 尚未登记明确写在公开任务中的专用 solver，另有 CHT 任务把低马赫
可压缩热物性错误写成 incompressible，因而在路由层立即失败。

只补充 solver capability facts 并修正 CHT 公开描述后，独立运行 7 题 recovery suite：

```text
/tmp/foampilot-knowledge-skills-1-1-extra-7-routing-recovery-20260805-v1
```

随后将完整 case generation 的有限预算从 `300/360 s` 调整为 `420/480 s`，并对两个纯生成
超时项执行带变更的独立 rerun，而没有修改或解冻 parent run。校正后的 20 题有效视图为：

| 指标 | 结果 |
|---|---:|
| `PASS` | 11/20 |
| `FAIL_AGENT` | 7/20 |
| `DEFERRED_BACKEND` | 2/20 |
| case generation 成功 | 19/20 |
| 目标 solver 启动 | 19/20 |
| solver 正常结束 | 11/20 |
| public validation 通过 | 11/20 |
| 环境/权限阻断 | 0/20 |

该“有效视图”由不可变原始 run、7 题 routing recovery 和两个带变更 rerun 组合而成，用于
剔除评测夹具缺陷；它不是一次单变量 A/B suite。逐类结果为：

- 通过：`simple-pipe-cyclic`、`pimple-tjunction`、`pimple-offset-cylinder`、
  `piso-rans-cavity`、`srf-mixer`、两个 rhoPimple case、两个 buoyant case、
  `interfoam-container-discharge`、`dsmc-free-space-periodic`；
- 已进入目标 solver 后失败：`simple-t3a-boundary-layer`、`rhocentral-wedge-ma5`、
  `cht-heated-duct`、`compressible-interfoam-climbing-rod`、`driftflux-dahl`、
  `multiphase-euler-bubble-column`、`reacting-counterflow-flame`；
- 暂缓：`rhosimple-square-bend` 长时间非收敛后 repair model deadline 已过期；
  `dense-particle-column` 在 420 秒内仍无法完成整体 case generation。

新增 20 题都是 public-validation holdout。Agent 不可读取目标 tutorial；结果证明的是从公开任务
独立生成并执行 native case 的能力，不等于复现官方 tutorial 字节或通过 golden physics。

### 8.3 受控学习结果

新增 6 个 Foundation v10 solver-family guide：

```text
compressibleInterFoam
driftFluxFoam
multiphaseEulerFoam
dsmcFoam
denseParticleFoam
reactingFoam
```

它们来自本机官方 solver 源码与公开同族字典的重写总结，不包含目标几何、标准答案或 evaluator
tolerance。知识条目总数由 43 增至 49，仍通过 frozen manifest 和 leakage 检查。

学习后的真实 gate 说明：

- `dsmcFoam` 在一次日志修复后 public validation `PASS`；
- driftFlux 的错误从 `Unknown mixtureViscosityModel Newtonian`/缺 `Vc` 推进到
  `alpha1DiffusionFinal`/`div(tauDm)`；
- multiphaseEuler 的错误从缺 `T.<phase>`/错误 transport 层级推进到缺少完整 interfacial-model
  tables；
- reactingFoam 的错误从 `reactingMixture`/缺 species 推进到精确 mixture 大小写和
  `unityLewisFourier` runtime name；
- compressibleInterFoam 消除了 300 秒误截断并真实启动 solver，但跨文件契约遵从仍不完整；
- denseParticleFoam 即使检索到新 contract、限制 computational parcels 且给出 420 秒，仍无法
  完成整体生成。该题明确暴露“一次整体生成大型 Lagrangian case”的当前边界。

这些结果支持继续通过公开日志补齐 family contract，但不支持为每道题增加专用模板或继续无限
放宽超时。下一阶段更合理的方向是对大型 asset/parcel 文件采用受控生成器或 2--3 批依赖生成，
同时保持 ExecutionPlan、Runner、repair 和 evaluator 主链不变。

### 8.4 完成前验证

最终验证证据：

```text
Knowledge validate: 49 entries, PASS
登记 scenarios 的 8 个 Skill validators: 全部 PASS
pytest: 508 passed, 5 skipped
artifact manifest: 65 runs checked, 0 problems
wheel: foampilot-0.1.0-py3-none-any.whl
wheel sha256: 43a081463c07c3a188b7b9291d8be54805e8c45cf26255d41b3adfd3a418eb4b
```

### 8.5 结论边界

本轮改善了三项可交付能力：

1. 30 题既有 corpus 已实现 `30/30` case generation 和 `28/30` 目标 solver 启动；
2. 新增 solver-family 不再因 registry 缺失被机械拒绝，20 题有效视图达到 `19/20` generation
   与 `19/20` 目标 solver 启动；
3. 长批次中环境/bubblewrap 阻断为 0，失败能够继续下一题，并被区分为 task、backend、solver、
   public validation 或严格 physics 层。

仍不能声称 50 题全部求解正确：有效视图中 public validation 为 `32/50`，30 题中的严格
physics qualification 为 `11/15`。当前最重要的准确性缺口是复杂 family 的跨文件字典完整性、
热力学/多相稳定性和 repair 一次只修复一个 reader error；最重要的性能边界是大型完整 case 的
单次模型生成。
