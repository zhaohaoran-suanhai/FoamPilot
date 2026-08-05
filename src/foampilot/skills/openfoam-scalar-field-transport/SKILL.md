---
name: openfoam-scalar-field-transport
description: Use when authoring or repairing a Foundation OpenFOAM v10 scalarTransportFoam or electrostaticFoam case involving passive transport, diffusion, potential fields, sources, or scalar boundary conditions.
---

# 标量输运与势场算例

## 核心原则

先明确标量的物理含义、dimensions、扩散/源项和边界，再决定是否需要流场。标量求解器不能
自动补出任务未提供的速度、通量或电荷源。

## 必需契约

1. 声明求解字段及其 dimensions：被动浓度、温度类标量和电势不能互换 dimensions。
2. 对 `scalarTransportFoam`，明确 `U`/`phi` 的来源、扩散系数和源项；若没有已知流场，
   只能选择任务允许的静止场或先生成公开定义的流场。
3. 对 `electrostaticFoam`，提供电势、介电/源项数据以及能固定势场规范自由度的边界条件。
4. `fvSchemes` 只覆盖实际存在的对流、扩散与梯度算子；纯扩散问题不得凭空添加不一致的
   对流通量。
5. `fvSolution` 必须包含实际标量字段，稳态/瞬态控制与任务时间语义一致。
6. 每个 mesh patch 都有标量边界；入口 fixedValue、出口 zeroGradient、绝缘和指定电势等
   类型按物理意义选择。

## 结果检查

- 目标 solver 启动且字段保持有限；
- 有界标量不越过公开上下界；
- 纯扩散/势场满足最大值原则、对称性或公开解析解；
- 有源输运检查积分守恒，电势问题检查通量或场强方向；
- 将使用预先存在流场这一前提写入产物和验证报告。

## 常见错误

| 症状 | 最小修复 |
| --- | --- |
| 缺少 `U` 或 `phi` | 明确流场来源并加入必要生成步骤 |
| scalar dimensions 错误 | 按任务物理量恢复 dimensions 和源项单位 |
| 势场不唯一 | 提供至少一个兼容的电势参考边界 |
| 标量超界 | 检查边界、源项、时间步和有界格式 |

不得读取目标 tutorial 的场值或私有解析结果来构造边界条件。
