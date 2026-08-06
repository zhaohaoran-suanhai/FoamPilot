---
name: openfoam-buoyant-cht
description: Use when authoring or repairing a Foundation OpenFOAM v10 buoyantFoam or chtMultiRegionFoam case involving p_rgh, heat transfer, turbulence wall functions, thermal interfaces, or multiple regions.
---

# 浮力传热与共轭换热算例

## 核心原则

压力语义、热物性、能量字段、壁面函数和区域接口必须成套一致。多区域算例中，同名字段必须
按 region 分别编写和验证，不能把单区域文件复制为所有区域的配置。

## 必需契约

1. 对浮力流分离热力学压力 `p` 与静水压缩减压力 `p_rgh = p - rho*g*h`；
   `pRefCell`/`pRefValue` 固定 `p_rgh` 规范自由度，不能取代工作压力。
2. 同时确定重力方向、几何基准、温度初态、热物性和浮力方向，检查热壁/冷壁是否与任务
   描述一致。
3. 湍流算例使用内部一致的 `k`、`omega`/`epsilon`、`nut` 和 `alphat` wall function；
   层流算例不得凭空增加湍流场。
4. `chtMultiRegionFoam` 必须声明 fluid/solid regions，为每个 region 提供独立的初始场、
   `fvSchemes`、`fvSolution` 和物性，并让成对 interface patch 类型匹配。
5. mesh commands 先生成总网格，再按需要执行 region 划分和 region-specific `checkMesh`；
   目标 solver 是验证这些步骤后的首个求解 command。
6. 稳态任务检查 residual 趋势和 continuity，不以迭代次数或 `End` 代替收敛。
7. 发生 thermo inversion、`Maximum number of iterations exceeded` 或负温度时，先验证参考状态、
   初始/边界 `p`、`T`、`rho`、energy 和 thermo package 能否一致反演，并保存失败前的
   temperature extrema。不得只修改 `fvSolution`；状态可反演后才调整松弛、时间步和格式。

## 结果检查

- 每个 region 的网格、字段和 interface patch 一致；
- 温度、压力和速度保持有限；
- fluid residual、local/cumulative continuity 以及 solid temperature residual 下降；
- 在临时副本中用 Foundation `wallHeatFlux`/公开积分检查热流平衡；
- execution verdict 与 public-physics verdict 分开报告。

## 常见错误

| 症状 | 最小修复 |
| --- | --- |
| 将 `pRefValue` 当作工作压力 | 分离 `p`、`p_rgh` 与 reference 职责 |
| `div(phi,K)` 或能量项缺失 | 只补当前 solver 实际请求的离散条目 |
| thermo inversion 反复出现 | 先验证参考状态、初边值和 thermo package，不得只调整 linear solver |
| interface patch 不成对 | 对照各 region boundary 修正映射名称和类型 |
| 温度平滑但热流不守恒 | 用 active transport model 计算真实壁面热流 |

不得使用目标算例的 Nusselt 数、温度剖面或私有 golden 调参。
