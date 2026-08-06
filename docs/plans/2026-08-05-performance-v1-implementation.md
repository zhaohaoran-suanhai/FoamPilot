# FoamPilot Performance v1 实施计划

> **执行约束：** 在当前会话内联执行；每项行为变更先写失败测试，再做最小实现。不得启动子代理，
> 不得自动 commit 或 push。

**目标：** 在不改变 `NativeAgent.solve()` 主状态机、安全 Runner、公开验证和 qualification 冷路径
边界的前提下，为冷路径增加可复算性能证据，并为工程重复运行增加显式、严格、可审计的计划、
网格和 repair 前序产物复用。

**架构：** 新增 `foampilot.performance` 包，集中保存性能模型、证据聚合、计划复用校验、派生缓存和
repair 依赖判定。`NativeAgent` 仍是唯一编排入口，`PlanRunner` 仍是唯一执行器；性能层只选择
ExecutionPlan 来源、恢复已验证派生产物和生成待执行 command 子序列。

**技术栈：** Python 3.12、Pydantic v2、现有 JSON/JSONL artifact、Foundation OpenFOAM v10、pytest。

## 全局约束

- 严格遵守 `docs/design/performance-v1-design.md` 的 P0→P4 顺序。
- qualification 默认禁用 plan/cache/reuse，warm performance 不计入泛化准确率。
- cache miss 或 repair reuse unsafe 必须退回现有冷路径；显式 plan reuse 拒绝不得静默调用模型。
- 不增加数据库、常驻服务、renderer、MCP、Rust/C++ 或第二套 Runner/状态机。
- 复用内容只能 copy/reflink，禁止可写 hardlink；source run 和 parent attempt 不得被修改。
- 所有稳定原因码保留英文，面向用户的说明使用中文。

---

### Task 1：P0 运行性能证据与 manifest 计时

**文件：**

- Create: `src/foampilot/performance/__init__.py`
- Create: `src/foampilot/performance/models.py`
- Create: `src/foampilot/performance/reporting.py`
- Modify: `src/foampilot/artifacts/store.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Test: `tests/test_performance_reporting.py`
- Test: `tests/test_artifact_store.py`

**接口：**

- `PerformanceSummary(path_kind, stages, model, reuse, diagnostics)`：schema v1 严格模型。
- `build_performance_summary(run_dir, *, path_kind, reuse) -> PerformanceSummary`：只读取已落盘证据。
- `ArtifactStore.finalize()` 在 manifest 顶层写 `build_seconds`，但 manifest 不回写其他 artifact。

- [x] 先写测试：固定 workflow event、model trace、ExecutionPlan 和 PlanRunResult，断言各阶段耗时、
  `time_to_first_openfoam_command_seconds`、logical request、transport attempt 与 retry delay 可复算。
- [x] 运行目标测试并确认因 `foampilot.performance` 不存在而失败。
- [x] 实现严格模型和聚合器；证据不足写 `null` 与 diagnostics，未发生阶段写 `0.0`。
- [x] 在 `_finish()` 的 `RUN_FINALIZED` 事件和 summary 落盘后、artifact manifest 前写
  `performance-summary.json`。
- [x] 修改 `ArtifactStore.finalize()`，以单调时钟记录内容 manifest 构建耗时；补充 exclusive 和
  verify 回归测试。
- [x] 运行 P0 测试和 native state-machine 测试。

### Task 2：P0 TaskBuilder 与 qualification 汇总

**文件：**

- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/qualification/models.py`
- Modify: `src/foampilot/qualification/reporting.py`
- Test: `tests/test_taskbuilder_cli.py`
- Test: `tests/test_qualification_reporting.py`

**接口：**

- `foampilot task draft` 在 `<output>.performance.json` 写 extraction 总耗时、logical requests、
  transport attempts 和 retry delay。
- qualification 汇总读取每个 run 的 `performance-summary.json`，分别报告 cold/warm pre-solve
  p50/p95、end-to-end p50/p95、solver entry/completion/validation 与 blocker 计数。

- [x] 写失败测试，证明 TaskBuilder 性能文件与 solve run 分离，qualification 缺失证据时不会猜测。
- [x] 实现 TaskBuilder trace 落盘和汇总字段，保持旧 report reader 兼容。
- [x] 运行 TaskBuilder、qualification 和 CLI 回归测试。

### Task 3：P1 显式复用已验证 ExecutionPlan

**文件：**

- Create: `src/foampilot/performance/plan_reuse.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/artifacts/models.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/cli/main.py`
- Test: `tests/test_verified_plan_reuse.py`
- Test: `tests/test_native_agent_cli.py`

**接口：**

- `VerifiedPlanSource.load(source_run, task, environment, public_asset_root) -> VerifiedPlanSource`。
- `NativeAgent.solve(..., reuse_verified_plan: Path | None = None)`；复用路径允许 `gateway=None`。
- CLI 增加 `solve --reuse-verified-plan SOURCE_RUN_DIR`，qualification parser 不暴露此参数。

- [x] 写失败测试：exact TaskSpec/source manifest/mesh OK/solver normal end 命中且模型调用为零。
- [x] 写拒绝测试：TaskSpec、asset 字节、OpenFOAM、schema、solver executable、MPI/resource 任一变化
  返回 `PLAN_REUSE_REJECTED`，且 case 尚未物化、模型未调用。
- [x] 实现 source manifest 校验、资格检查、兼容键和 `plan-reuse.json`。
- [x] 让复用只替换 plan 来源，后续 normalization、policy、inspection、Runner 和 validation 原样执行。
- [x] 运行 P1、continuation、CLI 和 state-machine 回归测试。

### Task 4：P2 内容寻址 GeometryFacts 与网格缓存

**文件：**

- Create: `src/foampilot/performance/derived_cache.py`
- Modify: `src/foampilot/runtime/models.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/cli/main.py`
- Test: `tests/test_derived_cache.py`
- Test: `tests/test_native_agent_state_machine.py`

**接口：**

- `DerivedCache(root)` 提供 `load/store_geometry()` 与 `load/store_mesh()`；entry 使用内容寻址目录、
  独立 manifest 和原子 rename。
- `NativeAgent.solve(..., derived_cache: Path | None = None)`；CLI 增加 `solve --derived-cache ROOT`。
- `PlanRunResult.reused_steps` 只记录来源引用，不伪造 native return code。

- [x] 写 GeometryFacts hit/miss/invalid 测试，覆盖资产字节、单位、role 和 probe version 变化。
- [x] 写 mesh hit 测试，断言 mesh generator 调用次数为零，但当前 `checkMesh` 与阈值评价仍执行。
- [x] 写 miss/隔离测试，覆盖 mesh 文件、plan mesh 字典、command、工具版本、region 和动态网格变化。
- [x] 实现安全 copy、内容 manifest、cache quarantine 和 `execution-reuse.json`。
- [x] 运行 P2、preprocessing、Runner、validation 和 state-machine 回归测试。

### Task 5：P3 repair 依赖判定和阶段复用

**文件：**

- Create: `src/foampilot/performance/repair_reuse.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/runtime/models.py`
- Test: `tests/test_repair_stage_reuse.py`
- Test: `tests/test_native_repair.py`

**接口：**

- `classify_repair_rerun(plan, decision) -> RepairReuseDecision` 返回 `mesh|initialize|solve|postprocess`、
  reason codes 和可复制路径。
- `prepare_repair_reuse(parent_attempt, next_attempt, decision) -> ExecutionReuseRecord` 在物化后安全
  恢复前序产物，并生成保持原顺序的 command 子序列。

- [x] 写失败测试：只改 `fvSchemes/fvSolution` 不运行 mesh generator；改 `0/` 从 initialize；改
  mesh/patch/command/include 或多区域/动态 mesh 从 mesh。
- [x] 写 parent hash 不变、copy 后 hash 相同、缺失/损坏证据安全回退测试。
- [x] 实现分类器、复制器与 `execution-reuse.json`；新 attempt 仍重新运行当前 `checkMesh`。
- [x] 运行 repair、continuation、validation 和 state-machine 回归测试。

### Task 6：P4 冷路径模型调用和 fail-fast 收尾

**文件：**

- Modify: `src/foampilot/models/gateway.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/qualification/reporting.py`
- Modify: `docs/system-overview.md`
- Modify: `docs/independent-agent-quickstart.md`
- Create: `docs/reports/2026-08-05-performance-v1.md`
- Test: `tests/test_model_gateway.py`
- Test: `tests/test_native_agent_state_machine.py`

**接口：**

- 确定性本地/配置错误不重试；只有网络、过载、限流、timeout 和可恢复流中断退避。
- 结构化 TaskSpec 不触发 TaskBuilder；显式/唯一 family 不调用 routing model；authoring 保持一次 bundle。

- [x] 用 P0 fixture 冻结实施后的 logical request、transport attempt、solver entry 与 validation 指标。
- [x] 写 deterministic backend error 单次 transport 和中文稳定错误测试；现有 Gateway 已满足，无需行为修改。
- [x] 不根据样本臆调 timeout；只有 p50/p95 证据支持时才修改 budget 数值。
- [x] 更新中文流程文档和实测报告，明确 cold/warm/qualification 边界。
- [x] 运行完整 deterministic suite、wheel、preflight、model doctor、真实最小 OpenFOAM gate、P1 warm
  gate 和 P2 mesh-cache gate。
