# 阶段 2：前处理能力规格

状态：核心实现已完成，当前证据和 surface、Gmsh、多区域 gate 缺口见
[第二阶段实施记录](../reports/2026-08-04-stage-2-preprocessing.md)。本文保留完整规格，未通过的
原生 gate 不得解释为现有能力。

## 1. 背景与目标

当前 FoamPilot 能让 Agent 编写 `blockMeshDict`，也能以 typed command 调用多种 OpenFOAM
mesh utility，但任务输入没有稳定表达几何单位、surface、region、patch 语义或网格目标。
复杂几何只能依赖 prompt 和 Agent 临场理解，容易在进入 solver 前出现：

- 几何尺度或单位错误；
- patch 名称和边界物理含义不一致；
- `blockMesh` 相邻 block 拓扑不一致；
- `snappyHexMesh` 缺少 feature、refinement 或 location 信息；
- Gmsh physical group 转换后 patch 语义丢失；
- 网格质量和 cell 数超出任务预算。

本阶段的目标是让 FoamPilot 可以从以下输入开始完整求解：

1. 参数化简单几何；
2. STL/OBJ 等表面几何；
3. Gmsh `.geo` 或 `.msh`；
4. 已提供的 OpenFOAM mesh 资产。

Agent 负责设计网格策略并编写原生配置；OpenFOAM/Gmsh 负责离散化；FoamPilot 负责输入校验、
事实探测、安全执行、质量评估和有界修复。

## 2. 非目标

- 不开发 CAD 建模器或 STEP/IGES B-Rep 内核。
- 不承诺修复任意破损 CAD 或非流形工业表面。
- 不自动证明网格无关性或工程级 y+ 合格。
- 不替代专业网格软件的全部交互功能。
- 不建立确定性 case renderer；Agent 仍编写 OpenFOAM/Gmsh 文件。
- 不把已有官方 tutorial mesh 或目标 case 作为生成模板。
- 不在本阶段建设图形界面。

## 3. 在现有主链中的位置

```text
TaskSpec
  -> stage public geometry assets
  -> GeometryProbe
  -> GeometryFacts
  -> environment / capability / mesh route
  -> Knowledge + physics Skill + mesh Skill
  -> Agent 编写完整 ExecutionPlan v3
  -> mesh commands
  -> checkMesh / MeshQualityEvaluator
  -> initialize / solve / postprocess
  -> existing public validation and repair
```

`GeometryProbe` 只读取已经声明并通过 SHA256 校验的 public asset。它不读取 tutorial、外部任意
路径或 evaluator-only 数据。

## 4. TaskSpec 的最小扩展

### 4.1 版本策略

阶段 2 将引入包含几何与网格意图的规范 `TaskSpec` 新版本。仓库内 examples、qualification
fixtures 和文档一次性迁移到新版本，之后 authoring 只保留一条主路径。

历史 run 继续能够只读报告，但不长期维护 v1/v2 双 solve 状态机。若迁移工具确有需要，应是
一次性离线转换，不得成为运行时 fallback。

### 4.2 `GeometryInput`

建议结构如下，最终字段名在实现计划前冻结：

```yaml
geometry:
  mode: parametric | surface | gmsh | openfoam_mesh
  dimensionality: two_d | axisymmetric | three_d
  description: "公开、与物理任务一致的几何说明"
  length_unit: m | cm | mm | um | in
  assets:
    - path: geometry/body.stl
      format: stl
      role: closed_body_surface
  parameters:
    channel_length:
      value: 1.0
      unit: m
  patch_roles:
    inletSurface: inlet
    outletSurface: outlet
    bodySurface: wall
  region_roles:
    fluid: fluid
```

约束：

- `assets[].path` 必须引用 `public_assets` 中已有且 hash 已验证的文件；
- STL/OBJ 本身不携带可靠长度单位，`length_unit` 不得由 Agent 猜测；
- `patch_roles` 描述物理角色，不等同于具体 OpenFOAM boundary condition；
- 参数化几何只表达有实际任务需求的尺寸，不设计通用 CAD DSL；
- 已有 mesh 必须说明其来源、单位、region 和 patch 预期。

### 4.3 `MeshIntent`

```yaml
mesh:
  strategy: auto | blockMesh | snappyHexMesh | gmsh | provided
  target_cell_size: 0.002
  target_cell_count:
    min: 50000
    max: 2000000
  refinement_regions:
    - role: body_near_field
      level: 3
  boundary_layers:
    enabled: true
    patches: [bodySurface]
    layer_count: 5
  quality:
    require_check_mesh_pass: true
    max_non_orthogonality: 70
    max_skewness: 4
```

`MeshIntent` 是公开目标和预算，不是完整 mesh dictionary。未由用户声明的工程级 y+、首层高度
或增长率不能被系统伪装成已满足要求。

## 5. GeometryProbe

### 5.1 职责

在模型生成前，以有界、确定性的方式生成 `GeometryFacts`：

```yaml
schema_version: 1
source_hashes: {}
bounding_box_m: {}
point_count:
face_count:
surface_names: []
region_names: []
closed_surface:
manifold_status:
dimensionality_observation:
patch_role_matches: []
warnings: []
```

Probe 首期优先使用已安装 Python 依赖读取 STL/OBJ 的 metadata、bounds 和基本拓扑。只有现有
库无法可靠判断的项目，才通过固定 argv、固定超时和记录完整日志的受控 native probe 调用
`surfaceCheck`；不得让模型提供 probe 命令。

### 5.2 单位和尺度

所有内部几何长度统一转换为米，同时保留原始单位和值。Probe 只报告：

- 声明单位；
- 换算后的 bounding box；
- 与任务尺寸是否明显冲突；
- 是否存在零尺度、极端尺度或空几何。

它不能根据“看起来像毫米”自动改写用户单位。

### 5.3 patch 与 region 语义

Probe 通过 exact name、公开 alias 和用户映射建立候选关系，并保存 evidence。以下情况必须在
生成前停止为 `PATCH_MAPPING_UNRESOLVED`：

- 一个必要物理角色对应多个无法区分的 surface；
- 必需入口/出口没有候选；
- 多区域任务缺少 region/interface 对应关系；
- 用户映射引用不存在的 surface。

名字高度明确且无冲突时允许继续，但最终映射仍写入 artifact。

## 6. 网格路线

### 6.1 参数化 `blockMesh`

适用于简单二维/三维盒体、通道、楔形和少量 block 的结构拓扑。

Agent 编写：

- `system/blockMeshDict`；
- 必要 include 文件；
- `blockMesh` 与 `checkMesh` typed commands。

确定性检查只验证高置信度关系：

- vertex/index 引用合法；
- block cell counts 为正；
- 相邻 block 共享面的离散兼容性；
- arc/spline 语法可解析；
- patch face 不重复且覆盖符合声明；
- 二维任务具有一致的单层和 `empty` 语义。

几何拓扑是否最优仍由 Agent 判断。

### 6.2 STL/OBJ + `snappyHexMesh`

典型命令链为：

```text
blockMesh background mesh
-> surfaceFeatureExtract（需要时）
-> snappyHexMesh
-> checkMesh
```

Agent 必须根据 `GeometryFacts` 编写：

- geometry entries；
- refinement surfaces/regions；
- feature 配置；
- location-in-mesh；
- layer controls；
- mesh quality controls；
- patch/region 对应关系。

所有命令继续使用 `ExecutionPlan v3` 的 `mesh` 或 `check` stage。新增 executable/stage 语义必须
进入 environment discovery、policy、semantic mapping 和测试，不允许 shell wrapper。

### 6.3 Gmsh

支持两种受控输入：

```text
公开或 Agent 编写的 .geo
  -> gmsh typed command
  -> .msh
  -> gmshToFoam
  -> patch/region normalization
  -> checkMesh

公开 .msh
  -> gmshToFoam
  -> patch/region normalization
  -> checkMesh
```

Gmsh 必须作为 environment capability 显式发现。Runner 只允许解析后的 executable 与 args，
禁止 `.geo` 通过 shell 执行外部脚本。

physical group 是 Gmsh 路线的必要输入或 Agent 输出；转换后必须验证每个声明 group 都有对应
OpenFOAM patch/zone。

### 6.4 已有 OpenFOAM mesh

用户可以提供完整 `constant/polyMesh` 资产，但必须：

- 逐文件 hash；
- 无符号链接、绝对路径或目标 tutorial 泄漏；
- 通过 `checkMesh`；
- patch/region 与 TaskSpec 一致；
- 不因为已有网格而绕过后续 case authoring、Runner 和 evaluator。

## 7. MeshQualityReport

网格阶段无论成功或失败，都产生结构化报告：

```yaml
schema_version: 1
strategy:
commands_completed: []
mesh_created:
check_mesh_passed:
cells:
faces:
points:
regions:
patches: []
max_non_orthogonality:
max_skewness:
negative_volume_count:
failed_requirements: []
warnings: []
evidence_files: []
```

报告事实和 TaskSpec 中的公开阈值分开保存。`checkMesh` 的退出码、原生日志和解析后的值都必须
保留，不能只保存一个布尔值。

## 8. 失败与修复

增加清晰的前处理失败代码：

| code | 含义 | 是否允许 Agent repair |
| --- | --- | --- |
| `GEOMETRY_ASSET_INVALID` | 文件、hash、格式或基本拓扑无效 | 否，要求用户更换资产 |
| `GEOMETRY_SCALE_UNRESOLVED` | 单位或尺度冲突 | 否，要求用户确认 |
| `PATCH_MAPPING_UNRESOLVED` | 物理角色与 surface/region 无法唯一对应 | 否，要求用户确认 |
| `MESH_TOOL_UNAVAILABLE` | 所需 OpenFOAM/Gmsh 工具不存在 | 否，环境 blocker |
| `MESH_PLAN_INVALID` | 文件、命令或跨文件语义错误 | 是 |
| `MESH_GENERATION_FAILED` | native mesh command 失败 | 是，依据日志定向修复 |
| `MESH_QUALITY_FAILED` | 网格生成完成但公开质量要求未过 | 是，预算内调整 |

mesh repair 只接收：

- GeometryInput、MeshIntent 和 GeometryFacts；
- mesh-related files；
- 失败 mesh command 日志；
- MeshQualityReport；
- mesh knowledge/Skill。

默认不得修改物性、初始场、求解器或 evaluator 要求。修复后的新 attempt 从 mesh 开始完整执行，
保持现有不可变 attempt 规则。

## 9. 开发子阶段

### 9.1 `blockMesh` 可靠性

- 添加通用多 block 拓扑和 arc/spline 高置信度检查；
- 补充对应 Knowledge 与 mesh Skill；
- 复测已有结构网格失败和 holdout。

### 9.2 surface 路线

- 增加 `GeometryInput`、`MeshIntent`、asset staging 和 GeometryProbe；
- 打通 STL/OBJ、background mesh、feature、snappy 和 checkMesh；
- 验证内流与外流两类几何。

### 9.3 Gmsh 和提供网格路线

- 显式发现 Gmsh；
- 支持 `.geo -> .msh -> gmshToFoam` 和公开 `.msh`；
- 支持经过审计的 `constant/polyMesh`；
- 验证 physical groups、patch 和 region。

### 9.4 多区域与接口

- 在前三条路线稳定后增加 multi-region/interface gate；
- 不在 surface/Gmsh 最小闭环前提前建设通用 CHT 网格平台。

## 10. 测试与验收

### 10.1 确定性测试

- TaskSpec 新版本和一次性 fixture migration；
- asset hash、路径、格式、单位和大小限制；
- GeometryProbe 对合成 STL/OBJ 的 bounds、闭合性和 patch facts；
- strategy routing；
- typed command policy；
- checkMesh parser 和 MeshQualityReport；
- mesh failure classifier 与 repair scope；
- protected-path 和 tutorial leakage。

### 10.2 真实 native gate

至少包含：

1. 参数化二维多 block 通道；
2. 参数化三维弯曲或带弧边界几何；
3. STL 内流道 snappy case；
4. STL 外流场 snappy case；
5. Gmsh 二维带孔区域；
6. 公开 `.msh` 转换 case；
7. 一个多区域接口 case；
8. 一个故意破坏的 mesh，用于验证 scoped repair。

每个成功 gate 必须证明：

- geometry hash 和单位被记录；
- mesh 命令链正常完成；
- `checkMesh` 通过公开门槛；
- patch/region 与 TaskSpec 一致；
- 目标 solver 至少启动；
- artifact manifest 完整。

### 10.3 阶段完成条件

- 三种主要路线 `blockMesh`、surface/snappy、Gmsh 各至少两个独立几何通过 native gate；
- 已有简单 case 不因 TaskSpec 迁移发生功能回归；
- 高影响单位或 patch 歧义会在模型调用前快速、中文、可恢复地报告；
- mesh failure 不触发无关 physics 文件的大范围重写；
- 不以放宽 `checkMesh` 或用户质量阈值制造通过。

## 11. 产物

- 规范 TaskSpec 几何/网格字段；
- GeometryProbe 与 GeometryFacts；
- blockMesh、surface、Gmsh mesh route；
- MeshQualityReport 与 mesh failure classification；
- mesh Knowledge/Skill 和 scenario；
- 真实前处理 gate 资产与报告；
- 更新后的快速开始和功能边界说明。

完成阶段 2 后，用户仍可直接编写结构化 TaskSpec。自然语言入口在阶段 3 增加，不能反向阻塞
已经验证的结构化使用方式。
