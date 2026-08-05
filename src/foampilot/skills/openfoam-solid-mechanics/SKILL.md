---
name: openfoam-solid-mechanics
description: Use when authoring or repairing a Foundation OpenFOAM v10 small-strain solidDisplacementFoam or solidEquilibriumDisplacementFoam case with displacement, stress, material, and mechanical boundary conditions.
---

# 小变形固体力学算例

## 核心原则

单位制、材料参数、位移约束和载荷必须定义一个可解且不含刚体模态的力学问题。不得因为
位移场有限或求解器写出 `End` 就认为应力与边界反力可信。

## 必需契约

1. 统一使用 SI dimensions，明确密度、杨氏模量、泊松比和模型假设；检查泊松比处于材料
   模型允许区间。
2. `D`、应力相关字段和材料字典采用目标求解器实际读取的 Foundation v10 名称与结构。
3. 每个 mesh patch 都必须有机械边界；固定、对称、自由表面、位移载荷和牵引载荷不能互相
   冲突。
4. 约束足以消除刚体平移与旋转，但不得过约束任务声明的变形方向。
5. `fvSchemes` 覆盖位移梯度和拉普拉斯项，`fvSolution` 包含位移求解器与算法段；只有任务
   确实需要时才启用非线性或瞬态设置。
6. 网格尺度、载荷大小和材料刚度必须数值上可分辨，且不通过随意降低刚度来“帮助收敛”。

## 结果检查

- 目标固体求解器启动并正常完成；
- 位移 residual 下降，位移和应力保持有限；
- 约束 patch 的位移符合边界条件；
- 合力/反力、对称性或公开解析量满足任务容差；
- 若任务超出小变形线弹性范围，明确报告能力边界，而不是伪造模型。

## 常见错误

| 症状 | 最小修复 |
| --- | --- |
| singular/不收敛且整体漂移 | 检查并消除刚体模态 |
| 位移数量级异常 | 检查材料与载荷 dimensions、几何单位 |
| patch field 缺失 | 对照 mesh boundary 补齐 `D` 边界 |
| 应力可信但反力不平衡 | 重新检查载荷方向、面积和约束 |

不得读取目标 tutorial 的位移或应力结果来设定材料和载荷。
