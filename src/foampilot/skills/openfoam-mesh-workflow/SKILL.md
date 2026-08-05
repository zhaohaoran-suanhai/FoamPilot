---
name: openfoam-mesh-workflow
description: Use when a Foundation OpenFOAM v10 case must create, inspect, or repair blockMesh, surface/snappyHexMesh, Gmsh, or provided OpenFOAM meshes from public geometry evidence.
---

# 规划并验证 OpenFOAM 网格流程

## 输入边界

只使用 TaskSpec 中声明的 `GeometryInput`、`MeshIntent`、已校验 public asset 和系统提供的
`GeometryFacts`。STL/OBJ 的长度单位必须来自 TaskSpec；不得根据包围盒外观猜测单位，也不得
读取目标 tutorial、golden mesh 或任意宿主机路径。

## 选择网格路线

- 参数化盒体、通道和少量规则 block 优先使用 `blockMesh`。
- 已命名 STL/OBJ 表面采用 background `blockMesh`、必要的 `surfaceFeatureExtract`、
  `snappyHexMesh` 和 `checkMesh`。
- `.geo` 或 `.msh` 只有在环境声明 Gmsh 可用时才采用 `gmsh`；转换使用 `gmshToFoam`，并核对
  physical group 对应的 patch/zone。
- 已提供 OpenFOAM mesh 不再重新生成，但仍必须运行 `checkMesh`，且 patch/region 必须匹配
  TaskSpec。

所有步骤都写成 `ExecutionPlan v3` typed command。网格程序使用 `stage: mesh`，`checkMesh`
使用 `stage: check`；不得生成 shell、`Allrun`、重定向或 MPI launcher。

## 编写与自检

### blockMesh

共享面两侧必须复用相同全局 vertex label，并在两个切向方向使用相容 cell 数。逐块检查 hex
手性、外表面覆盖、二维单层与 `empty` 一致性。不要用不存在的命令参数掩盖拓扑错误。

### surface/snappyHexMesh

surface 名称必须来自 `GeometryFacts.surface_names`。显式设置 background domain、refinement、
location-in-mesh 和必要 feature；只有 TaskSpec 启用 boundary layer 时才配置 layer。location 点
必须能由公开几何事实解释，不能随意落在未知一侧。snappy 后核对预期 patch、region 和空 patch。

### Gmsh

为每个入口、出口、壁面、interface 和 volume region 声明 physical group。转换后检查 group
名称是否成为预期 patch/zone；不能用自动生成的编号替代公开物理角色。

## 网格质量

始终保留原生网格日志，并以 `MeshQualityReport` 的观测值对照 `MeshIntent`：

- `check_mesh_passed`；
- cell/face/point/region 数；
- 最大 non-orthogonality 和 skewness；
- 负体积以及 patch/region 事实。

缺失的显式阈值证据视为未证明，而不是自动通过。不能放宽 TaskSpec 阈值制造成功。

## 网格修复

网格失败时只修改网格字典、`.geo/.msh` 和 mesh/check command。只有日志证明 patch 名发生变化
时，才允许同步初始场的 `boundaryField`；此时 internalField、物性、求解器、数值格式和公开验收
要求必须保持不变。每次只修复一个由日志直接支持的拓扑或质量原因，并完整重跑网格链。
