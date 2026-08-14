# FoamPilot 当前状态与新会话交接

状态：**2026-08-14 TaskBuilder 减重完成；Foundation v10 多孔 case-only 编写闭环经证据真实性加固后已验证**

用途：让新的开发对话不依赖此前十几个小时的上下文，也能准确区分现行架构、已验证能力、
未验证边界和已完成的代码/测试减重。本文描述当前事实，不替代架构规范、源码或测试。

## 1. 新对话的固定阅读顺序

1. 本文：当前状态、证据和接手动作；
2. [`../AGENTS.md`](../AGENTS.md)：仓库规则、禁止路线和验证要求；
3. [`architecture.md`](architecture.md)：已冻结的程序职责、数据流、输入输出和唯一权威；
4. [代码库减重设计](superpowers/specs/2026-08-13-codebase-consolidation-design.md)：本轮已实施的
   等价重构目标和职责边界；
5. [代码与测试减重实施计划](superpowers/plans/2026-08-13-code-and-test-consolidation.md)：
   本轮步骤、门禁和交付记录；
6. 只有需要追溯某项决策或旧证据时，才进入 [`archive/README.md`](archive/README.md)。

发生冲突时，权威顺序为：当前用户决定 > `AGENTS.md` > `architecture.md` > 当前 schema/源码与
测试 > 本文 > 功能设计 > 历史计划和报告。若源码行为与冻结架构冲突，不得在“减重”中自行
选择一方；应停止该项移动并单独报告冲突。

## 2. 版本与工作树基线

| 项目 | 当前事实 |
|---|---|
| 发布版本 | `v0.2.0`，2026-08-11 的内部可追溯基线 |
| Python 包版本 | `foampilot.__version__ == "0.2.0"` |
| 当前分支 | `main` |
| 最近生产代码基线 | `55ab25f feat: reconcile native mesh task ingress` |
| 架构基线 | `60d55da docs: define current architecture contracts`，随后由本文冻结 |
| 本轮减重起点 | `9afb78b docs: prepare consolidation handoff` |
| 发布状态 | `main` 含有 `v0.2.0` 之后的大量未发布变更；不是新的正式版本 |
| 本轮状态 | 代码与测试拆分、证据真实性修复、全量、发行物和真实冷路径门禁均已完成；尚未提交 |

本轮直接在 `main` 上实施，没有创建分支或 worktree，也没有升级版本、打 tag 或 push。新对话
开始时仍应重新执行 `git status --short` 和 `git log -8 --oneline --decorate`，不得套用旧行号。

## 3. 当前产品边界

FoamPilot 当前是面向**本机 Foundation OpenFOAM 10**的自然语言 CFD Agent。它已有一条规范
求解链：公开任务/资产进入 TaskBuilder 或直接 TaskSpec，经过确定性前处理、分阶段模型推理、
设计风险门禁、完整 case 编写、typed plan、受控执行、单一证据提取、后处理和显式验收。

当前不把以下事项描述为已有能力：

- Foundation OpenFOAM 10 之外的版本兼容；
- 第二台干净 Ubuntu + Foundation v10 的跨机安装资格；
- 远程/HPC 调度；
- 任意 OpenFOAM 时间目录的通用断点续算；
- 通用 force/heat-flux 采集；
- 单元测试或 offscreen Qt 测试所不能证明的真实 Desktop 图形端到端体验；
- 公开发行包与私有 evaluator、Knowledge、Skills 的物理分离。

最后一项已决定暂缓。当前 wheel 仍可能包含仓库中的现有 Knowledge、Skills 和 qualification/
evaluator 数据，因此只适合作为本机内部软件，不应因已有 wheel 就宣称可公开推广。

## 4. 能力与证据矩阵

| 能力 | 实现状态 | 当前最高证据 | 不能外推的结论 |
|---|---|---|---|
| Runtime 配置、Foundation v10 发现、typed execution | 已实现 | 自动测试、本机 preflight/真实门禁 | 不能证明另一台机器可安装 |
| bubblewrap/host 三档策略与 host 风险扫描 | 已实现 | 自动测试和本机策略证据 | audited host 不等价于沙箱隔离 |
| TaskDraft v2 -> TaskSpec v3 | 已实现 | 自动测试和真实自然语言 TaskDraft | 非确定模型不保证每个提示词都成功 |
| 原子 polyMesh 目录资产与 `MeshFacts` | 已实现 | 真实 porousBlockage 资产检查 | polyMesh 不提供可靠物理长度单位 |
| 分阶段 intent/design/author/plan | 已实现 | replay matrix、聚焦测试、本机工程门禁 | solver 结束不等于物理正确 |
| 单一 `RunFacts` -> `DerivedMetrics` -> `ResultReport` | 已实现 | 当前 schema 本机 provided-mesh 门禁 | 尚非跨机 qualification |
| 有限 numerical repair | 已实现 | 自动测试和既有真实闭环证据 | 不能任意改物理、网格或求解器 |
| detached job、取消、重连、reconcile、strict resume/rerun | 已实现 | 自动测试与 offscreen Desktop 测试 | strict resume 不是任意断点续算 |
| Desktop 自然语言求解与实时投影 | 已实现 | offscreen 测试和既有本机使用证据 | 尚无独立真实 GUI 门禁 |
| 跨机安装和 Desktop 实机门禁 | 未执行 | `NOT_RUN` | 不能标记为 PASS 或 FAIL |
| evaluator/私有知识和 Skills 分包 | 暂缓 | 只有设计边界 | 当前 wheel 不适合公开分发 |

证据解释固定为：自动测试证明契约；solver completion 证明该进程完成；公开验收证明该 run 的
已声明检查；qualification 才能支持规定题集上的结论。四者不得互相替代。

## 5. 2026-08-13 真实 polyMesh 输入实验

### 5.1 输入

- 外部算例目录：
  `/home/edwin/workplace/openfoam-v2512-selected-100-results-from-server-20260807/`
  `case-incompressible-pisofoam-laminar-porousblockage-205447969d3f`
- 原子网格资产：上述目录下的 `mesh/openfoam/constant/polyMesh`
- install path：`constant/polyMesh`
- 请求：二维单相不可压缩牛顿层流瞬态流过局部多孔体区域；多孔区可穿透，不得解释成阀门、
  孔板、狭缝或固体障碍；请求流量、压力、速度、多孔响应与瞬态演化证据。
- 初次请求没有明确声明网格长度单位，后续用户已明确确认单位为 `m`，并确认使用 `pisoFoam`。
- 修正后的请求明确说明输入是原生 polyMesh，不再要求读取 `.msh`；准确版本见
  [`porous-blockage-native-polymesh-prompt.md`](porous-blockage-native-polymesh-prompt.md)。
- 边界语义已按真实拓扑修正：`top`/`bottom` 是 `symmetryPlane`，`frontAndBack` 是 `empty`；
  网格没有 wall patch，因此不再要求无滑移壁面。

### 5.2 确定性网格事实

| 事实 | 值 |
|---|---|
| manifest SHA256 | `6ad7b430eaf465065a1f7a14c0fff39a4f44e441e5647484c51b60fd1bd33715` |
| points / faces / internal faces / cells | `4290 / 8288 / 4000 / 2048` |
| unscaled bounds | `(-2,-2,-0.1)` 到 `(6,2,0.1)` |
| patches | `inlet: patch(32)`；`outlet: patch(32)`；`top: symmetryPlane(64)`；`bottom: symmetryPlane(64)`；`frontAndBack: empty(4096)` |
| cell zone | `porousBlockage`，64 cells |
| topology checks | owner 数与 face 数一致；cell label 连续；boundary face coverage 连续 |
| raw mesh in model context | `false`；模型只收到紧凑结构化事实 |

### 5.3 TaskDraft 结果

- 草稿：`/tmp/foampilot-porous-ingress-rerun2-20260813/task-draft.yaml`
- 草稿 SHA256：`d947d1264c0da6fe3e7f6d15b2dd90ddec67598754544db8ad72ae361dade185`
- draft id：`draft-adfdb1b53cd467b8`
- 模型逻辑请求/transport attempts：`1 / 1`
- 提取耗时：`138.6034043749969 s`
- 最终状态：`incomplete` / `TASK_REQUEST_INCOMPLETE`
- 唯一 blocking issue：`TASK_UNIT_AMBIGUOUS` at `geometry.length_unit`
- 其余 issue：四个资源预算 `TASK_DEFAULT_APPLIED` advisory，不阻断编译。

这是当时一次**正确的正常终止**：polyMesh 坐标只有数值，不能证明是 m、mm 或其他单位；禁止
模型猜测。该历史 TaskDraft 证据仍有效，但其“单位尚未补充”状态已被后续用户确认取代。

`/tmp` 路径是可丢弃实验产物，不是仓库契约。减重后的行为门禁是“同等输入仍只能因长度单位
缺失而阻断”，而不是依赖该临时文件永久存在。

减重后新增了 `tests/test_real_taskbuilder_ingress_gate.py`。它从
`FOAMPILOT_REAL_POLYMESH_CASE_ROOT` 指向的上述真实目录重新计算成员和 manifest，依次调用
`build_task_ingress_context()`、重构后的 `extract_task_draft()` 和 `validate_task_draft()`。本机运行
结果为 `1 passed`：geometry 仍为 `openfoam_mesh`，mesh strategy 仍为 `provided`，模型请求只含
紧凑 topology、没有 raw `FoamFile` 内容，唯一 blocking tuple 仍为
`("TASK_UNIT_AMBIGUOUS", "geometry.length_unit")`。这是一次新的提取器执行，不是 CFD solve。

### 5.4 2026-08-14 多孔 case-only 编写闭环

用户逐字段确认单位、`pisoFoam`、物性、边界、多孔模型和快速时间控制后，当前 `main` 工作树
完成了一次真实模型驱动但不执行 solver 的标准 plan 流程。通过的 run 为：

`/tmp/foampilot-porousblockage-fast-case-20260814/plan-runs/`
`run-20260814T073342971082Z-28d77713`

冻结工况为 `U=1e-4 m/s`、`nu=1e-6 m2/s`、参考 `Re=400`、`d=10000 m^-2`、
`f=0 m^-1`、`endTime=80000 s`、`deltaT=100 s`、`writeInterval=20000 s`。该快速时间范围只用于
case 编写验证，不证明流场已经达到稳定或准稳定。

新鲜证据如下：

- 标准流程完成 Intent、CaseDesigner、RiskGate、CaseAuthor、设计一致性检查和 ExecutionPlan
  编译，最终 `workflow_state=COMPLETED`；设计一致性报告没有 blocking issue；
- 由该 run 的冻结 design、bundle、environment 和 observation plan 确定性重编译出的最终
  case-only 计划为
  `/tmp/foampilot-porousblockage-fast-case-20260814/execution-plan-case-only.json`，包含 9 个编写
  文件和唯一 `checkMesh` 命令，不含 `solve`、`pisoFoam`、decompose、reconstruct 或 postprocess；
- 公开 asset staging 和 case materialization 接口把 case 写入
  `/tmp/foampilot-porousblockage-fast-case-20260814/case`，没有覆盖原始 polyMesh；
- `foampilot inspect` 返回 `PASS`，blocking issue 为空，并观察到 `inlet`、`outlet`、`top`、
  `bottom`、`frontAndBack` 五个 patch；
- Foundation OpenFOAM 10 `checkMesh` 返回 `Mesh OK`：2048 hexahedra、1 个 cellZone、5 个
  patches、2 个 solution directions、最大长宽比 1、最大非正交度 0；
- Foundation 10 `foamDictionary` 成功解析 `Stokes`、`nu=1e-6`、`porousBlockage`、
  `d=(10000 10000 10000)`、`f=(0 0 0)` 和 `globalCartesian` 引用。
- 结构检查加固后，使用同一冻结 design、bundle 和权威 mesh facts 重新生成的
  `/tmp/foampilot-porousblockage-fast-case-20260814/design-conformance-final.json`
  仍为 `passed=true`、blocking issue 为 0；47 条 advisory 是尚无确定性文件映射的设计事实，
  不会被伪装成已验证关系。

本轮修复保持原职责边界：Intent 只协调 region/cellZone，并拒绝把带局部名称的 global scope
静默放大；ObservationRequest 域统一拥有“`history` 覆盖同语义时刻请求”的兼容规则，
AcceptanceCompiler 和 ObservationPlanner 共同调用；CaseDesigner 协调层只在全部直接子事实已
确认且逐项完全相等时确认父级聚合值；CaseAuthor 只绑定冻结 manifest metadata。设计一致性
检查器现在按平衡字典块验证 Foundation 10 的 `simulationType laminar`、嵌套
`laminar { model Stokes; }` 以及唯一且完整嵌套的
`explicitPorositySourceCoeffs`，不再用散落关键字或 manifest metadata 代替文件结构。
兼容投影仅在显式单位齐全且所有别名完全一致时转换。TaskSpec 的确认执行开关被确定性绑定到
runner decision，PlanContext 只接受已确认的布尔 `execution.run_solver`；solver contributor 在
case-only 模式下不再要求 solver、MPI helper 或并行字典，PlanCompiler 还检查 task/design 一致
并负责最终计划中不得出现执行阶段的防御性投影。这样 case 编写、命令贡献与计划策略仍各守
原职责边界。

该 case-only run 的模型虽然生成了合规 lower_snake_case quantity，却使用 `L/T`、`L^2/T^2`、
`L^3/T` 等符号量纲，导致当时 ObservationPlanner 将 field-backed 观测标为 unavailable。这是
模型不可见本地 quantity/dimension 注册契约造成的真实缺口，不是门禁过严。当前工作树已把
第一方注册表的 canonical `kind + quantity + dimension` 组合投影到 Intent 模型上下文和 quantity
Schema 描述；输入边界只归一化注册且唯一的 quantity/dimension aliases，并保留 normalization
记录；ObservationPlanner 对 field-backed、`run_facts` 和当前 unavailable collector 一律要求
正规化后的 canonical 契约，未知值继续 fail closed。该修复不改变通用 ModelGateway、
CaseAuthor 或 Planner 职责。

上述历史 run 没有运行 `pisoFoam`，因此本节原有 case-only 证据仍不包含时间目录、solver
completion、速度/压力结果或物理验收结论；新的 canonical solve 证据必须由新的不可变 run
单独记录，不能回写或拔高这个历史 run。

### 5.5 与已有本机工程闭环的关系

另一个当前-schema 本机门禁已经验证 provided polyMesh 不被生成文件覆盖、真实 `checkMesh`/
`icoFoam` 正常结束，以及流量、运动压差、cellZone 平均速度、残差和连续性通过单一证据链进入
`DerivedMetrics`。该证据记录于 [`qualification.md`](qualification.md)。它证明本机工程路径可
闭环，但不是上述缺单位草稿的新求解，也不是第二台机器的 qualification。

## 6. TaskBuilder 等价减重结果与剩余技术债

`extraction.py` 已从 1205 行收薄为 153 行。新增文件按职责承接原实现：

| 文件 | 行数 | 唯一职责 |
|---|---:|---|
| `taskbuilder/extraction_protocol.py` | 176 | transport schema、事实路径词表和 system prompt |
| `taskbuilder/authority.py` | 294 | 重复事实归一、证据绑定和来源降权 |
| `taskbuilder/provided_mesh.py` | 279 | 已验证 polyMesh topology 的确定性协调，不做 I/O |
| `taskbuilder/public_geometry.py` | 205 | 已验证 STL/OBJ/GEO metadata 的确定性协调，不检查文件 |
| `taskbuilder/questions.py` | 152 | 输入问题白名单和最终重建 |
| `taskbuilder/extraction.py` | 153 | 唯一模型调用、串行阶段编排和 TaskDraft 组装 |
| `taskbuilder/projection.py` | 63 | validation/compiler/questions 共用的 authority 投影 |

原 `tests/test_task_extractor.py` 的 45 个规范化场景 ID 在删除前后逐项相等。测试现分为六个根级
职责文件，共 1401 行；重复 gateway、payload、public-file 和 polyMesh 构造集中到 180 行的
`tests/support/taskbuilder.py`。场景数没有减少，也没有合并不同攻击面。

`src/foampilot/agent/native_orchestrator.py`、`src/foampilot/cli/main.py` 和
`src/foampilot/desktop/main_window.py` 仍然较大。2026-08-13 的等价减重没有改动这些文件；
随后获批的多孔冷路径修复在 `native_orchestrator.py` 增加了编排接线，但没有继续拆分大文件。
大文件本身不是删除理由；如需继续减重，必须另做职责映射、规格和基线。

## 7. 等价减重不可破坏的结果

1. `foampilot.taskbuilder` 的公开 import、TaskDraft v2、TaskSpec v3 不变；
2. CLI 参数、JSON 结构、退出码、稳定英文错误码和中文 recovery 不变；
3. provided polyMesh 始终作为原子目录 ingest、hash、复制并禁止模型覆盖；
4. topology、patch、zone 和 manifest 只由程序生成，模型不能伪造 `public_asset` 权威；
5. 用户证据核验继续覆盖否定、互斥词、科学计数法、嵌套字段和语义 key；
6. 未知单位、资产冲突、不存在的 patch/zone、symlink 逃逸继续 fail closed；
7. TaskBuilder 只阻断用户/资产权威缺口，solver、物性、数值和时间设计仍属于后续阶段；
8. 模型调用次数和公开上下文上限不增加；
9. 不能为了减少测试数删除不同攻击面或不同权威来源的用例；
10. 新模块不得成为第二套事实解释器，也不得产生新的文件或进程副作用。

## 8. 2026-08-13 等价减重实施结果

本轮按批准计划直接在当前 `main` 上完成：

1. 冻结并保持原 45 个 extractor 场景；
2. 抽取显式测试支撑层，不使用隐式 conftest fixture；
3. 依次迁移 protocol、authority、provided mesh、public geometry、questions/projection；
4. 保持唯一的 `extract_task_draft()` 公共入口和一次模型调用；
5. 新增 AST 边界测试，确认只有 `extraction.py` 依赖 `foampilot.models`，新模块均不依赖
   runtime、plans、agent、workflow、desktop、CLI 或 qualification；
6. 在场景集合等价后删除原单体测试文件；
7. 新增真实 polyMesh ingress/extractor/validation 门禁。

该等价减重阶段没有改动公开 schema、CLI、NativeAgent、OpenFOAM 执行路径或 capability
matrix，也没有运行新的 CFD solve。上述边界只描述 2026-08-13 的减重提交，不覆盖第 5.4 节
记录的后续已批准多孔冷路径开发。

## 9. 可信基线命令

在仓库根目录执行：

```bash
git status --short
git log -8 --oneline --decorate
git diff --check

PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m compileall -q src tests

PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/test_taskbuilder_*.py \
  tests/test_task_draft.py \
  tests/test_task_draft_validation.py \
  tests/test_task_compiler.py \
  tests/test_asset_contracts.py \
  tests/test_poly_mesh_inspector.py \
  tests/test_desktop_workspace.py

QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider tests
```

减重前全量比较点为 `1220 passed, 13 skipped`；第 11 节记录减重后的新鲜结果和 skip 边界。

发行物门禁、临时安装和真实输入复核的完整命令位于实施计划，避免本文复制容易漂移的构建
步骤。

## 10. 等价减重完成时的后续任务队列

等价减重完成且流程仍通过后，再独立判断：

1. 是否处理减重过程中记录的真实缺陷；
2. 是否继续拆分 NativeAgent、CLI 或 Desktop，前提是另写职责映射和规格；
3. 是否补充第二台干净 Ubuntu + Foundation v10 门禁；
4. 是否重启 evaluator/Knowledge/Skills 公开与私有分包；
5. 是否推进 ParaView/三维结果桥接、人工 repair、通用 continuation 或远程/HPC。

这些候选都不能混入当前 TaskBuilder 清理提交。

## 11. 减重完成后的新鲜验证

2026-08-13 在当前 `main` 工作树完成以下门禁：

| 门禁 | 结果 |
|---|---|
| 原 extractor 场景集合 | 拆分前后 45 个规范化 scenario ID 完全相等，全部通过 |
| repository docs + import boundary | `22 passed in 1.47s` |
| 最终 TaskBuilder/asset/compiler/Desktop 聚焦回归 | `148 passed in 2.27s` |
| 完整 deterministic/Qt-offscreen 回归 | `1221 passed, 14 skipped in 46.92s` |
| `compileall src tests` | exit 0 |
| 真实 porousBlockage ingress/extractor/validation | `1 passed in 0.44s`；唯一 blocking tuple 为 `TASK_UNIT_AMBIGUOUS` / `geometry.length_unit` |
| sdist -> wheel 内容一致性 | `2 passed in 0.09s` |
| clean-wheel 导入 | 从 `/tmp` 成功导入公开 `extract_task_draft` 和五个新职责模块；所有 `__file__` 均位于临时安装目录 |
| `git diff --check` | exit 0 |

发行物摘要：

- `foampilot-0.2.0.tar.gz`：749194 bytes，SHA256
  `eff5059de27b50892aeabcf23c91e5373800b9d196c0dd7ab23782748093c67d`；
- `foampilot-0.2.0-py3-none-any.whl`：716027 bytes，SHA256
  `dcee72dce63e18c380ec5a69fa2427b513eb064d4cf682c9e9a113226e99914d`。

14 个 skip 中，13 个是仓库既有的 opt-in 外部环境、真实模型、真实 OpenFOAM、发行物或 GUI
门禁；新增的 1 个是未设置 `FOAMPILOT_REAL_POLYMESH_CASE_ROOT` 时跳过真实资产门禁。该门禁
随后已用已知本地案例显式运行并通过。该减重阶段没有运行新的 CFD solve、第二台机器
qualification 或真实 GUI 门禁，也没有升级版本、打 tag 或 push。第 5.4 节是其后的多孔
case-only 编写闭环；它已经越过 confirmation 和 authoring gate，但没有执行 CFD solver。

2026-08-14 多孔 case-only 修复与编写完成后的新鲜完整回归为
`1327 passed, 14 skipped in 47.76s`。审查加固涉及的设计一致性、多孔扩展、执行计划、Intent、
Acceptance 和 ObservationPlanner 聚焦回归为 `110 passed`；`compileall src tests` 和
`git diff --check` 均为 exit 0。这些自动测试与真实
`foampilot inspect`、Foundation 10 `checkMesh`、`foamDictionary` 共同证明当前 case-only 编写
闭环；它们不证明 `pisoFoam` completion、流场稳定或物理结果正确。

Intent 观测契约对齐及审查加固后的新鲜完整回归为
`1343 passed, 14 skipped in 48.47s`；Intent、模型 Schema、Observation registry/Planner
聚焦回归为 `71 passed`，`compileall src tests` 和
`git diff --check` 均为 exit 0。对第 5.4 节历史 `simulation-intent.json` 做只读确定性重放产生
15 条显式 alias normalization：流量、运动压差、多孔区平均速度/运动压力都从 unavailable
变为已注册的 `runtime_configuration`，残差和连续性继续使用 `run_facts`。新增反例门禁还证明
任意 residual/continuity quantity/dimension 不再绕过契约进入 `run_facts`，跨 kind、错误量纲和
歧义别名绑定也被拒绝。

随后创建了两个新的 canonical solve run，均未被冒充为 CFD 失败或通过：

- `/tmp/foampilot-porousblockage-fast-solve-contractfix-20260814/`
  `run-20260814T090504147117Z-c81a14eb` 在模型输出前因默认 Codex 状态目录只读而
  记录为当时的 `PROCESS_INTERRUPTED`；
- 同目录 `run-20260814T090537055855Z-0538675e` 使用隔离的可写 Codex 状态目录，已越过初始化，
  但两次模型传输都因当前外层网络到 `chatgpt.com/backend-api/codex/responses` 断开而
  记录为当时的 `PROCESS_INTERRUPTED`。

两者都只完成到 `CONTEXT_READY`，没有 `simulation-intent.json`、CaseAuthor、Runner、OpenFOAM
attempt 或 solver 结果。因此当前代码修复已有确定性证据和历史 Intent 重放证据，但新的规范
FoamPilot solve 仍是 `NOT_COMPLETED`，不能用此前直接 `pisoFoam` 的成功代替。

后续根因对照证明：`--ephemeral` 只禁止 Codex session rollout 持久化；仅把 SQLite 和日志目录
重定向到可写位置仍会在 app-server 初始化阶段失败，完整 `CODEX_HOME` 才是状态根边界。当前
工作树因此为内置 `codex-cli` profile 声明了状态根契约：`CODEX_HOME` 未设置时解析为
`$HOME/.codex`，有效值必须是已经存在的绝对可写目录。`model doctor` 和真实 exchange 共用同一
空文件写探针，不读取或复制状态根中的其他成员；只读/缺失状态根现在是不可重试的
`BACKEND_MISCONFIGURED`。明确的未登录、限流、过载和网络诊断分别归入 `AUTH_FAILED`、
`RATE_LIMITED`、`OVERLOADED` 和 `NETWORK_UNAVAILABLE`，未知非零退出才保留
`PROCESS_INTERRUPTED`。

该修复使本地 doctor 和失败归因可信，但不会也不能替外层宿主开放网络。新的初始 polyMesh +
提示词 canonical solve 仍需在同一个可写且已登录的 `CODEX_HOME`、可访问的模型网络和 Foundation
v10 runtime 下重新执行；在此之前 fresh CaseAuthor 证据仍为 `NOT_COMPLETED`。

本修复完成后的新鲜自动门禁为：模型后端/CLI 聚焦回归 `52 passed`，完整回归
`1359 passed, 14 skipped in 47.76s`，`compileall src tests` 和 `git diff --check` 均为 exit 0。
真实 `model doctor` 对当前只读默认状态根在约 0.0002 秒内返回
`BACKEND_MISCONFIGURED`；对新建可写但未登录的状态根返回 `AUTH_FAILED`；对此前已登录且可写的
隔离状态根返回 `PASS`。三种结果证明 doctor 使用的是有效状态根契约，而不再只是版本探测。

随后使用快速 case-only TaskSpec、原始公开 polyMesh 资产、新 run root、已登录可写状态根和
`--backend codex-cli --model-name gpt-5.6-sol` 启动了一次不复用旧 plan 的 canonical cold path：

`/tmp/foampilot-porousblockage-runtime-readiness-fresh-20260814/`
`run-20260814T095416573639Z-785ad838`

该 run 的两次 `interpret-simulation-intent` transport 分别耗时约 `35.63 s` 和 `36.54 s`，均在
零输出字节时明确记录 `NETWORK_UNAVAILABLE`。最终 `workflow_state=DEFERRED`、
`last_completed_stage=CONTEXT_READY`，没有 `simulation-intent.json`、CaseAuthor、native attempt
或 case 文件。这条证据证明新的错误分类已进入 FoamPilot 自身 solve 编排；它也再次确认当前
环境还不能完成“初始 polyMesh + 提示词 -> fresh Case”，原因是外层模型网络不可用，而不是
Codex 状态根、Intent Schema 或 CaseAuthor。

上述网络失败是当时外层沙箱内的历史证据，不再代表当前最新状态。允许模型子进程访问网络后，
使用同一快速 case-only TaskSpec、原始公开 polyMesh、已登录可写 `CODEX_HOME` 和 Foundation
OpenFOAM 10 启动了新的、未复用旧 plan 的 canonical cold path：

`/tmp/foampilot-porousblockage-authorized-fresh-20260814/`
`run-20260814T110624240272Z-c4cc7ff6`

该 run 从输入资产和提示词重新完成 Intent、CaseDesigner、RiskGate、CaseAuthor、CaseVerifier、
PlanCompiler、case 物化和 Runner。Intent transport 在约 `97.5 s` 后成功；Design 第一次响应未
通过结构化 Schema，第二次 transport 在约 `77.1 s` 后成功；CaseAuthor transport 在约
`76.5 s` 后成功。最终 `workflow_state=COMPLETED`、`native_status=RUN_COMPLETED`、
`last_completed_stage=RUN_FINALIZED`，case 位于 `attempt-01/case/`。

本次修复保持既有职责边界：TaskSpec 的 `required_outputs` 作为确定性任务合同覆盖模型复述；
设计一致性检查接受 Foundation 10 合法的 `laminar Stokes` manifest 表述；Foundation 10 porous
扩展通过 `CapabilityDescriptor.authoring_rules` 声明自己的精确字典约束，通用 CaseAuthor 只接收
已冻结扩展规则并编写 CaseBundle。多孔扩展要求 `selectionMode`、`cellZone`、`type`、`d`、`f`
和 `coordinateSystem` 位于 `explicitPorositySourceCoeffs` 内；CaseVerifier 仍独立拒绝错误层级、
错误 zone 或与冻结设计不一致的阻力系数，不做静默文本修补。

物化 case 包含 `0/U`、`0/p`、`constant/physicalProperties`、
`constant/momentumTransport`、`constant/coordinateSystems`、`constant/fvModels` 以及完整的
`system` 字典。ExecutionPlan 只包含 `checkMesh`；它在 bubblewrap 中返回 `0`，报告
`Mesh OK`、2048 个六面体、5 个 patch 和 1 个 cellZone。快速方案明确冻结
`execution.run_solver=false`，所以该证据证明“原始 polyMesh + 提示词 -> fresh Case -> 原生网格
检查”的 FoamPilot solve 编排已经走通，但不证明 `pisoFoam` 启动、求解完成、流场稳定或物理
结果正确。

上述修复后的新鲜完整回归为 `1361 passed, 14 skipped in 47.70s`；多孔扩展、CaseAuthor、
设计一致性、native state machine 和 CLI 聚焦回归为 `126 passed`，`git diff --check` 为 exit 0。

### 11.1 2026-08-14 case-only 证据真实性加固与替换运行

独立复审发现，上述 `c4cc7ff6` 历史 run 的 case 编写和 `checkMesh` 进程虽然真实发生，但其
`run-facts.json` 把 `checkMesh` 输出的 `Time = 0`/`End` 错记为 `solver_progress`，继而把
`run-assessment.json` 写成 `NORMAL_SOLVER_END`；同一历史 case 的 `controlDict` 还含一套模型
编写的 `functions`，并同时 include 系统生成的 `foampilot-observations`。因此该历史 run 不再作为
solver completion 或观测配置所有权的当前证据。它从未运行 `pisoFoam`，该事实不因旧 assessment
文件而改变。

当前修复保持文件职责边界并 fail closed：

- 只有 `stage=solve` 的日志可以产生 solver progress、残差、连续性、Courant 和写出时间证据；
  没有 solve stage 且 `checkMesh` 成功时使用独立原因 `CASE_AUTHORING_CHECKS_PASSED`；任何声称
  成功的 `PlanRunResult` 都必须以 raw 或 reused step 覆盖计划中的全部命令，否则以
  `RUN_RESULT_INCOMPLETE` 拒绝；
- CaseAuthor 不再接收 `ObservationPlan`，并被明确禁止编写 function objects；运行期
  `functions` 只由 `observations/openfoam10.py` 注入；即使 ObservationPlan 为空，任何模型编写
  文件中的顶层 `functions` 也会在注入前拒绝；
- Foundation 10 多孔坐标系必须包含唯一 `cartesian` 类型、有限三维 `origin`、唯一
  `coordinateRotation`、`axesRotation` 以及非零且不共线的 `e1`/`e2`；若冻结设计含
  `regions.<zone>.coordinate_system`，编写出的 `origin`/`e1`/`e2` 还必须逐值一致；
- 原生模型 transport 上限统一为 9，confirmation、lineage、Pydantic 边界、摘要
  `resume.allowed` 和实际 ledger 使用同一剩余预算；
- CaseDesigner normalizer 不再删除跨容器重复事实，也不再为空候选伪造 provenance；这些输出
  必须由严格 schema 拒绝并进入模型 correction。

修复后使用原始公开 polyMesh、同一快速 case-only TaskSpec、新 run root、已登录可写
`CODEX_HOME` 和本机 Foundation OpenFOAM 10，执行了新的、未复用旧 Case/Plan 的 canonical
cold path：

`/tmp/foampilot-porousblockage-corrected-fresh-20260814/`
`run-20260814T114302919621Z-de45dd3a`

该 run 的第一份 Intent 输出在约 `98.1 s` 后被严格 Schema 拒绝，第二次约 `87.5 s` 后通过；
CaseDesigner 和 CaseAuthor 分别在约 `95.4 s`、`70.1 s` 后首次通过，所有成功 transport 的
`normalizations` 均为空。随后系统拥有的 `checkMesh` 在约 `0.2 s` 内返回 0 和 `Mesh OK`。
最终 `workflow_state=COMPLETED`、`native_status=RUN_COMPLETED`、
`last_completed_stage=RUN_FINALIZED`，摘要明确写为“case 编写和确定性检查完成；未运行 solver”。

替换运行的关键事实为：

- `run-assessment.json` 为 `CASE_AUTHORING_CHECKS_PASSED`；
- `solver_progress`、`residuals`、`continuity`、`courant`、`written_times` 和 `output_files` 全为空；
- `mesh_checks[0]` 为 `mesh_ok=true`，包含 2048 cells、8288 faces、4290 points 和 1 region；
- `ResultReport.verdict=NOT_REQUESTED`，没有把不可用观测或无阈值条件制造为 PASS；
- `controlDict` 没有顶层 `functions`，只 include 一次系统文件；系统文件包含唯一一套运行期采集器；
- `coordinateSystems` 使用 `porousCS` 的合法 `cartesian/axesRotation` 结构，
  `fvModels` 以 `explicitPorositySourceCoeffs` 引用它；
- `design-conformance.json` 为 0 blocking issue、47 条非阻断 advisory；artifact manifest 校验为空；
- case 文件位于 `attempt-01/case/`，包括 `0/U`、`0/p`、完整 `constant`/`system` 字典和原始
  `constant/polyMesh`。

追加证据完整性和所有权修复后，真实 run 的已有 artifact 已用当前提取器重放：计划与 raw
结果都只含 `check-mesh-default`，`mesh_ok=true`，solver progress 和写出文件仍为空。最终完整
Qt-offscreen 回归为 `1379 passed, 14 skipped in 52.02s`；`compileall src tests` 和
`git diff --check` 均为 exit 0。重新构建的隔离 wheel 为
`foampilot-0.2.0-py3-none-any.whl`，大小 738914 bytes，SHA256
`c48a69d47c1228be4b32206bd3fee848132d507b59ecf90c812258fe4d95c879`；从临时安装目录导入后，
多孔 physics 扩展、证据评估、系统观测、设计 normalizer、lineage 和共享预算模块均来自该 wheel。
