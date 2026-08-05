# 第一阶段：Knowledge 与 Skills 实施计划

> 执行约束：在当前会话内按任务内联实施，不分派子代理。

**目标：** 不改变 FoamPilot 规范求解路径，提升 solver-family 上下文覆盖和 repair 定向性。

**架构：** 保留按槽位检索的 ContextAssembler 和公开知识库，增加确定性 coverage 报告、六个
物理族 Skill，以及使用失败证据的 error-playbook 检索。官方 example 仍只属于 evaluator/teacher
边界，promotion 继续离线并需要人工批准。

**技术栈：** Python 3.12、Pydantic v2、PyYAML、pytest、现有 FoamPilot CLI、Foundation
OpenFOAM v10。

## 全局约束

- 在已授权的 `/home/edwin/workplace/FoamPilot` 当前 `main` 工作区实施；
- 不 commit、不 push；
- 不增加逐题 Knowledge、Skill、renderer、目标 tutorial 文件、golden value 或私有 evaluator 规则；
- 第一阶段运行时只允许一个通用 Skill 加至多一个物理族 Skill；
- 新行为先写测试并确认按预期失败；
- 保留 `NativeAgent.solve()` 和不可变产物主链。

---

### 任务 1：知识覆盖报告

**文件：**

- 新增 `src/foampilot/knowledge/coverage.py`；
- 修改 `src/foampilot/knowledge/__init__.py` 和 `src/foampilot/cli/main.py`；
- 新增 `tests/test_knowledge_coverage.py`。

**接口：** 以六个受审查物理族为行、知识类型为列，输出 `covered`、`partial`、`missing` 或
`development_only`；coverage 只表示候选条目存在，不代表求解通过。

- [x] 编写 solver-family、缺失槽位和 CLI 的先失败测试；
- [x] 实现不可变 Pydantic 模型、固定 family-to-solver registry 和确定性排序；
- [x] 导出 API 并增加 `foampilot knowledge coverage`；
- [x] coverage、CLI 和 corpus 校验通过。

### 任务 2：六个物理族 Skill

**文件：**

- 修改 `src/foampilot/context/skill_registry.py` 和 `src/foampilot/skills/scenarios.yaml`；
- 新增不可压缩压力速度、可压缩瞬态、VOF、浮力/CHT、固体力学、标量/势场六个 Skill；
- 删除被替代的窄 `openfoam-buoyant-case` 和 `openfoam-rhocentral-case`；
- 修改 Skill、registry 和 context 测试。

- [x] 覆盖六个 family 和一个未映射窄 solver 的映射测试；
- [x] 实现 registry 映射和六份简洁中文 Skill；
- [x] 为每份 Skill 增加正例、反例、压力和边界 scenario；
- [x] registry/scenario 测试通过后删除旧 Skill；
- [x] 所有 Skill 结构与 context 路由测试通过。

### 任务 3：repair 失败证据检索

**文件：**

- 修改 `src/foampilot/context/assembler.py`、`src/foampilot/agent/context.py`、
  `src/foampilot/agent/native_orchestrator.py` 和相关测试。

- [x] 证明压力参考等原生日志只在 repair 阶段选择匹配 playbook；
- [x] 增加有界日志归一化，并只把失败证据附加到 error-playbook 槽位；
- [x] 普通 repair 和 continuation repair 都传递公开反馈及失败日志尾部；
- [x] 继续持久化所选 ID/hash，不保存完整 prompt；
- [x] context、continuation 和状态机测试通过。

### 任务 4：通用 error playbook 与 blockMesh 知识

**文件：**

- 新增缺失 `fvSchemes` 算子、缺失 `fvSolution` solver、thermo 失稳、boundary/patch 不一致四条
  error playbook；
- 新增多块 `blockMesh` 拓扑一致性知识；
- 重建 `src/foampilot/knowledge/knowledge-manifest.json`；
- 修改 corpus、retrieval、leakage 和 provenance 测试。

- [x] 为五类查询编写先失败测试；
- [x] 增加 Foundation v10 范围、来源 hash 和无目标专用参数的条目；
- [x] 重建 manifest；
- [x] knowledge validation、retrieval、source hash 和 leakage 测试通过。

### 任务 5：文档和证据报告

**文件：**

- 修改 `docs/knowledge-governance.md`、`docs/solver-family-self-checks.md`、
  `docs/system-overview.md`；
- 新增 `docs/reports/2026-08-04-stage-1-knowledge-skills.md`；
- 修改 `tests/test_repository_docs.py`。

- [x] 增加 coverage 和 family Skill 边界的文档断言；
- [x] 更新主文档并建立带证据表的阶段报告；
- [x] 文档测试通过。

### 任务 6：验证 gate

- [x] 全仓确定性测试通过；
- [x] knowledge validate 与 coverage 命令通过，当前 corpus 为 43 条；
- [x] 通用、六个 family 和 mesh Skill 的结构/scenario 测试通过；
- [x] preflight 与 model doctor 快速返回；
- [x] 不可压缩真实 `blockMesh -> checkMesh -> icoFoam` gate 达到
  `PUBLIC_VALIDATION_PASS`；
- [ ] 完成可压缩、VOF、浮力/CHT 和固体的独立重复真实 gate；
- [ ] 用冻结 backend/model/资源快照重新运行 30 题 suite，并与已发布基线比较；当前外层
  只读沙箱不能完成 Codex CLI 深层生成，不能把历史基线当成本阶段新结果；
- [x] 运行 `git diff --check`，检查变更范围并保持工作区未提交。
