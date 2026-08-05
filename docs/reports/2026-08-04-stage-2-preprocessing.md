# 第二阶段：前处理能力实施记录

## 结论范围

本阶段在唯一的 `NativeAgent.solve()` 主链中加入公开几何事实、网格路线、外部工具能力、
网格质量报告和限定范围的修复，没有增加第二个 mesh runner、确定性 case renderer 或
tutorial 模板路径。当前证据证明实现和一个真实 `blockMesh` 闭环可用；surface/snappy、Gmsh
和多区域路线仍需更多真实几何 gate，本文不把合成 probe 测试等同于这些路线已经求解通过。

## 已实现内容

- `TaskSpec` 统一升级为 schema v2，可选声明 `GeometryInput` 和 `MeshIntent`；
- 支持参数化、STL/OBJ surface、Gmsh 和已提供 OpenFOAM mesh 四类输入契约；
- public asset 在路由和模型调用前完成 hash、路径、单位、bounds、surface name 与基础拓扑探测；
- 显式 `mesh.strategy` 覆盖 prompt 关键词，`auto` 只按无歧义 geometry mode 选择路线；
- 环境把可选 Gmsh 纳入 typed executable 能力和 strict-resume 指纹；
- surface、Gmsh 和 OpenFOAM 网格工具具有确定性 command stage 规则；
- 每个已执行 attempt 生成 `mesh-quality-report.json`，分开保存观测值和公开阈值失败；
- 增加 `MESH_QUALITY_FAILED`，不再把质量超限混同为 mesh command 崩溃；
- mesh repair 只允许修改 mesh/check command 和网格文件；只有 patch 同步有直接证据时，
  才允许只修改初始场的 `boundaryField`；
- 增加通用 mesh Skill，以及 snappy surface 和 Gmsh physical-group 两条公开知识。

## 当前证据

| 证据 | 结果 | 能证明什么 |
| --- | --- | --- |
| GeometryProbe 合成 STL/OBJ 测试 | 通过 | 单位换算、hash、bounds、闭合性、patch 映射与快速失败可复现 |
| routing/environment/semantic/continuation 测试 | 通过 | 网格策略和可执行能力进入同一计划与续跑边界 |
| MeshQualityReport 与状态机测试 | 通过 | 原生日志可解析，公开阈值有独立失败语义 |
| mesh repair/Skill/Knowledge 测试 | 通过 | 修复范围不会默认扩散到物性、求解器和初始内场 |
| `foampilot preflight --json` | `PASS` | bubblewrap namespace 被当前外层环境拒绝时，auto 策略立即选择 audited host，不产生交互式权限等待 |
| 冻结计划真实 continuation gate | `1 passed`，约 3.2 s | `blockMesh -> checkMesh -> icoFoam` 的失败、后端暂缓、严格续跑和修复可在真实 OpenFOAM v10 完成 |

当前机器未发现 Gmsh，因此 Gmsh 原生生成 gate 记为“环境未评估”，不记为 Agent 或 OpenFOAM
失败。surface/snappy 与多区域仍需要独立真实 gate；后续评测必须继续区分“接口已实现”、
“网格程序已运行”和“目标 solver/物理验证已通过”。

## 稳定性结论

几何资产错误、patch 映射歧义和缺失网格工具现在都在模型生成前以稳定代码结束。bubblewrap
不可用不会弹出权限对话或阻塞长任务，Runner 的 `auto` 后端会记录原因并使用受审计 host
执行。这个 fallback 保留 typed command、allowlist、资源限制与日志，但不提供 network
namespace 隔离，报告和演示时应明确这一差异。
