---
name: openfoam-multiphase-vof
description: Use when authoring or repairing a Foundation OpenFOAM v10 VOF or miscible two-liquid case with interFoam or twoLiquidMixingFoam, especially for phase initialization, p_rgh, interface boundedness, and conservation.
---

# 多相 VOF 与两液体输运算例

## 核心原则

相分数初始化、相物性、重力压力、界面输运格式和 Courant 控制必须共同闭合。不得通过裁剪
最终字段或放宽容差掩盖不有界的界面输运。

## 必需契约

1. 明确相名称、相序、密度、运动黏度/动力黏度和表面张力，按 Foundation v10 求解器实际
   读取的文件布局编写物性。
2. 按求解器读取顺序完成 `phaseProperties`、每相 `physicalProperties`、初始
   `alpha.<phase>` 和相分数 solver entry。`twoLiquidMixingFoam` 的 `phaseProperties` 同时
   核对 `Dab`、`alphatab`；相分数 solver entry 同时包含 `solver`、`smoother`、
   `tolerance` 与 `relTol` 以及求解器需要的 alpha controls。
   对 `pcorr`、`p_rgh`、`U` 等会进入 final corrector 的字段保持 base/Final 成对。
3. `alpha.<phase>` 是无量纲相分数；`p_rgh` 的 dimensions 是压力维度，不是密度或相分数
   维度。每个字段必须覆盖全部 mesh patch。
4. 区域化初态必须由显式 `setFields` command 生成；command 位于网格检查之后、求解之前。
5. 为相分数提供求解器要求的 `alpha`、`alphar` 和压缩/限制格式，且给出必要的
   `nAlphaSubCycles`、`cAlpha`、`maxAlphaCo` 等控制量。
6. 时间步同时满足全局 Courant 和 alpha Courant 要求；监测项不是进入求解的前置依赖。
7. 只使用 Foundation v10 已安装且本题必需的 function object；未知或非必要诊断应从
   `controlDict` 移除，由验证器在求解后计算。

## 结果检查

- 检查全时段 `alpha` extrema，而不只检查最终写出时间；
- 检查相体积守恒和界面运动方向；
- 检查压力、速度、Courant 与 continuity 是否有限；
- 将求解器正常完成与公开物理验证分别报告。

## 常见错误

| 症状 | 最小修复 |
| --- | --- |
| `p_rgh` dimensions 错误 | 恢复压力维度并重新检查初边值 |
| `Dab`、`alphatab` 或 alpha solver keyword 缺失 | 按 reader 契约一次补齐同一 dictionary，避免逐关键词 repair |
| 初始液柱/液滴不存在 | 补充并执行 `setFields`，不要手写运行后字段 |
| `alpha` 超出 `[0,1]` | 检查界面格式、压缩、子循环和时间步 |
| function object 类型未知 | 删除非必要监测项，保留求解主路径 |

不得读取目标 tutorial 或 golden 界面位置。
