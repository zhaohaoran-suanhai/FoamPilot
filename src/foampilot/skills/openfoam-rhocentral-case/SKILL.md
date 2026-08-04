---
name: openfoam-rhocentral-case
description: Use when an Agent authors, repairs, or validates a Foundation OpenFOAM v10 rhoCentralFoam shock-tube or inviscid compressible transient case.
---

# 编写、修复与验证 rhoCentralFoam 算例

## 求解器语义

将 `deltaT` 视为初始时间步，将 `maxDeltaT` 视为自适应增长的上限。当任务要求
`adjustTimeStep yes` 时，设置正的 `maxCo`，并保证 `maxDeltaT > deltaT`。从其他
tutorial 复制的 `maxDeltaT` 不能证明它适合当前网格、热力学状态或结束时间。

## 必需流程

1. 从声明的 ideal-gas properties 推导密度与比热比。存在公开输入时，不得使用记忆中的
   空气常数替代。
2. 将初始间断面与 cell face 对齐，并使横向在几何上保持一维。
3. 使用内部一致的 Foundation v10 热力学模型和无黏输运属性，并对照 mesh patch 清单
   检查每个 field 与 boundary。
4. 求解前检查 `controlDict`：
   - `adjustTimeStep yes`;
   - 任务要求的正 `maxCo`；
   - `maxDeltaT` 严格大于初始 `deltaT`；
   - write precision 足以表示自适应时间步。
5. 求解器正常结束后，解析实际 Courant history。若数值远低于目标，可能说明
   `maxDeltaT` 仍是 active limiter；若超过允许值，则不安全。
6. 根据公开初始状态计算 ideal-gas Riemann 问题的精确波速与波位置，并在声明的 cell-width
   容差内检查 rarefaction head、contact 和 shock。
7. 任一公开检查失败时，将检查证据交给 repair，并只修改与原因直接相关的最小输入。
   零退出码不等于 physics pass。

## 证据契约

报告以下内容：

- `deltaT`、`maxDeltaT`、`maxCo` 和 `adjustTimeStep`；
- 观测到的 peak Courant number 及其相对目标比值；
- 解析与检测得到的 rarefaction、contact 和 shock 位置；
- 以米和 cell width 表示的空间容差；
- 相互独立的 execution verdict 与 public-physics verdict。

## 边界

- 绝不能使用目标 tutorial、私有 validator 或 golden wave position。
- 不得为了通过私有比较而调节时间步。
- 不得用质量守恒和平滑 profile 代替波传播精度证据。
- 缺少 Courant 或波检测证据时，以 not proven 停止。

## 常见错误

| 错误 | 必需修正 |
| --- | --- |
| `maxDeltaT` 等于初始 `deltaT` | 为自适应步长留出增长空间，并验证实际 Co |
| 复用其他可压缩 tutorial 的上限 | 根据当前网格和公开状态推导控制量 |
| 只比较最终 primitive profile | 添加精确的公开 wave-position 检查 |
| 将 `End` 视为成功 | 分离 execution completion 与 physics acceptance |
