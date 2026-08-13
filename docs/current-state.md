# FoamPilot 当前状态与新会话交接

状态：**2026-08-13 文档冻结快照**

用途：让新的开发对话不依赖此前十几个小时的上下文，也能准确区分现行架构、已验证能力、
未验证边界和代码/测试减重任务。本文描述当前事实，不替代架构规范、源码或测试。

## 1. 新对话的固定阅读顺序

1. 本文：当前状态、证据和接手动作；
2. [`../AGENTS.md`](../AGENTS.md)：仓库规则、禁止路线和验证要求；
3. [`architecture.md`](architecture.md)：已冻结的程序职责、数据流、输入输出和唯一权威；
4. [代码库减重设计](superpowers/specs/2026-08-13-codebase-consolidation-design.md)：已批准的
   等价重构目标；
5. [代码与测试减重实施计划](superpowers/plans/2026-08-13-code-and-test-consolidation.md)：
   新对话的逐步执行清单；
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
| 发布状态 | `main` 含有 `v0.2.0` 之后的大量未发布变更；不是新的正式版本 |
| 下一任务 | 只做代码和测试的等价减重，不升级版本、不打 tag、不 push |

文档提交位于生产代码基线之后，不改变运行行为。新对话开始时必须重新执行 `git status --short`
和 `git log -8 --oneline --decorate`；如果生产代码基线之后又出现本交接未记录的代码提交，应先
审计差异，不得直接套用旧行号。

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
- 请求文本没有明确声明网格长度单位。

原请求仍包含“读取 `.msh`”的陈旧措辞，但公开附件实际是原生 polyMesh。程序以资产和拓扑事实
为权威，生成 `geometry.mode=openfoam_mesh` 和 `mesh.strategy=provided`，没有把 `.msh` 文本
误当成真实资产类型。这正是“程序负责确定网格事实、模型负责受限工程推理”的预期行为。

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

这是一次**正确的正常终止**：polyMesh 坐标只有数值，不能证明是 m、mm 或其他单位；禁止模型
猜测。用户明确单位后才可形成可编译 TaskSpec。本轮没有补充单位，因此没有启动新的求解，也
没有新的 CFD 结果可宣称。

`/tmp` 路径是可丢弃实验产物，不是仓库契约。减重后的行为门禁是“同等输入仍只能因长度单位
缺失而阻断”，而不是依赖该临时文件永久存在。

### 5.4 与已有本机工程闭环的关系

另一个当前-schema 本机门禁已经验证 provided polyMesh 不被生成文件覆盖、真实 `checkMesh`/
`icoFoam` 正常结束，以及流量、运动压差、cellZone 平均速度、残差和连续性通过单一证据链进入
`DerivedMetrics`。该证据记录于 [`qualification.md`](qualification.md)。它证明本机工程路径可
闭环，但不是上述缺单位草稿的新求解，也不是第二台机器的 qualification。

## 6. 当前技术债与本次允许范围

当前最明确的认知负担集中在 TaskBuilder 输入链：

| 文件 | 当前规模 | 问题 | 本轮动作 |
|---|---:|---|---|
| `src/foampilot/taskbuilder/extraction.py` | 1205 行 | protocol、authority、两种资产协调、问题和 orchestration 集中 | 按批准设计拆成六个单责模块 |
| `tests/test_task_extractor.py` | 1681 行 | 重要风险测试和重复 fixture 混合 | 按职责拆文件并抽公共 fixture |
| `src/foampilot/agent/native_orchestrator.py` | 4691 行 | 仍然很大 | 本轮不动；先证明 TaskBuilder 减重方法 |
| `src/foampilot/cli/main.py` | 1948 行 | CLI 命令集中 | 本轮不动 |
| `src/foampilot/desktop/main_window.py` | 1738 行 | Desktop 投影集中 | 本轮不动 |

大文件本身不是删除理由。新对话只实施已批准的 TaskBuilder 代码/测试等价减重；不得顺手修复
新功能、改变 schema、放宽 authority、增加模型调用、重写 workflow 或扩大到 NativeAgent/CLI/
Desktop。若执行中发现真实功能缺陷，记录为独立后续事项，先保持本轮行为等价。

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

## 8. 接手执行顺序

新对话应直接采用以下开场目标，不重新讨论产品方向：

> 在当前 `main` 分支，完整阅读 `docs/current-state.md`、`AGENTS.md`、
> `docs/architecture.md`、已批准减重设计和实施计划。只执行 TaskBuilder 代码与测试的等价
> 减重；不新增能力、不改变职责边界、不升级版本、不打 tag、不 push。先建立新鲜基线，逐步
> 拆分并在每一步运行聚焦测试，最后完成全量、发行物和真实 polyMesh 输入门禁并提交。

具体步骤：

1. 核验工作树和提交基线；
2. 运行第 9 节 baseline，任何失败都先判断是否为已有失败；
3. 按实施计划逐模块迁移，禁止一次性重写 1200 行文件；
4. 每移动一个职责，同时移动对应测试并运行该模块聚焦门禁；
5. 删除旧实现前用 `rg` 证明只剩一个权威定义；
6. 更新架构文件目录和本文的规模/验证记录；
7. 执行完整交付门禁，审阅 diff 后只提交本次减重。

## 9. 可信基线命令

在仓库根目录执行：

```bash
git status --short
git log -8 --oneline --decorate
git diff --check

PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m compileall -q src tests

PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py \
  tests/test_task_draft.py \
  tests/test_task_draft_validation.py \
  tests/test_task_compiler.py \
  tests/test_taskbuilder_cli.py \
  tests/test_taskbuilder_semantics.py \
  tests/test_asset_contracts.py \
  tests/test_poly_mesh_inspector.py \
  tests/test_desktop_workspace.py

QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider tests
```

文档冻结前最近一次全量基线为 `1220 passed, 13 skipped`。这是预期比较点，不是允许跳过新鲜
运行的理由；完成减重时必须记录新的完整输出和 skip 原因。

发行物门禁、临时安装和真实输入复核的完整命令位于实施计划，避免本文复制容易漂移的构建
步骤。

## 10. 后续任务队列（本轮不实施）

等价减重完成且流程仍通过后，再独立判断：

1. 是否处理减重过程中记录的真实缺陷；
2. 是否继续拆分 NativeAgent、CLI 或 Desktop，前提是另写职责映射和规格；
3. 是否补充第二台干净 Ubuntu + Foundation v10 门禁；
4. 是否重启 evaluator/Knowledge/Skills 公开与私有分包；
5. 是否推进 ParaView/三维结果桥接、人工 repair、通用 continuation 或远程/HPC。

这些候选都不能混入当前 TaskBuilder 清理提交。

## 11. 本文档冻结时的新鲜验证

2026-08-13 在生产代码基线未变的前提下完成以下只读/测试验证：

| 门禁 | 结果 |
|---|---|
| 本文、README、AGENTS、架构、设计和实施计划的本地 Markdown 链接 | `62 checked, 0 broken` |
| `docs/architecture.md` 文件职责覆盖 | `198/198` 个生产 Python 文件 |
| repository docs + import boundary | `21 passed in 1.46s` |
| TaskBuilder/asset/compiler/Desktop workspace 聚焦回归 | `126 passed in 1.16s` |
| 完整 deterministic/Qt-offscreen 回归 | `1220 passed, 13 skipped in 47.57s` |
| `compileall src tests` | exit 0 |
| `git diff --check` | exit 0 |
| 真实 porousBlockage TaskDraft 再验证 | 唯一 blocking issue 仍是 `geometry.length_unit` 的 `TASK_UNIT_AMBIGUOUS` |

13 个 skip 是仓库既有的 opt-in 外部环境/真实模型/真实 OpenFOAM/发行物门禁，不应记为通过，
也不是本次纯文档变更引入的失败。本次没有重建发行物、没有运行新 CFD、没有执行第二台机器或
真实 GUI 门禁；这些动作属于实施计划最终阶段或仍为 `NOT_RUN` 的产品资格。
