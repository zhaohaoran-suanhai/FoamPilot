# Knowledge/Skills 阶段 1.2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本计划由当前会话内联执行，不使用子代理。

**Goal:** 让已经被正确检索的 Foundation OpenFOAM v10 solver guide 通过 family Skill 被完整执行，优先消除复杂多相、可压缩 VOF 和反应流中“知识已存在但 case 仍逐关键词失败”的问题。

**Architecture:** 保持 `TaskSpec → CapabilityProfile → slot context → ExecutionPlan v3 → Runner → validation → bounded repair` 唯一主链。新增一个跨 solver 的多相耦合 family Skill，把 `compressibleInterFoam` 接入现有 VOF Skill、把 `reactingFoam` 接入现有可压缩 Skill，并在通用 Skill 中规定 selected solver guide 的必需项必须先形成原子清单；不修改 Runner、Gateway、TaskSpec、ExecutionPlan、evaluator 或 repair 数据结构。

**Tech Stack:** Python 3.12、Pydantic v2、PyYAML、pytest、Foundation OpenFOAM v10、Markdown/YAML package data、FoamPilot CLI。

## Global Constraints

- 当前验证目标固定为 Foundation OpenFOAM v10。
- 只使用已经冻结的 Agent run、公开日志、仓库内正式 Knowledge 和公开 Foundation v10 源码证据。
- 不向 author/repair 暴露目标 tutorial、golden、私有 evaluator、目标路径或 tolerance。
- 不新增逐题 Knowledge、逐题 Skill、renderer、CaseSpec 或第二套 solve 路径。
- 每次运行最多加载一个通用 Skill、一个 family Skill，以及任务存在 geometry/mesh 时的 mesh Skill。
- 正确 solver guide 已经包含的规则不复制成第二条 Knowledge；本轮优先修复 Skill 选择和知识遵从。
- Skill 只规定编写、自检和 repair 顺序，不把 CFD 策略判断下沉为全局 blocking inspector。
- qualification 继续使用 `NativeAgent.solve()`，并保持 backend/model、suite、资源和 evaluator protocol 可比较。
- 真实复测串行启动题目；每道题内部可以使用 TaskSpec 允许的 MPI ranks，但不超过 16。
- backend/environment failure 与 case/solver/qualification failure 分开统计；provider deferred 不得改写为 Agent 错误。
- 保留当前工作区中 Agent Harness v2 规格及其 README 索引，不重置或覆盖。
- 未经用户单独授权，不执行 `git commit`、`git push` 或 remote 修改。

---

## 证据基线

当前冻结报告显示：

```text
既有 30 题：30/30 generation，28/30 target solver started，23/30 normal completion，21/30 public validation
新增 20 题有效视图：19/20 generation，19/20 target solver started，11/20 normal completion/public validation
环境或 bubblewrap 阻断：0
```

以下四个 post-learning gate 都选中了正确 solver guide，但只加载了
`openfoam-author-native-case`，没有 family Skill：

| solver | 已选 Knowledge | attempt-02 最早失败 |
| --- | --- | --- |
| `driftFluxFoam` | `of10.solver.driftfluxfoam-contract` | `div(tauDm)` 缺失 |
| `multiphaseEulerFoam` | `of10.solver.multiphaseeulerfoam-contract` | `phaseTransfer` table 缺失 |
| `reactingFoam` | `of10.solver.reactingfoam-contract` | 错写 `unityLewis`，运行时只接受 `unityLewisFourier` 等 |
| `compressibleInterFoam` | `of10.solver.compressibleinterfoam-contract` | `alpha.liquid` solver entry 缺失 |

四条正式 solver guide 的当前版本已经逐项写明上述 reader contract。因此本轮不再添加相同知识，
而是让模型按 solver guide 的成组必需项一次完成文件。

## 文件职责与修改地图

| 文件 | 职责 | 本轮变化 |
| --- | --- | --- |
| `src/foampilot/context/skill_registry.py` | solver 到最多一个 family Skill 的确定性映射 | 登记四个已有 solver |
| `src/foampilot/skills/openfoam-author-native-case/SKILL.md` | 所有原生 case 的通用行为契约 | 增加 selected solver guide 原子清单规则 |
| `src/foampilot/skills/openfoam-multiphase-coupled/SKILL.md` | drift-flux 与 Euler-Euler 多相编写顺序 | 新建一个跨 solver family Skill |
| `src/foampilot/skills/openfoam-multiphase-coupled/agents/openai.yaml` | 新 Skill 的用户界面 metadata | 新建 |
| `src/foampilot/skills/openfoam-multiphase-vof/SKILL.md` | 不可压缩/可压缩 VOF 与 miscible 两液体 | 增加 `compressibleInterFoam` 分支 |
| `src/foampilot/skills/openfoam-multiphase-vof/agents/openai.yaml` | VOF Skill metadata | 扩展描述范围 |
| `src/foampilot/skills/openfoam-compressible-transient/SKILL.md` | 可压缩和反应流 reader 顺序 | 增加 `reactingFoam` 分支 |
| `src/foampilot/skills/openfoam-compressible-transient/agents/openai.yaml` | 可压缩 Skill metadata | 扩展描述范围 |
| `src/foampilot/skills/scenarios.yaml` | family Skill 的 trigger/non-trigger/pressure contract | 新增 coupled multiphase，并扩展两个既有场景 |
| `tests/test_skill_registry.py` | solver-family Skill 映射 | 增加四个 solver 的精确映射测试 |
| `tests/test_context_assembler.py` | 实际上下文装配和最多一个 family Skill | 验证四个 task 的 Knowledge + Skill 同时出现 |
| `tests/test_packaged_skills.py` | wheel/package 中 Skill 内容契约 | 验证新 Skill 和三类原子清单 |
| `tests/test_skill_validation.py` | Skill 结构与 scenario 完整性 | 将新 Skill 纳入仓库级验证 |
| `docs/design/knowledge-skills-design.md` | Knowledge/Skills 现行状态 | 将已完成 1.1 和本轮 1.2 状态写准确 |
| `docs/reports/2026-08-06-knowledge-skills-stage-1-2.md` | RED/GREEN、真实 gate 和边界 | 新建实施与验证报告 |

## Task 1：冻结“知识已选中但 Skill 缺失”的 RED 行为

**Files:**
- Modify: `tests/test_skill_registry.py`
- Modify: `tests/test_context_assembler.py`
- Modify: `tests/test_packaged_skills.py`
- Modify: `tests/test_skill_validation.py`
- Create: `docs/reports/2026-08-06-knowledge-skills-stage-1-2.md`

**Interfaces:**
- Consumes: `select_skill_names(capability, task=None) -> tuple[str, ...]`、`assemble_agent_context(...) -> AgentContext`。
- Produces: 四个 solver 的明确 RED 测试，以及不依赖完整 case 字节的公开失败证据。

- [x] **Step 1: 建立阶段 1.2 报告并记录证据边界**

写入：

```markdown
# Knowledge/Skills 阶段 1.2 实施与验证报告

日期：2026-08-06
状态：实施中

## 1. 证据边界

本轮只使用冻结 run、公开 OpenFOAM 日志、正式 Knowledge 和 Foundation v10 公开源码。
四个失败均已选中正确 solver guide，但未加载 family Skill。改进目标是 Skill 路由与知识遵从，
不是 Runner、Gateway、evaluator 或新增逐题知识。
```

- [x] **Step 2: 写 solver 到 family Skill 的失败测试**

在 `tests/test_skill_registry.py` 的参数表加入：

```python
(
    "compressibleInterFoam",
    "openfoam-multiphase-vof",
),
(
    "driftFluxFoam",
    "openfoam-multiphase-coupled",
),
(
    "multiphaseEulerFoam",
    "openfoam-multiphase-coupled",
),
(
    "reactingFoam",
    "openfoam-compressible-transient",
),
```

- [x] **Step 3: 写实际上下文装配的失败测试**

在 `tests/test_context_assembler.py` 增加：

```python
@pytest.mark.parametrize(
    ("solver", "family", "knowledge_id", "skill_name"),
    [
        (
            "compressibleInterFoam",
            "compressible-vof",
            "of10.solver.compressibleinterfoam-contract",
            "openfoam-multiphase-vof",
        ),
        (
            "driftFluxFoam",
            "drift-flux",
            "of10.solver.driftfluxfoam-contract",
            "openfoam-multiphase-coupled",
        ),
        (
            "multiphaseEulerFoam",
            "multiphase-euler",
            "of10.solver.multiphaseeulerfoam-contract",
            "openfoam-multiphase-coupled",
        ),
        (
            "reactingFoam",
            "compressible-reacting",
            "of10.solver.reactingfoam-contract",
            "openfoam-compressible-transient",
        ),
    ],
)
def test_specialized_solver_context_pairs_guide_and_family_skill(
    solver: str,
    family: str,
    knowledge_id: str,
    skill_name: str,
) -> None:
    context = assemble_agent_context(
        _task(),
        _profile(solver, family=family),
    )

    assert context.knowledge_slots["solver_family_contract"] == knowledge_id
    assert context.skill_names == (
        "openfoam-author-native-case",
        skill_name,
    )
    assert len(context.skill_names) == 2
```

- [x] **Step 4: 让 package/validator 测试先声明新 Skill**

在 `tests/test_packaged_skills.py` 的 `expected` 集合和
`tests/test_skill_validation.py::test_repository_family_skills_validate` 参数表加入：

```python
"openfoam-multiphase-coupled"
```

- [x] **Step 5: 运行 RED**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_skill_registry.py \
  tests/test_context_assembler.py::test_specialized_solver_context_pairs_guide_and_family_skill \
  tests/test_packaged_skills.py::test_all_reusable_skills_are_packaged \
  tests/test_skill_validation.py::test_repository_family_skills_validate
```

Expected: 新映射和新 Skill 相关样本失败；既有 solver 样本继续通过。

## Task 2：增加最小 family Skill 路由

**Files:**
- Modify: `src/foampilot/context/skill_registry.py`
- Create: `src/foampilot/skills/openfoam-multiphase-coupled/SKILL.md`
- Create: `src/foampilot/skills/openfoam-multiphase-coupled/agents/openai.yaml`
- Modify: `src/foampilot/skills/scenarios.yaml`

**Interfaces:**
- Consumes: Task 1 的四个 solver mapping 测试；现有 `FAMILY_SKILLS` 和 `read_skills()`。
- Produces: 每个 solver 恰好一个 family Skill；新的 `openfoam-multiphase-coupled` portable Skill。

- [x] **Step 1: 登记四个 solver**

在 `FAMILY_SKILLS` 增加：

```python
"compressibleInterFoam": "openfoam-multiphase-vof",
"driftFluxFoam": "openfoam-multiphase-coupled",
"multiphaseEulerFoam": "openfoam-multiphase-coupled",
"reactingFoam": "openfoam-compressible-transient",
```

不登记 `denseParticleFoam`：当前证据是整体生成规模超时，不是已证明的 family Skill 缺口。

- [x] **Step 2: 创建 coupled multiphase Skill metadata**

`agents/openai.yaml` 使用：

```yaml
interface:
  display_name: "OpenFOAM 多相耦合算例"
  short_description: "编写和修复 Foundation v10 drift-flux 与 Euler-Euler 多相算例"
  default_prompt: "使用 $openfoam-multiphase-coupled 编写或修复多相耦合算例。"
```

- [x] **Step 3: 创建 coupled multiphase Skill**

Skill 必须保持短小，并包含以下 solver 分支：

```markdown
---
name: openfoam-multiphase-coupled
description: Use when authoring or repairing a Foundation OpenFOAM v10 driftFluxFoam or multiphaseEulerFoam case with interacting phases, grouped fields, and interfacial model dictionaries.
---

# 多相耦合算例

## 共同顺序

先把 selected solver guide 中列出的必需表、字段、物性和 base/Final 项转成清单，再生成文件。
空模型表若被 Foundation v10 reader 无条件读取，也必须显式保留为空字典，不能因本题不启用该
模型而删除整个 table。

## driftFluxFoam

一次检查 phaseProperties、每相 physicalProperties、alpha.<phase>、U、p_rgh、g、
momentumTransport、alpha.*Diffusion/base-Final 以及 div(tauDm)。不要等 reader 逐项报错。

## multiphaseEulerFoam

一次检查 phases/referencePhase、每相 U/T/alpha/physicalProperties/momentumTransport、共享
p/p_rgh/g，以及 blending、surfaceTension、interfaceCompression、drag、virtualMass、
heatTransfer、phaseTransfer、lift、wallLubrication、turbulentDispersion 表。

## 修复

保持相名、物性、边界和已通过阶段不变；同一 reader contract 内的成组必需项应一次补齐，
但不捆绑数值调参或模型替换。
```

- [x] **Step 4: 增加 coupled multiphase scenario**

在 `skills/scenarios.yaml` 增加一个 `skill_name: openfoam-multiphase-coupled` 条目，至少包含：

```yaml
triggers:
  - 编写或修复 Foundation v10 的 driftFluxFoam 或 multiphaseEulerFoam 多相耦合算例。
non_triggers:
  - 编写单相不可压缩、VOF 自由表面或纯固体算例。
boundaries:
  - 只使用公开相定义、物性、边界和日志，不得读取目标 tutorial 或 golden。
pressure_prompt: >-
  phaseProperties 已经缺少多个 reader table，但日志目前只报告 phaseTransfer。为了减少改动，
  请只补 phaseTransfer，等待下一次日志再继续。
success_criteria:
  - 按 selected solver guide 一次核对同一 reader contract 的成组必需表。
  - 保持相名、字段、物性和 base/Final 项一致。
  - 不把字典完整性修复与数值调参或模型替换捆绑。
forbidden_actions:
  - 不得逐关键词追赶已知成组 reader contract。
  - 不得读取目标 tutorial、golden 或私有 evaluator。
```

- [x] **Step 5: 验证新 Skill 和映射 GREEN**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_skill_registry.py \
  tests/test_context_assembler.py::test_specialized_solver_context_pairs_guide_and_family_skill \
  tests/test_skill_validation.py::test_repository_family_skills_validate
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate \
  src/foampilot/skills/openfoam-multiphase-coupled --json
```

Expected: 全部通过；每个特殊 solver 只加载通用 Skill 加一个 family Skill。

## Task 3：把 solver guide 变成生成前的原子清单

**Files:**
- Modify: `src/foampilot/skills/openfoam-author-native-case/SKILL.md`
- Modify: `src/foampilot/skills/openfoam-multiphase-vof/SKILL.md`
- Modify: `src/foampilot/skills/openfoam-multiphase-vof/agents/openai.yaml`
- Modify: `src/foampilot/skills/openfoam-compressible-transient/SKILL.md`
- Modify: `src/foampilot/skills/openfoam-compressible-transient/agents/openai.yaml`
- Modify: `src/foampilot/skills/scenarios.yaml`
- Modify: `tests/test_packaged_skills.py`

**Interfaces:**
- Consumes: `AgentContext.knowledge_text` 中唯一的 `solver_family_contract` 和 Task 2 的 Skill 路由。
- Produces: 生成前可执行的 reader checklist，不增加 reviewer 或模型请求。

- [x] **Step 1: 写通用原子清单失败测试**

在 `tests/test_packaged_skills.py` 增加：

```python
def test_native_skill_treats_selected_solver_guide_as_atomic_checklist() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-author-native-case", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "selected solver guide" in text
    assert "原子清单" in text
    assert "不得等 reader 逐项报错" in text
```

- [x] **Step 2: 写两个 family 分支失败测试**

增加：

```python
def test_vof_skill_covers_compressible_interfoam_reader_contract() -> None:
    text = files("foampilot").joinpath(
        "skills", "openfoam-multiphase-vof", "SKILL.md"
    ).read_text(encoding="utf-8")

    for marker in (
        "`compressibleInterFoam`",
        "`pMin`",
        "`alpha.<phase>` solver entry",
        "flow Courant 与 alpha Courant",
    ):
        assert marker in text


def test_compressible_skill_covers_reactingfoam_reader_contract() -> None:
    text = files("foampilot").joinpath(
        "skills", "openfoam-compressible-transient", "SKILL.md"
    ).read_text(encoding="utf-8")

    for marker in (
        "`reactingFoam`",
        "`multiComponentMixture`",
        "`unityLewisFourier`",
        "species",
    ):
        assert marker in text
```

- [x] **Step 3: 运行 RED**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_packaged_skills.py::test_native_skill_treats_selected_solver_guide_as_atomic_checklist \
  tests/test_packaged_skills.py::test_vof_skill_covers_compressible_interfoam_reader_contract \
  tests/test_packaged_skills.py::test_compressible_skill_covers_reactingfoam_reader_contract
```

Expected: 三个测试均因缺少明确行为文字而失败。

- [x] **Step 4: 更新通用 Skill**

在“编写原生文件”最前增加：

```text
将上下文中唯一的 selected solver guide 视为版本化 reader contract。先把其中明确写出的必需文件、
字段、字典表、operator 和 base/Final 配对整理成原子清单，逐项映射到 files/commands 后再输出
CaseBundle。不得只实现日志当前提到的一项，也不得等 reader 逐项报错后再补同一组已知必需项。
```

- [x] **Step 5: 扩展 VOF Skill 的 compressible 分支**

保持已有 `interFoam`/`twoLiquidMixingFoam` 内容不变，增加：

```text
对 compressibleInterFoam，除 VOF 共同项外，一次检查 phaseProperties/pMin、每相
physicalProperties、根与按需每相 momentumTransport、正的 p/T/rho/energy 状态，以及完整的
alpha.<phase> solver entry。时间步同时限制 flow Courant 与 alpha Courant。
```

同步扩展 frontmatter、OpenAI metadata 和 scenario trigger，使其明确覆盖可压缩 VOF，但仍拒绝
drift-flux/Euler-Euler 场景。

- [x] **Step 6: 扩展可压缩 Skill 的 reacting 分支**

增加：

```text
对 reactingFoam，在启动前一次核对精确 thermo mixture runtime 名、species/defaultSpecie、每个
组分数据与初始场、chemistry/combustion/reaction 文件，以及已注册 thermophysical transport 名。
Foundation v10 使用 unityLewisFourier，而不是 unityLewis。先得到可构造的正温 thermo/species
状态，再调整 chemistry 强度或时间步。
```

同步扩展 frontmatter、OpenAI metadata 和 scenario trigger；不得把 reacting 专用文件要求施加到
普通 `rhoCentralFoam`。

- [x] **Step 7: GREEN 和结构验证**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_packaged_skills.py \
  tests/test_skill_validation.py \
  tests/test_context_assembler.py \
  tests/test_skill_registry.py
for skill in \
  openfoam-author-native-case \
  openfoam-multiphase-vof \
  openfoam-multiphase-coupled \
  openfoam-compressible-transient
do
  PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate \
    "src/foampilot/skills/$skill" --json
done
```

Expected: 聚焦测试和四个 Skill validator 全部通过。

## Task 4：验证 Knowledge/Skill 上下文预算与 package

**Files:**
- Modify only if a failing assertion proves necessary: `src/foampilot/context/assembler.py`
- Modify: `docs/design/knowledge-skills-design.md`
- Modify: `docs/reports/2026-08-06-knowledge-skills-stage-1-2.md`

**Interfaces:**
- Consumes: Task 2/3 新增 Skill 文本；当前 `payload_limit_bytes=32 * 1024` 和 package-data glob。
- Produces: 无截断上下文、可安装 wheel 和准确的阶段状态。

- [x] **Step 1: 运行 Knowledge、Skill、泄漏和上下文全套确定性测试**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py \
  tests/test_context_assembler.py \
  tests/test_skill_registry.py \
  tests/test_packaged_skills.py \
  tests/test_skill_validation.py \
  tests/test_knowledge_models.py \
  tests/test_task_spec.py \
  tests/test_agent_context.py \
  tests/test_stage2_cli.py
```

Expected: 全部通过；若新 Skill 超过 32 KiB 预算，应缩短 Skill，不提高全局预算。

- [x] **Step 2: 构建 wheel 并检查新 Skill**

Run:

```bash
rm -rf /tmp/foampilot-stage-1-2-wheel
/home/edwin/feal-venv-py312/bin/python -B -m pip wheel \
  --no-deps --wheel-dir /tmp/foampilot-stage-1-2-wheel .
/home/edwin/feal-venv-py312/bin/python -B -c \
  'from pathlib import Path; import zipfile; wheel=next(Path("/tmp/foampilot-stage-1-2-wheel").glob("*.whl")); names=set(zipfile.ZipFile(wheel).namelist()); required={"foampilot/skills/openfoam-multiphase-coupled/SKILL.md","foampilot/skills/openfoam-multiphase-coupled/agents/openai.yaml"}; missing=sorted(required-names); assert not missing, missing; print(wheel)'
```

Expected: wheel 构建成功且包含新 Skill 两个文件。

- [x] **Step 3: 更新设计状态和阶段报告**

把 `docs/design/knowledge-skills-design.md` 顶部状态改为：阶段 1.1 已完成；阶段 1.2 按本计划实施。
在阶段报告记录：修改文件、确定性测试、wheel 路径/hash、上下文字节和明确未改变的架构边界。

## Task 5：四个历史失败族的真实 forward gate

**Files:**
- Modify: `docs/reports/2026-08-06-knowledge-skills-stage-1-2.md`
- Runtime artifacts only: `/tmp/foampilot-knowledge-skills-1-2-forward-20260806-v1`

**Interfaces:**
- Consumes: 当前四个公开 TaskSpec、固定 `codex-cli`/`gpt-5.6-sol`、Foundation v10。
- Produces: 每题全新 cold authoring 的 generation、solver entry、normal completion、public validation 和 repair 证据。

- [x] **Step 1: 运行 preflight 和 model doctor**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot preflight --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot model doctor \
  --backend codex-cli --model-name gpt-5.6-sol --json
```

Expected: Foundation v10 可用；模型 backend 可执行。bubblewrap 不可用时使用有记录的
audited-host fallback，不等待交互权限。

- [x] **Step 2: 串行运行四个 cold forward gate**

依次运行：

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve \
  src/foampilot/qualification/data/tasks/driftflux-dahl.yaml \
  --run-root /tmp/foampilot-knowledge-skills-1-2-forward-20260806-v1 \
  --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
```

其余三个 task 路径依次替换为：

```text
multiphase-euler-bubble-column.yaml
reacting-counterflow-flame.yaml
compressible-interfoam-climbing-rod.yaml
```

Expected: 四题均生成有效 case 并启动目标 solver；正常结束和 public validation 单独记录，不把
solver entry 冒充求解正确。

- [x] **Step 3: 核对真实上下文**

逐题读取 `agent-context.json`，必须证明：

```text
正确 solver guide 已选择
+ 预期 family Skill 已加载
+ family Skill 总数不超过 1
+ protected path 未出现
```

- [x] **Step 4: 分类 forward 结果**

对每题记录：

```text
case generation
checkMesh
target solver started
solver normal completion
public validation
first failure code/log signal
repair 是否一次覆盖同组 reader contract
provider/environment blocker
```

如果仍出现同一四个精确 reader error，则阶段 1.2 不通过；先回到 Task 3 修正 Skill，不扩大到新架构。

## Task 5A：根据 v1 forward 证据精修原子 reader contract

**Files:**
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/driftfluxfoam-contract.yaml`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/multiphaseeulerfoam-contract.yaml`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/reactingfoam-contract.yaml`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/compressibleinterfoam-contract.yaml`
- Modify: `src/foampilot/skills/openfoam-multiphase-coupled/SKILL.md`
- Modify: `src/foampilot/skills/openfoam-multiphase-vof/SKILL.md`
- Modify: `src/foampilot/skills/openfoam-compressible-transient/SKILL.md`
- Modify: `tests/test_knowledge_models.py`
- Modify: `tests/test_packaged_skills.py`
- Runtime artifacts only: `/tmp/foampilot-knowledge-skills-1-2-forward-20260806-v2`

**Interfaces:**
- Consumes: v1 四题公开日志、Foundation v10 reader/source 和公开 family dictionaries。
- Produces: 四个可复用 solver-family 原子契约；不增加 TaskSpec、逐题规则或模型调用。

- [x] **Step 1: 根因追溯并建立 RED**

冻结四个 reader 事实：`Vc`/`nLimiterIter`、逐相 `div(phi,alpha.<phase>)`、reaction include、
alpha base/Final 与黏性应力 operator。新增 7 个聚焦断言，确认修改前结果为 `7 failed`。

- [x] **Step 2: 最小更新 Knowledge 与 family Skills**

只补读者无条件读取的字段、维度、include 关系和精确 operator 命名；不复制目标 case 的几何、
边界值、数值系数或完整字典。

- [x] **Step 3: 确定性 GREEN 与 Skill validator**

聚焦 RED 变为 `7 passed`；Knowledge/Context/Skills 相关测试为 `113 passed`；四个相关 Skill
validator 均为 `PASS`。

- [x] **Step 4: 用相同四题运行 v2 cold forward gate**

继续串行、每题全新生成，不手工修 case。比较 v1/v2 的 solver entry、首个 reader error、repair
是否推进和正常结束，只有 v2 改善后才进入 Task 6。

## Task 5B：最后一轮 Knowledge-only 精修与 v3 gate

**Files:** 与 Task 5A 相同；runtime root 为
`/tmp/foampilot-knowledge-skills-1-2-forward-20260806-v3`。

**Interfaces:**
- Consumes: v2 四题冻结日志与 Foundation v10 对应 reader/source。
- Produces: first-phase mixture model、Euler-Euler grouped viscous operator、`Yi/ YiFinal`、
  `phaseProperties.sigma` 四项原子契约。

- [x] **Step 1: 建立第二组 7 个 RED**

修改前聚焦组为 `7 failed`，分别对应四个 Knowledge guide 与三个 family Skill 的缺失契约。

- [x] **Step 2: 最小更新并通过确定性验证**

修改后聚焦组为 `7 passed`；Knowledge/Context/Skills 组为 `113 passed`；四个 Skill validator
均为 `PASS`。

- [x] **Step 3: 串行运行相同四题 v3 cold forward gate**

v3 后停止 Knowledge-only 逐关键词循环：若仍只是 reader 链逐项推进，将失败样本冻结给架构
P0/P1，而不是进行第四轮提示词/知识堆叠。

## Task 6：受影响题族回归和 50 题阶段 gate

> 顺序调整（2026-08-06）：按最新确认的总体顺序，先完成本阶段定向 gate，再进入 Agent
> Harness v2 P0/P1；既有 30+20 大回归移到 P0/P1 后执行，以免先为已确认的状态/repair 缺口
> 继续支付 50 题 cold 成本。本 Task 保留为后续回归定义，当前未执行，也不标记为通过。

**Files:**
- Modify: `docs/reports/2026-08-06-knowledge-skills-stage-1-2.md`
- Runtime artifacts only under explicit `/tmp/foampilot-knowledge-skills-1-2-*` roots

**Interfaces:**
- Consumes: Task 5 通过的 Knowledge/Skill package；既有 30+20 suites。
- Produces: 与阶段 1.1 基线分层比较的回归证据，为架构 P0/P1 提供冻结输入。

- [ ] **Step 1: 先运行受影响回归题**

串行复测以下既有 TaskSpec，不新增 suite/task YAML：

```text
multiphase-dam-break
compressible-shock-tube
rhopimple-shock-tube
rhocentral-oblique-shock
rhocentral-forward-step
buoyant-hot-room-boussinesq
simple-t3a-boundary-layer
rhocentral-wedge-ma5
```

Run root：

```text
/tmp/foampilot-knowledge-skills-1-2-affected-20260806-v1
```

Expected: 记录完整分层结果；目标 solver entry 不低于对应历史基线，不以单次随机改善宣称 family
能力完成。

- [ ] **Step 2: 冻结配置运行既有 30 题**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot qualify suite \
  --suite-file src/foampilot/qualification/data/suites/official-corpus-30-baseline-v1.yaml \
  --run-root /tmp/foampilot-knowledge-skills-1-2-official-30-20260806-v1 \
  --workers 1 --backend codex-cli --model-name gpt-5.6-sol --json
```

- [ ] **Step 3: 冻结配置运行既有额外 20 题**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot qualify suite \
  --suite-file src/foampilot/qualification/data/suites/official-corpus-extra-20-v1.yaml \
  --run-root /tmp/foampilot-knowledge-skills-1-2-extra-20-20260806-v1 \
  --workers 1 --backend codex-cli --model-name gpt-5.6-sol --json
```

- [ ] **Step 4: 验证 artifact 并形成分层比较**

报告必须与阶段 1.1 分别比较：

```text
generation success
target solver started
solver normal completion
public validation
strict qualification（有 evaluator 的题）
logical model requests / transport attempts
time to first OpenFOAM command
environment/backend blocker
```

50 题阶段 gate：

- generation 不低于阶段 1.1 的有效基线 `49/50`；
- target solver started 不低于 `47/50`；
- normal completion 和 public validation 不得出现无法解释的净退化；
- 四个原始 reader error 不再出现；
- environment/bubblewrap blocker 保持 `0`；
- 任何组合后的“有效视图”必须标注来源，不冒充单次 suite。

## Task 7：阶段收尾并决定是否进入架构 P0/P1

**Files:**
- Modify: `docs/reports/2026-08-06-knowledge-skills-stage-1-2.md`
- Modify: `docs/design/knowledge-skills-design.md`
- No code changes unless earlier verification exposes a defect

**Interfaces:**
- Consumes: Task 1—5B 的测试、wheel、run 和 artifact；Task 6 按顺序调整延期。
- Produces: 阶段 1.2 最终结论及架构 P0/P1 的真实 failure fixtures。

- [x] **Step 1: 运行全仓确定性验证**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
git diff --check
```

Expected: 全仓测试通过；没有 whitespace error。

- [x] **Step 2: 完成报告结论**

明确区分：

- Skill 路由和 reader-contract 遵从是否改善；
- case 是否进入 solver；
- solver 是否正常结束；
- public/physics qualification 是否通过；
- 失败是知识缺失、知识未遵从、数值问题、provider 问题还是 evaluator 问题；
- 本轮没有实现 Agent Harness v2 P0/P1 或 IDE。

- [x] **Step 3: 冻结下一阶段输入**

只有阶段 1.2 报告和 artifacts 完成后，才为 Agent Harness v2 的 P0/P1 创建独立实施计划。计划只
选择本轮证明确实需要的 failure classifier、repair scope、command patch 和状态字段，不把 P2/P3
或 IDE 提前纳入。

## 完成定义

阶段 1.2 只有同时满足以下条件才完成：

1. 四个 specialized solver 均选择正确 guide 和恰好一个 family Skill；
2. 新 Skill 通过结构、场景、package 和泄漏测试；
3. 正常 authoring 没有新增模型调用；
4. 四个历史 reader error 在全新 cold forward gate 中不再重复；
5. v3 定向 gate 冻结分层结果；50 题 gate 明确延期到 P0/P1 后，不能冒充已经通过；
6. 所有运行产物可由 artifact manifest 验证；
7. 报告没有把 solver 启动、正常结束、public validation 和 qualification 混为一类；
8. 没有逐题内容、目标 tutorial/golden 泄漏或自动 Knowledge promotion；
9. 全仓确定性测试通过；
10. 未经用户授权没有 commit 或 push。
