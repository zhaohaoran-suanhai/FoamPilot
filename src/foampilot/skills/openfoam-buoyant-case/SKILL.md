---
name: openfoam-buoyant-case
description: Use when an Agent authors, repairs, or validates a Foundation OpenFOAM v10 steady buoyantFoam case with p_rgh, turbulence wall functions, and wall heat transfer.
---

# 编写、修复与验证 buoyantFoam 算例

## 求解器语义

`p` 是热力学压力；`p_rgh = p - rho*g*h` 是浮力压力方程求解的静水压缩减压力。
`pRefCell` 与 `pRefValue` 用于固定 `p_rgh` 的规范自由度，不能取代理想气体模型使用的
热力学工作压力。

## 必需流程

1. 同时确定工作压力、重力基准、`p` 初始场、`p_rgh` 初始场以及
   `pRefCell`/`pRefValue`。拒绝混用绝对压力与缩减压力参考语义。
2. 稳态任务采用 `steadyState` 时间离散。将 `controlDict` 中的 time 视为迭代计数，
   并为 `p_rgh`、`U`、焓和湍流场配置 residual control。
3. 选择一组内部一致的 Foundation v10 湍流边界条件：
   - `k` 使用 `kqRWallFunction`；
   - `omega` 使用 `omegaWallFunction`；
   - `nut` 使用兼容的 wall function；
   - `alphat` 使用可压缩 wall function，并声明湍流 Prandtl 数和必需的 value 项。
4. 出现 `End` 后，对比方程初始 residual 的起始窗口与终止窗口。每个声明字段都必须呈下降
   趋势，并在结束时低于公开的 solver-family 阈值。
5. 检查终止时的 local 与 cumulative continuity error。即使带符号 cumulative error
   发生抵消，只要 local error 仍过大，就应继续求解或增强稳定性。
6. 在已完成算例的临时副本上运行 Foundation v10 `wallHeatFlux`。使用 active
   `thermophysicalTransportModel` 给出的积分 `Q`；不要只根据分子导热系数和第一层 cell
   温度重构热流。
7. 使用声明的公开容差检查
   `abs(Q_hot + Q_cold) / max(abs(Q_hot), abs(Q_cold))`。将失败的公开检查返回 repair，
   但不得暴露任何 golden result。

## 证据契约

报告以下内容：

- pressure field 与 reference 语义；
- wall-function 类型和湍流 Prandtl 数；
- 每个字段起始/终止 residual window 的中位数与比值；
- 终止 local 和 cumulative continuity error；
- 热壁/冷壁积分 `wallHeatFlux` 数值与归一化不平衡量；
- 相互独立的 execution verdict 与 public-physics verdict。

## 边界

- 绝不能根据固定迭代次数或 `End` 推断收敛。
- 绝不能把 cumulative continuity 的抵消当成 local continuity 足够小的证明。
- 绝不能使用目标算例的热流率、profile、Nusselt 数或私有 golden 选择数值设置。
- 不得仅为后处理而修改已保留的 attempt。

## 常见错误

| 错误 | 必需修正 |
| --- | --- |
| 将 `pRefValue` 视为绝对工作压力 | 分离热力学压力与缩减压力的职责 |
| 混用从无关湍流设置复制的 wall function | 验证一组内部一致的 Foundation v10 配置 |
| 只检查最终迭代编号 | 检查 residual 趋势和 continuity |
| 用分子项 `k*dT/dn` 估算湍流壁面热流 | 通过 `wallHeatFlux` 使用 active transport model |
