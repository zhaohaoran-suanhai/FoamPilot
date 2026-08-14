# 原生 polyMesh 多孔阻塞算例提示词

本提示词用于已经声明为原子目录资产的 `constant/polyMesh`，目标是 Foundation OpenFOAM 10 的
`pisoFoam`。它不适用于 Fluent/Gmsh `.msh` 导入流程。

## 已确认输入

- 直接使用随任务声明的原生 `constant/polyMesh`；不得导入、转换、缩放、重新生成或覆盖网格。
- 网格坐标长度单位为米（`m`）。
- 求解器为 Foundation OpenFOAM 10 的 `pisoFoam`。
- 本次采用快速 case-only 验证：`endTime=80000 s`、`deltaT=100 s`、
  `writeInterval=20000 s`；只写出并检查 case，不启动 `pisoFoam`。
- patch/zone 采用检查器确认的准确映射：`inlet` 为入口，`outlet` 为出口，`top` 和 `bottom`
  为 `symmetryPlane`，`frontAndBack` 为 `empty`，`porousBlockage` 为多孔 cellZone。
- 网格没有独立 wall patch；不得虚构 wall，也不得把 `top`/`bottom` 改为无滑移壁面。

## 物理意图

研究二维通道内单相、不可压缩、牛顿层流在瞬态启动后穿过局部体积多孔阻力区域时，速度场、
运动压力场、流量和压降如何建立并逐渐稳定。多孔区是可穿透的流体体单元区，不是阀门、阀座、
孔板、喷嘴、狭缝或不可穿透固体；不得计算 Cv/Kv。

在已有 `porousBlockage` cellZone 上使用 Foundation v10 支持的体积动量源。黏性 Darcy 阻力为
主，惯性阻力只有在能力和量纲明确时才使用。若黏度、入口速度、Darcy/Forchheimer 系数没有
用户数值，CaseDesigner 应提出带单位、保持层流且产生清楚但有限压降的具体候选，由 RiskGate
逐字段确认，不得冒充用户事实。

从近似静止流体和一致的运动压力参考状态开始。当前受信任能力从初始时刻施加稳定、低速的
固定入口速度，不生成未经支持的时间斜坡；出口给定压力参考并允许自然流出，不得把入口速度
强加到出口。用
`Re = U_ref * D_h / nu` 校核层流状态。

## 时间与观测

以 `t_c = L / U_ref` 估算通道对流时间。正式研究流动稳定性时，`system/controlDict` 的
`endTime` 应覆盖数个对流时间；本次快速 case-only 验证只取一个对流时间 `80000 s`，不得据此
预先宣称物理稳定。不得创建、读取或设置不存在的 `solver_control.end_time`；到达 `endTime`
不等于物理稳定。

CaseDesigner 应提出稳定的时间步和 Courant 控制候选。紧凑网格摘要没有给出精确最小单元长度
时，不得虚构该长度，也不得仅因此阻断 case 编写；采用保守候选，并由实际运行的 Courant 数和
有限数值门禁检验。

至少保留入口/出口流量、多孔 cellZone 平均速度、运动压差、残差、连续性、Courant 数、最终
时间和正常 `End` 证据。若第一方观测能力不能唯一构造多孔区正前/正后的内部采样面，应明确
报告该派生指标不可用，并使用已有边界或 cellZone 指标；不得虚构采样位置。缺少内部采样面或
用户数值验收阈值只限制结果解释，不阻断安全编写和求解。

合理结果应表现为：流体能穿过多孔区；多孔区产生附加、连续的压力损失；上游压力高于下游；
区内速度受抑制但非零；入口和出口流量后期接近；下游速度连续恢复；没有 NaN、Inf 或明显非
物理振荡。

## 本次 case-only 验收

- Foundation OpenFOAM 10 的 `checkMesh` 没有 fatal mesh error；
- 完整写出 `0/`、`constant/` 和 `system/` 所需文件；
- `fvModels` 选择真实 `porousBlockage` cellZone，并引用已定义的坐标系；
- 编译后的 case-only ExecutionPlan 不含 `solve` 或 `pisoFoam` 命令。

只有后续明确执行求解时，才增加 `pisoFoam` 正常退出、正常 `End`、`U/p` 无 NaN 或 Inf 等
运行验收；这些结果不属于本次 case-only 验证。

没有用户确认数值阈值的流量守恒、压降稳定性和速度恢复只能报告为指标，不能制造 PASS。
