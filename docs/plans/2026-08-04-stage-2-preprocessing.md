# 第二阶段：前处理能力实施计划

> 执行约束：第一阶段核心实现通过确定性 gate 后内联实施，不分派子代理。

**目标：** 把 FoamPilot 从仅依赖 prompt 描述几何，扩展到可验证的参数化、surface、Gmsh 和
已有 OpenFOAM 网格输入。

**架构：** 将可选 `GeometryInput` 和 `MeshIntent` 纳入唯一 TaskSpec v2；公开资产在路由前由
GeometryProbe 探测；所有网格命令继续由模型写入 ExecutionPlan v3；原生日志由确定性程序生成
MeshQualityReport。不增加第二个 mesh runner 或 case renderer。

**技术栈：** Python 3.12、Pydantic v2、PyVista、PyYAML、pytest、Foundation OpenFOAM v10
网格工具、可选 Gmsh。

## 全局约束

- 不 commit、不 push；
- 只保留一条规范 solve 路径；
- Agent 编写的命令保持 typed，不接受 shell 或 `Allrun`；
- 不推断 STL/OBJ 单位；
- 保留公开资产 hash 和 tutorial/golden 隔离；
- Gmsh 不存在时记录环境未评估，不把它记为 OpenFOAM 或 Agent 失败。

---

### 任务 1：规范几何和网格任务模型

**文件：**

- 新增 `src/foampilot/tasks/geometry.py`；
- 修改 `src/foampilot/tasks/models.py` 和 `src/foampilot/tasks/__init__.py`；
- 修改 TaskSpec、qualification 和 replay fixture 测试及仓库任务 YAML。

- [x] 覆盖参数化、surface、Gmsh、已有网格、缺单位、重复 role 和未声明附件；
- [x] 实现最小严格模型和跨字段校验；
- [x] 一次性迁移仓库拥有的 TaskSpec 到 schema v2；
- [x] TaskSpec、qualification 加载和合成 replay 测试通过。

### 任务 2：GeometryProbe 与 GeometryFacts

**文件：**

- 新增 `src/foampilot/preprocessing/models.py` 和 `geometry_probe.py`；
- 新增 `tests/fixtures/preprocessing/` 与 `tests/test_geometry_probe.py`。

- [x] 使用合成 STL/OBJ 覆盖 bounds、单位换算、空几何、缺附件和 patch 映射；
- [x] 实现有界 PyVista 探测，不运行模型命令；
- [x] 以稳定 geometry code 拒绝单位歧义和冲突映射；
- [x] probe、hash 和路径约束测试通过。

### 任务 3：主状态机集成

**文件：**

- 修改 `workflow/models.py`、`agent/native_orchestrator.py`、`agent/context.py`、
  `agent/prompts.py`、`routing/router.py` 和相关测试。

- [x] 证明 geometry probe 发生在 context/generation 之前；
- [x] 覆盖显式 surface、Gmsh 和已有网格策略路由；
- [x] 增加 `GEOMETRY_READY` 和正确的 task-domain failure；
- [x] 以有界 JSON 把 GeometryFacts 传入 routing/context/prompt；
- [x] routing、prompt、workflow、continuation 和状态机测试通过。

### 任务 4：外部网格工具的统一 executable 边界

**文件：**

- 修改 environment、plan、inspection、runtime、continuation 和 validation 模块及测试。

- [x] Gmsh 只在环境实际发现时进入 available executable 集合；
- [x] 实现统一 executable-name 属性并更新所有 policy/runner 调用；
- [x] 增加 `surfaceCheck`、`surfaceFeatureExtract`、`snappyHexMesh`、`gmsh`、
  `gmshToFoam` 的 stage 映射和 mesh 分类；
- [x] environment、plan、inspection、runner 和 continuation 测试通过。

### 任务 5：MeshQualityReport

**文件：**

- 新增 `src/foampilot/preprocessing/mesh_quality.py`；
- 修改 validation、artifact、improvement、workflow 和 orchestrator 模块；
- 新增 `tests/test_mesh_quality_report.py`。

- [x] 覆盖 Mesh OK、失败检查、cell/face/point、non-orthogonality、skewness 和缺失指标；
- [x] 实现有界日志解析和显式 unavailable；
- [x] 在公开验证前写入 `mesh-quality-report.json` 并记录 workflow evidence；
- [x] 阈值失败映射为 `MESH_QUALITY_FAILED`，不修改 evaluator 容差；
- [x] mesh-quality、validation 和状态机测试通过。

### 任务 6：限定范围的 mesh repair、Skill 与 Knowledge

**文件：**

- 修改 `agent/repair.py`、`context/skill_registry.py` 和语义检查；
- 新增 `src/foampilot/skills/openfoam-mesh-workflow/`；
- 新增 snappy surface 与 Gmsh physical-group 知识并重建 manifest；
- 修改 repair、context、Skill、knowledge 和 leakage 测试。

- [x] 拒绝 mesh repair 中无关的物性、求解器和初始内场修改；
- [x] 只在 geometry/mesh 任务中加载可选 mesh Skill；
- [x] 只有 patch/boundary 直接证据存在时才允许同步初始场 `boundaryField`；
- [x] 公开 mesh 知识通过校验并重建 manifest；
- [x] repair、context、Skill 和 leakage 测试通过。

### 任务 7：真实 gate 与文档

- [x] 全仓确定性测试通过；
- [x] preflight 记录 Gmsh 不可用和 audited-host fallback，不触发权限询问；
- [x] 一个真实 `blockMesh -> checkMesh -> icoFoam` continuation/repair gate 通过；
- [ ] 再完成一个独立 blockMesh gate；
- [ ] 完成两个 surface/snappy 原生 gate；
- [ ] 完成两个 Gmsh/已有网格 gate；当前机器没有 Gmsh，Gmsh 记为环境未评估；
- [ ] 完成一个多区域和一个故意触发 mesh repair 的真实 gate；
- [x] 已成功 gate 的 target solver start 和 artifact manifest 得到验证；
- [x] 更新当前中文文档和证据范围明确的阶段报告；
- [x] 运行 `git diff --check` 并保持工作区未提交。
