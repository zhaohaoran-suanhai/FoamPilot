---
name: openfoam-incompressible-pressure-velocity
description: Use when authoring or repairing a Foundation OpenFOAM v10 incompressible pressure-velocity case with icoFoam, pisoFoam, pimpleFoam, simpleFoam, porousSimpleFoam, or SRF solvers.
---

# 不可压缩压力—速度耦合算例

## 核心原则

先让压力参考、速度边界、通量和算法字典形成闭合契约，再选择离散格式和松弛因子。不要用
“求解器能启动”代替压力—速度系统的一致性。

## 必需契约

1. 根据稳态或瞬态选择 `SIMPLE`、`PISO` 或 `PIMPLE`，并只写对应算法段。
2. 在全 Dirichlet 或周期压力边界下明确提供 `pRefCell`/`pRefValue`；存在定压出口时不要
   机械添加参考单元。
3. 每个 mesh patch 都必须出现在 `U` 和 `p` 中；入口、出口、壁面和二维 `empty` patch
   的类型必须相容。
4. `fvSchemes` 必须覆盖求解器实际请求的梯度、散度和拉普拉斯算子；稳态 RANS 同时覆盖
   湍流输运项。
5. `fvSolution.solvers` 必须包含实际求解字段及其 `Final` 变体；稳态算例再配置合理的
   equation/field relaxation。
6. 对旋转参考系或多孔介质，只在任务确实声明相应物理模型时增加模型字典。
7. 使用 `volumeFractionSource` 时，`alpha.<volumePhase>` 表示障碍物占据比例，不是开放流体比例；
   自由流区域必须为 `0`，并且全域严格满足 `0 <= alpha.<volumePhase> < 1`。精确的 `1`
   会使剩余流体体积为零，不得用它表示完全固体区。
8. 对 Maxwell/PIMPLE，先确认模型专用 operators、`sigma` solver coverage 与 outer coupling，
   再读取 actual Courant 和 sigma stress residual history 并定位首次恶化时刻。一次 repair 只修改
   时间控制、stress convection、outer coupling 或 relaxation 中一个有证据的原因族；不得违反 TaskSpec
   明确固定的 `deltaT`、物性或边界，约束不可行时报告 conflict。

## 结果检查

- `checkMesh` 通过且 patch 清单与所有初始场一致；
- 目标求解器实际启动；
- continuity error、压力和速度 residual 没有持续增长；
- 稳态任务达到公开收敛阈值，瞬态任务满足 Courant 与时间步要求；
- 压降、流量或力等任务声明的公开物理量通过验证。

## 常见错误

| 症状 | 最小修复 |
| --- | --- |
| `Unable to set reference cell` | 根据压力边界判断是否补充 pressure reference |
| `keyword ... is undefined in dictionary fvSchemes` | 只补实际缺失的算子条目 |
| continuity 快速增大 | 检查边界通量、时间步、离散格式和松弛设置 |
| Maxwell stress residual 随 actual Courant 恶化 | 定位首次恶化，单独修复一个原因族；不要同时改固定输入 |
| SRF/porous 文件缺失 | 仅为已声明模型补充对应字典和 command |

不得读取目标 tutorial 或 golden 数值来选择参数。
