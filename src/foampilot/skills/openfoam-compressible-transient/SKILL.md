---
name: openfoam-compressible-transient
description: Use when authoring or repairing a Foundation OpenFOAM v10 compressible case with rhoCentralFoam, rhoPimpleFoam, or rhoSimpleFoam, especially when thermodynamics, shocks, acoustics, or adaptive time stepping are involved.
---

# 可压缩流与瞬态传播算例

## 核心原则

热力学模型、初始状态、能量变量、输运模型与数值时间尺度必须成套一致。正常写出 `End` 只
证明程序结束，不证明激波、接触间断或热力学状态正确。

## 必需契约

1. 从任务给出的状态量和物性建立完整 thermo 状态，不用记忆值替代公开输入。
2. 确认求解器使用的能量字段、压力、温度、速度和必要的湍流字段均存在，dimensions 与
   boundary patch 一致。
3. 先验证初始 thermo state：用公开状态关系核对 `p`、`T`、`rho` 与所选 energy 的一致性和
   正值；不能用裁剪字段掩盖 negative temperature。
4. 对每个 equation 按矩阵类型选择兼容的 solver/preconditioner。`rhoCentralFoam` 的
   `(rho|rhoU|rhoE)` 是守恒显式场，可对实际 diagonal 更新使用 `diagonal`；`(U|e)` 是可能
   被边界或方程请求的派生隐式场，应使用对当前 symmetric matrix 有效的 solver，例如
   `smoothSolver`。symmetric pressure matrix 不使用只注册于 asymmetric 路径的 `DILU`。
5. 对 `rhoCentralFoam`，将 `deltaT` 视为初始步长；若 `adjustTimeStep yes`，提供正的
   `maxCo` 且令 `maxDeltaT > deltaT`。
6. 对 PIMPLE/SIMPLE 可压缩求解器，提供实际方程所需的 `div(phi,U)`、能量和湍流散度
   格式以及字段求解器。
7. 初始间断或区域初始化必须在网格坐标系中可表达，并由必要的 `setFields` command 显式
   生成，不能假设字段已存在。
8. 时间精度、写出精度和采样位置必须足以支持任务声明的公开验证。字典 compatibility 与初始
   thermo state 通过后，才调整 Courant、格式或松弛。

## 结果检查

- 解析实际 Courant history，而不是只检查 `maxCo` 配置；
- 监测压力、温度、密度和能量是否保持有限及物理可行；
- 对激波管，用公开初始状态计算 Riemann 波速与波位置；
- 对稳态可压缩流，检查 residual、连续性和任务声明的质量/能量守恒。

## 常见错误

| 症状 | 最小修复 |
| --- | --- |
| `maxDeltaT` 限制实际 Co 远低于目标 | 放宽上限并保留稳定性检查 |
| thermo package 或字段不匹配 | 统一 thermo 类型、能量字段和物性字典 |
| `Unknown symmetric matrix solver diagonal` | 保留守恒显式场的 diagonal 项；为派生隐式场选择 `smoothSolver` 等 symmetric solver |
| `Unknown symmetric matrix preconditioner DILU` | 只修目标 field entry，选择 DIC/FDIC 或其他运行时列出的 symmetric 选项 |
| 温度/压力出现负值或 NaN | 检查初态、边界、时间步和有界格式 |
| 缺失能量散度项 | 只补目标求解器实际请求的 `fvSchemes` 条目 |

不得使用目标 tutorial、私有 validator 或 golden 波位置调参。
