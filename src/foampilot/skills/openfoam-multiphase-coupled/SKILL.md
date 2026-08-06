---
name: openfoam-multiphase-coupled
description: Use when authoring or repairing a Foundation OpenFOAM v10 driftFluxFoam or multiphaseEulerFoam case with interacting phases, grouped fields, and interfacial model dictionaries.
---

# 多相耦合算例

## 核心原则

先把 selected solver guide 中列出的必需表、字段、物性和 base/Final 项转成原子清单，再生成
文件。空模型表若被 Foundation v10 reader 无条件读取，也必须显式保留为空字典，不能因当前
任务不启用该模型而删除整个 table。

## driftFluxFoam

一次检查：

- `phaseProperties` 中的 phases、mixture-viscosity 和 relative-velocity contract；
- 每相 `physicalProperties.<phase>` 与 `alpha.<phase>` 名称一致；第一/分散相的 mixture model
  由 `physicalProperties.<dispersed>` 中的 `viscosityModel` 选择，例如任务选择浆体闭合时写
  `viscosityModel slurry`，不能把选择器放到 `phaseProperties` 后让分散相仍保留 `constant`；
- `U`、`p_rgh`、`g` 和 `momentumTransport`；
- `alpha.<phase>` 中的 `nAlphaCorr`、`nAlphaSubCycles`、`MULESCorr` 和必填
  `nLimiterIter`，以及 `alpha.*Diffusion` 的 base/Final 配对；
- 实际方程需要的 `div(tauDm)` 和其他精确 `divSchemes`。

不得等 reader 逐项报告 `alpha1DiffusionFinal`、`div(tauDm)` 或同组必需项后再逐个补齐。

## multiphaseEulerFoam

一次检查：

- `phases`、`referencePhase` 及每相 phase/diameter model；
- 每相 `U`、`T`、`alpha`、`physicalProperties` 和 `momentumTransport`；
- 共享 `p`、`p_rgh` 和 `g`；
- `blending`、`surfaceTension`、`interfaceCompression`、`drag`、`virtualMass`、
  `heatTransfer`、`phaseTransfer`、`lift`、`wallLubrication` 和
  `turbulentDispersion` 表；
- 每相速度、温度/能量、相分数和共享压力的 solver/operator entry。

相分数格式应逐相覆盖 reader 构造的 `div(phi,alpha.<phase>)`。使用 regex 时必须对
OpenFOAM key 中的字面括号做转义；不能用未转义的宽泛形式假定它会匹配。相对通量还应按
实际相对写出两个方向，或使用经过核对的、转义字面括号的等价 regex。

每相动量方程的黏性 key 使用 `thermo:rho.<phase>`、`nuEff.<phase>` 与
`grad(U.<phase>)` 的 grouped 名称；不能用 `rho.<phase>` 或 `grad(U)` 的简写猜测 reader
名称，也不能让通用 `default Gauss upwind` 接管黏性 tensor operator。

没有启用的相间机制若仍由 reader 无条件查找，应使用结构正确的空字典，不能省略 table。

## 结果检查

- 检查相名、grouped field 和物性文件严格一致；
- 检查各相分数有限且总和接近一；
- 检查共享压力、每相速度和温度有限；
- 将 reader contract 通过、solver 正常结束和物理验证分开报告。

## 修复

保持相名、物性、边界和已经通过的阶段不变。同一个 reader contract 中由正式 solver guide
明确列出的成组必需项应一次补齐，但不能捆绑数值调参、模型替换或验收条件修改。

不得读取目标 tutorial、golden 或私有 evaluator。
