# Knowledge/Skills 阶段 1.1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本计划只允许当前会话内联执行，不使用子代理。

**Goal:** 基于冻结的 30 题证据，修正专用知识误激活并加强 Foundation OpenFOAM v10 多相、可压缩、浮力和 Maxwell/PIMPLE 契约，优先提高目标求解器进入率与正常完成率。

**Architecture:** 保持 `TaskSpec → CapabilityProfile → slot-based context → ExecutionPlan v3 → Runner → validation → bounded repair` 主链不变。只修改现有 Knowledge YAML、family Skill 和对应测试；专用条目用 `activation_terms` opt-in，Skill 使用正向编写/修复顺序，不增加逐题内容、renderer、状态机或 blocking inspector。

**Tech Stack:** Python 3.12、Pydantic v2、PyYAML、pytest、Foundation OpenFOAM v10、FoamPilot CLI、Markdown/YAML package data。

## Global Constraints

- 当前验证目标固定为 Foundation OpenFOAM v10。
- 不读取或复制目标 tutorial；官方 example 只能在 attempt 冻结后由 teacher 侧提炼通用语义。
- 不写入目标几何参数、patch 名、golden value、evaluator tolerance 或官方目标路径。
- qualification 的 public checks、私有 evaluator 和 tolerance 不得修改。
- 每次最多加载一个通用 Skill、一个 family Skill，以及任务确有 geometry/mesh 时的 mesh Skill。
- Knowledge 保存事实与适用条件；Skill 保存判断、编写、自检和 repair 顺序。
- 先使用冻结失败 attempt 作为 RED 行为证据，再修改一个 Skill；验证完成前不得继续修改下一个 Skill。
- 真实 gate 固定 `codex-cli` / `gpt-5.6-sol`，串行执行；求解器内部可使用 TaskSpec 允许的 MPI ranks。
- backend/environment failure 与 case/solver/qualification failure 分开统计。
- 保留当前脏工作区中的 Performance v1 变更；不得重置、清理或覆盖无关文件。
- 未经用户单独授权，不执行 `git commit` 或 `git push`。

---

## 文件职责与修改地图

| 文件 | 职责 | 本轮变化 |
| --- | --- | --- |
| `src/foampilot/knowledge/openfoam10/physics-models/volume-fraction-source.yaml` | 专用固定体积分数模型事实 | 增加显式 activation terms，避免普通 pimple/rhoPimple 任务误召回 |
| `src/foampilot/knowledge/openfoam10/solver-guides/interfoam-vof-contract.yaml` | `interFoam` 原生文件与字段契约 | 明确 alpha solver entry 的完整结构 |
| `src/foampilot/knowledge/openfoam10/solver-guides/twoliquidmixingfoam-contract.yaml` | `twoLiquidMixingFoam` 启动契约 | 明确 `Dab`、`alphatab` 及 alpha controls |
| `src/foampilot/knowledge/openfoam10/solver-guides/rhocentralfoam-contract.yaml` | `rhoCentralFoam` 守恒/派生字段契约 | 修正 diagonal 与 symmetric matrix solver 的适用范围 |
| `src/foampilot/knowledge/openfoam10/solver-guides/rhopimplefoam-compressible-laminar-contract.yaml` | `rhoPimpleFoam` PIMPLE/thermo 契约 | 增加矩阵类型与 preconditioner 兼容规则 |
| `src/foampilot/knowledge/openfoam10/error-playbooks/thermo-state-instability.yaml` | thermo 失败 repair 顺序 | 明确 thermo inversion 失败不得只改 `fvSolution` |
| `src/foampilot/knowledge/openfoam10/solver-guides/pimplefoam-maxwell-contract.yaml` | Maxwell/PIMPLE 专用契约 | 强化 actual Courant、stress residual 与单原因 repair |
| `src/foampilot/skills/openfoam-multiphase-vof/SKILL.md` | 多相编写与 repair 顺序 | 增加 solver 启动前的 phase dictionary/alpha entry 正向清单 |
| `src/foampilot/skills/openfoam-compressible-transient/SKILL.md` | 可压缩编写与 repair 顺序 | 增加矩阵兼容和 thermo-positive startup 顺序 |
| `src/foampilot/skills/openfoam-buoyant-cht/SKILL.md` | 浮力/CHT 编写与 repair 顺序 | 增加 thermo inversion 的根因优先级 |
| `src/foampilot/skills/openfoam-incompressible-pressure-velocity/SKILL.md` | 不可压缩 PIMPLE/Maxwell 行为 | 增加 Maxwell 失稳时的 evidence-driven repair 顺序 |
| `src/foampilot/skills/scenarios.yaml` | family Skill 压力场景 | 为上述四个 Skill 增加可观察成功条件，不新增 Skill |
| `src/foampilot/knowledge/knowledge-manifest.json` | 冻结 Knowledge package bytes | 每个 Knowledge 小步后重建 |
| `tests/test_context_assembler.py` | slot/activation 集成行为 | 增加专用知识拒绝与显式激活测试 |
| `tests/test_knowledge_retrieval.py` | Knowledge 内容与来源契约 | 增加 Foundation v10 关键字和 solver pairing 断言 |
| `tests/test_packaged_skills.py` | 打包后的 Skill 行为契约 | 增加四个 Skill 的正向顺序断言 |
| `docs/reports/2026-08-05-knowledge-skills-stage-1-1.md` | RED/GREEN、真实 gate 与边界证据 | 新建实施记录，不复制完整 case 或私有 evaluator 数据 |

## Task 1：修正专用 Knowledge 的误激活

**Files:**
- Modify: `tests/test_knowledge_retrieval.py`
- Modify: `src/foampilot/knowledge/openfoam10/physics-models/volume-fraction-source.yaml`
- Modify: `src/foampilot/knowledge/knowledge-manifest.json`

**Interfaces:**
- Consumes: `load_task_spec(path)`, `_context_for_task(task)`, `KnowledgeEntry.activation_terms`。
- Produces: `volumeFractionSource` 只在公开任务文本含有显式模型证据时进入 `physics_transport_model` slot。

- [ ] **Step 1: 写入失败的误激活回归测试**

在 `tests/test_knowledge_retrieval.py` 增加：

```python
@pytest.mark.parametrize(
    "case_id",
    ["laminar-planar-couette", "rhopimple-shock-tube"],
)
def test_volume_fraction_source_requires_explicit_task_activation(
    case_id: str,
) -> None:
    task = load_task_spec(
        PROJECT
        / "src/foampilot/qualification/data/tasks"
        / f"{case_id}.yaml"
    )

    context = _context_for_task(task)

    assert (
        "of10.physics.volume-fraction-source"
        not in context.selected_knowledge_ids
    )
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py::test_volume_fraction_source_requires_explicit_task_activation
```

Expected: 两个参数样本至少一个失败，显示该专用条目仍被 executable overlap 激活。

- [ ] **Step 3: 最小化 activation 条件**

在 `volume-fraction-source.yaml` 的 `tags` 后增加：

```yaml
activation_terms:
  - volume fraction source
  - volumeFractionSource
  - solidEquilibriumEnergySource
```

不要加入泛化过宽的 `source`、`alpha`、`blockage` 或 solver name。

- [ ] **Step 4: 验证拒绝与正向激活同时成立**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py::test_volume_fraction_source_requires_explicit_task_activation \
  tests/test_knowledge_retrieval.py::test_blocked_channel_retrieves_volume_fraction_source_contract
```

Expected: `2 passed` 以上；普通 Maxwell/激波管拒绝，显式 blocked-channel 模型继续选中。

- [ ] **Step 5: 重建 manifest 并验证 corpus**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c \
  'from pathlib import Path; import json; from foampilot.knowledge import build_knowledge_manifest; p=Path("src/foampilot/knowledge/knowledge-manifest.json"); p.write_text(json.dumps(build_knowledge_manifest("src/foampilot/knowledge/openfoam10"), indent=2, sort_keys=True)+"\n", encoding="utf-8")'
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_knowledge_retrieval.py
```

Expected: Knowledge corpus 和 manifest 全部通过。

## Task 2：强化多相 Knowledge 与 `openfoam-multiphase-vof` Skill

**Files:**
- Modify: `tests/test_knowledge_retrieval.py`
- Modify: `tests/test_packaged_skills.py`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/interfoam-vof-contract.yaml`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/twoliquidmixingfoam-contract.yaml`
- Modify: `src/foampilot/skills/openfoam-multiphase-vof/SKILL.md`
- Modify: `src/foampilot/skills/scenarios.yaml`
- Modify: `src/foampilot/knowledge/knowledge-manifest.json`
- Create: `docs/reports/2026-08-05-knowledge-skills-stage-1-1.md`

**Interfaces:**
- Consumes: 冻结 run 中 `alpha.<phase>` 缺 `solver`、`phaseProperties` 缺 `Dab`/`alphatab` 的 RED 证据。
- Produces: 可检索的两液体启动事实，以及按读取顺序编写和 repair 的多相 Skill。

- [ ] **Step 1: 记录 RED 行为证据**

创建报告，记录以下公开日志事实，不复制 case 内容：

```markdown
## 多相 RED

- `interFoam`: `fvSolution/solvers/alpha.<phase>` 只有 alpha controls 时，原生日志报告 `keyword solver is undefined`。
- `twoLiquidMixingFoam`: `phaseProperties` 先后报告 `Dab` 与 `alphatab` 未定义，说明 repair 在逐关键词追赶 reader。
- 改进目标：Knowledge + `openfoam-multiphase-vof`，不是 Runner、backend 或 evaluator。
```

- [ ] **Step 2: 写入失败的 Knowledge 与 Skill 契约测试**

扩展 `test_extended_solver_contracts_cover_observed_startup_failures()`：

```python
"of10.solver.interfoam-vof-contract": (
    "alpha.<phase>",
    "solver、smoother、tolerance、relTol",
    "nAlphaCorr",
    "nAlphaSubCycles",
),
"of10.solver.twoliquidmixingfoam-contract": (
    "Dab",
    "alphatab",
    "nAlphaSubCycles",
),
```

并在 `tests/test_packaged_skills.py` 增加：

```python
def test_multiphase_skill_checks_reader_contract_before_solver() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-multiphase-vof", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "按求解器读取顺序" in text
    assert "`Dab`、`alphatab`" in text
    assert "`solver`、`smoother`、`tolerance` 与 `relTol`" in text
```

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py::test_extended_solver_contracts_cover_observed_startup_failures \
  tests/test_packaged_skills.py::test_multiphase_skill_checks_reader_contract_before_solver
```

Expected: 缺少新契约片段而失败。

- [ ] **Step 4: 最小修改两个 Knowledge 条目**

`interfoam-vof-contract.yaml` 明确：

```text
alpha.<phase> solver entry 同时包含 solver、smoother、tolerance、relTol 与
nAlphaCorr/nAlphaSubCycles；只有 alpha controls 的 regex dictionary 不是完整 linear-solver entry。
```

`twoliquidmixingfoam-contract.yaml` 明确：

```text
phaseProperties 顶层同时提供有量纲分子扩散系数 Dab 与无量纲 alphatab；二者在字段创建时直接读取，
不得等日志逐项报错后再补齐。
```

来源继续指向相应 Foundation v10 application source；不加入题目相名或数值。

- [ ] **Step 5: 最小修改多相 Skill 与场景**

在“必需契约”加入正向步骤：

```text
按求解器读取顺序先完成 phaseProperties、每相 physicalProperties、初始 alpha field、
alpha solver entry，再生成 setFields 和 solver command。twoLiquidMixingFoam 的 phaseProperties
同时核对 Dab、alphatab；alpha solver entry 同时核对 solver、smoother、tolerance、relTol 与 alpha controls。
```

把 `scenarios.yaml` 中该 Skill 的 `success_criteria` 增加两个可观察条件：完整 phase properties；
完整 alpha solver entry。不要增加 per-case 名称。

- [ ] **Step 6: GREEN、Skill 结构和 manifest 验证**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c \
  'from pathlib import Path; import json; from foampilot.knowledge import build_knowledge_manifest; p=Path("src/foampilot/knowledge/knowledge-manifest.json"); p.write_text(json.dumps(build_knowledge_manifest("src/foampilot/knowledge/openfoam10"), indent=2, sort_keys=True)+"\n", encoding="utf-8")'
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py tests/test_packaged_skills.py tests/test_skill_validation.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate \
  src/foampilot/skills/openfoam-multiphase-vof --json
```

Expected: 测试通过，Skill validator 返回 `PASS`。

- [ ] **Step 7: 运行多相 forward gate 后才进入下一 Skill**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve \
  src/foampilot/qualification/data/tasks/multiphase-dam-break.yaml \
  --run-root /tmp/foampilot-knowledge-skills-1-1/multiphase-dam-break \
  --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve \
  src/foampilot/qualification/data/tasks/two-liquid-lock-exchange.yaml \
  --run-root /tmp/foampilot-knowledge-skills-1-1/two-liquid-lock-exchange \
  --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve \
  src/foampilot/qualification/data/tasks/multiphase-capillary-rise.yaml \
  --run-root /tmp/foampilot-knowledge-skills-1-1/multiphase-capillary-rise-holdout \
  --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
```

Expected evidence: 三个 run 都验证 artifact manifest；分别记录 target solver entry、normal
completion、public validation 和模型时间。若 target 仍失败，失败必须越过本轮已修复的缺失关键词，
否则本 Skill 不得进入下一修改。

## Task 3：强化可压缩 Knowledge 与 `openfoam-compressible-transient` Skill

**Files:**
- Modify: `tests/test_knowledge_retrieval.py`
- Modify: `tests/test_packaged_skills.py`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/rhocentralfoam-contract.yaml`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/rhopimplefoam-compressible-laminar-contract.yaml`
- Modify: `src/foampilot/skills/openfoam-compressible-transient/SKILL.md`
- Modify: `src/foampilot/skills/scenarios.yaml`
- Modify: `src/foampilot/knowledge/knowledge-manifest.json`
- Modify: `docs/reports/2026-08-05-knowledge-skills-stage-1-1.md`

**Interfaces:**
- Consumes: Foundation v10 matrix runtime lists，以及冻结 run 的 `Unknown symmetric matrix solver diagonal`、`Unknown symmetric matrix preconditioner DILU` 和 negative-temperature 证据。
- Produces: 守恒显式场与派生隐式场分离、矩阵类型兼容、thermo-positive startup 的可压缩契约。

- [ ] **Step 1: 在报告记录可压缩 RED**

记录：

```markdown
## 可压缩 RED

- `rhoCentralFoam`: conserved `(rho|rhoU|rhoE)` 可使用 diagonal；派生 `(U|e)` 被配置为 diagonal 时，Foundation v10 将其按 symmetric matrix 拒绝。
- `rhoPimpleFoam`: symmetric pressure matrix 与 DILU 组合被 runtime 拒绝。
- 两个 `rhoCentralFoam` repair 修正字典后进入推进，但随后出现 negative initial temperature，说明字典兼容和 thermo-positive startup 是两个独立 gate。
```

- [ ] **Step 2: 写入失败的 Knowledge/Skill 测试**

在 `test_solver_contracts_cover_failures_observed_in_native_baseline()` 对 `rhoCentralFoam` 增加：

```python
assert "守恒显式场" in rho_central
assert "派生隐式场" in rho_central
assert "smoothSolver" in rho_central
assert "diagonal 只用于" in rho_central
```

在 `test_extended_solver_contracts_cover_observed_startup_failures()` 的 rhoPimple entry 增加
`"symmetric matrix"`、`"DIC"` 与 `"DILU"`。在 `tests/test_packaged_skills.py` 增加：

```python
def test_compressible_skill_checks_matrix_and_thermo_before_tuning() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-compressible-transient", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "矩阵类型" in text
    assert "守恒显式场" in text
    assert "派生隐式场" in text
    assert "先验证初始 thermo state" in text
```

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py::test_solver_contracts_cover_failures_observed_in_native_baseline \
  tests/test_knowledge_retrieval.py::test_extended_solver_contracts_cover_observed_startup_failures \
  tests/test_packaged_skills.py::test_compressible_skill_checks_matrix_and_thermo_before_tuning
```

Expected: missing contract fragments，不能是 YAML/schema error。

- [ ] **Step 4: 修正两个 solver contract**

`rhocentralfoam-contract.yaml` 将错误的“两组均 diagonal”改为：

```text
conserved `(rho|rhoU|rhoE)` 是显式更新，可使用 diagonal；由边界或派生更新请求的 `(U|e)`
必须使用 Foundation v10 对当前 symmetric matrix 有效的 solver，例如 smoothSolver，而不能沿用 diagonal。
```

`rhopimplefoam-compressible-laminar-contract.yaml` 增加：

```text
线性 solver/preconditioner 必须与 runtime 判定的 matrix type 相容；symmetric pressure matrix
使用 DIC/FDIC/GAMG/diagonal/none 等有效 preconditioner，DILU 只用于其注册的 asymmetric 路径。
看到 runtime valid-list 错误时只修该 field entry，不改 thermo 或物理边界。
```

来源 locator 扩展为有序 source set：

```text
rhoCentralFoam.C
src/OpenFOAM/matrices/lduMatrix/lduMatrix/lduMatrixSolver.C

rhoPimpleFoam.C
src/OpenFOAM/matrices/lduMatrix/lduMatrix/lduMatrixPreconditioner.C
```

每个条目的 `source.sha256` 使用所列文件按顺序直接拼接字节后的 SHA256；用下面的确定性命令分别
计算并把输出写入对应 YAML：

```bash
/home/edwin/feal-venv-py312/bin/python -B -c 'from hashlib import sha256; from pathlib import Path; root=Path("/home/edwin/workplace/OpenFOAM-10"); paths=[root/"applications/solvers/compressible/rhoCentralFoam/rhoCentralFoam.C",root/"src/OpenFOAM/matrices/lduMatrix/lduMatrix/lduMatrixSolver.C"]; h=sha256(); [h.update(p.read_bytes()) for p in paths]; print(h.hexdigest())'
/home/edwin/feal-venv-py312/bin/python -B -c 'from hashlib import sha256; from pathlib import Path; root=Path("/home/edwin/workplace/OpenFOAM-10"); paths=[root/"applications/solvers/compressible/rhoPimpleFoam/rhoPimpleFoam.C",root/"src/OpenFOAM/matrices/lduMatrix/lduMatrix/lduMatrixPreconditioner.C"]; h=sha256(); [h.update(p.read_bytes()) for p in paths]; print(h.hexdigest())'
```

- [ ] **Step 5: 修改一个可压缩 Skill**

在必需契约中增加正向 recipe：先区分守恒显式场与派生隐式场，再为每个 field 选择与 matrix type
相容的 solver/preconditioner；启动前用公开状态关系验证 p/T/rho/energy，字典 compatibility 通过后
才调整 Courant、格式或松弛。场景成功标准增加：拒绝把 diagonal/DILU 复制到所有 field；negative
temperature repair 先检查 thermo state，不能裁剪字段。

- [ ] **Step 6: GREEN、manifest 和 Skill validator**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c \
  'from pathlib import Path; import json; from foampilot.knowledge import build_knowledge_manifest; p=Path("src/foampilot/knowledge/knowledge-manifest.json"); p.write_text(json.dumps(build_knowledge_manifest("src/foampilot/knowledge/openfoam10"), indent=2, sort_keys=True)+"\n", encoding="utf-8")'
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py tests/test_packaged_skills.py tests/test_skill_validation.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate \
  src/foampilot/skills/openfoam-compressible-transient --json
```

Expected: 全部通过。

- [ ] **Step 7: 运行可压缩 target 与 holdout gate**

Run serially:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/rhopimple-shock-tube.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/rhopimple-shock-tube --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/rhocentral-oblique-shock.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/rhocentral-oblique-shock --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/rhocentral-forward-step.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/rhocentral-forward-step --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/compressible-shock-tube.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/compressible-shock-tube-holdout --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/compressible-blocked-channel.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/compressible-blocked-channel-holdout --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
```

每题使用独立 `/tmp/foampilot-knowledge-skills-1-1/<case-id>` run root、固定 backend/model，并验证
artifact manifest。通过条件不是强制五题 physics pass，而是：holdout solver entry 不回退；target
不再因 diagonal/DILU compatibility 失败；剩余 thermo 或数值失败单独归因。

## Task 4：强化浮力 thermo repair 顺序

**Files:**
- Modify: `tests/test_knowledge_retrieval.py`
- Modify: `tests/test_packaged_skills.py`
- Modify: `src/foampilot/knowledge/openfoam10/error-playbooks/thermo-state-instability.yaml`
- Modify: `src/foampilot/skills/openfoam-buoyant-cht/SKILL.md`
- Modify: `src/foampilot/skills/scenarios.yaml`
- Modify: `src/foampilot/knowledge/knowledge-manifest.json`
- Modify: `docs/reports/2026-08-05-knowledge-skills-stage-1-1.md`

**Interfaces:**
- Consumes: 冻结 buoyant run 中两次 `Maximum number of iterations exceeded`，repair 只修改 `fvSolution` 后错误复现的 RED。
- Produces: thermo inversion failure 的根因优先 repair 行为，不增加 Boussinesq 特定数值模板。

- [ ] **Step 1: 写 RED 与失败测试**

在报告记录“只修改 relaxation/linear solver 不能证明修复 thermo inversion”。在 Knowledge 测试断言：

```python
def test_thermo_playbook_requires_state_first_repair() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(CORPUS)
    }
    playbook = entries[
        "of10.error.thermo-state-instability"
    ].model_dump_json()

    assert "不得只修改 fvSolution" in playbook
    assert "temperature extrema" in playbook
```

Skill 测试为：

```python
def test_buoyant_skill_checks_thermo_inversion_before_linear_tuning() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-buoyant-cht", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "thermo inversion" in text
    assert "先验证参考状态" in text
    assert "不得只修改 `fvSolution`" in text
```

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py::test_thermo_playbook_requires_state_first_repair \
  tests/test_packaged_skills.py::test_buoyant_skill_checks_thermo_inversion_before_linear_tuning
```

Expected: 因缺少新 repair 顺序而失败。

- [ ] **Step 2: 最小修改 playbook 和一个 Skill**

playbook 增加：

```text
若日志来自 energy-to-temperature inversion，应先保存首个失败前的 temperature extrema、energy、
p/rho 与 thermo package；不得只修改 fvSolution。只有初始/边界状态可反演后，才调整松弛、时间步和格式。
```

`openfoam-buoyant-cht` 增加相同的正向 repair 顺序；不加入题目温度、热源位置或专用 relaxation 值。

- [ ] **Step 3: GREEN、Skill validator 和真实 gate**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c 'from pathlib import Path; import json; from foampilot.knowledge import build_knowledge_manifest; p=Path("src/foampilot/knowledge/knowledge-manifest.json"); p.write_text(json.dumps(build_knowledge_manifest("src/foampilot/knowledge/openfoam10"), indent=2, sort_keys=True)+"\n", encoding="utf-8")'
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_knowledge_retrieval.py tests/test_packaged_skills.py tests/test_skill_validation.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate src/foampilot/skills/openfoam-buoyant-cht --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/buoyant-hot-room-boussinesq.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/buoyant-hot-room-boussinesq --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/buoyant-benard-cells.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/buoyant-benard-cells-holdout --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/buoyant-cavity.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/buoyant-cavity-holdout --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
```

验证 manifest；要求两个 holdout 的 solver entry 不回退。target 若仍失败，报告 thermo state、首个
失败时间、实际 residual/continuity 与 repair changed-files，禁止为通过而调整 evaluator。

## Task 5：强化 Maxwell/PIMPLE 的 evidence-driven repair

**Files:**
- Modify: `tests/test_knowledge_retrieval.py`
- Modify: `tests/test_packaged_skills.py`
- Modify: `src/foampilot/knowledge/openfoam10/solver-guides/pimplefoam-maxwell-contract.yaml`
- Modify: `src/foampilot/skills/openfoam-incompressible-pressure-velocity/SKILL.md`
- Modify: `src/foampilot/skills/scenarios.yaml`
- Modify: `src/foampilot/knowledge/knowledge-manifest.json`
- Modify: `docs/reports/2026-08-05-knowledge-skills-stage-1-1.md`

**Interfaces:**
- Consumes: 冻结 Couette run 中 actual Courant 从小值增长到大于 2、stress residual 先恶化后 FPE，repair 只改 `fvSchemes` 后复现的 RED。
- Produces: 不违反 TaskSpec 的单原因稳定性 repair 顺序。

- [ ] **Step 1: 写 RED 与失败测试**

Knowledge 内容测试增加：

```python
def test_maxwell_contract_requires_evidence_driven_repair() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(CORPUS)
    }
    maxwell = entries[
        "of10.solver.pimplefoam-maxwell-contract"
    ].model_dump_json()

    assert "actual Courant" in maxwell
    assert "stress residual" in maxwell
    assert "一次 repair 只改变一个原因族" in maxwell
```

Skill 内容测试为：

```python
def test_incompressible_skill_bounds_maxwell_repair() -> None:
    root = files("foampilot").joinpath("skills")
    text = root.joinpath(
        "openfoam-incompressible-pressure-velocity", "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Maxwell" in text
    assert "actual Courant" in text
    assert "stress residual" in text
    assert "不得违反 TaskSpec" in text
```

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_knowledge_retrieval.py::test_maxwell_contract_requires_evidence_driven_repair \
  tests/test_packaged_skills.py::test_incompressible_skill_bounds_maxwell_repair
```

Expected: 因缺少新 evidence-driven repair 顺序而失败。

- [ ] **Step 2: 最小修改一个 Knowledge 和一个 Skill**

正向顺序固定为：

```text
确认 Maxwell 必需 operators/outer coupling
→ 读取 actual Courant 与 sigma residual history
→ 定位首次恶化时间
→ 只改时间控制、stress convection、outer coupling 或 relaxation 中一个原因族
→ 不得违反 TaskSpec 明确固定的 deltaT/物性/边界
```

如果 TaskSpec 固定量本身导致不可行，只能报告约束冲突，不能静默改值。

- [ ] **Step 3: GREEN、Skill validator 和真实 gate**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c 'from pathlib import Path; import json; from foampilot.knowledge import build_knowledge_manifest; p=Path("src/foampilot/knowledge/knowledge-manifest.json"); p.write_text(json.dumps(build_knowledge_manifest("src/foampilot/knowledge/openfoam10"), indent=2, sort_keys=True)+"\n", encoding="utf-8")'
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_knowledge_retrieval.py tests/test_packaged_skills.py tests/test_skill_validation.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate src/foampilot/skills/openfoam-incompressible-pressure-velocity --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/laminar-planar-couette.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/laminar-planar-couette --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/laminar-planar-poiseuille.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/laminar-planar-poiseuille-holdout --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot solve src/foampilot/qualification/data/tasks/laminar-planar-contraction.yaml --run-root /tmp/foampilot-knowledge-skills-1-1/laminar-planar-contraction-holdout --backend codex-cli --model-name gpt-5.6-sol --max-mpi-ranks 16 --json
```

要求 contraction 的 solver entry 不回退；Couette 若仍失稳，必须出现比“只改 fvSchemes”更有证据的
repair 或明确的 TaskSpec constraint conflict。

## Task 6：全量确定性验证、package 审计与阶段报告

**Files:**
- Modify: `docs/reports/2026-08-05-knowledge-skills-stage-1-1.md`

**Interfaces:**
- Consumes: Tasks 1–5 的 RED/GREEN、真实 run、artifact manifest 和 performance summaries。
- Produces: 可审计的阶段 1.1 结论；不宣称未经验证的泛化能力。

- [ ] **Step 1: 验证所有 Skill 与 Knowledge**

Run:

```bash
for skill_dir in src/foampilot/skills/*; do
  test -f "$skill_dir/SKILL.md" || continue
  PYTHONPATH=src /home/edwin/feal-venv-py312/bin/foampilot skill validate \
    "$skill_dir" --json
done
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_context_assembler.py \
  tests/test_knowledge_retrieval.py \
  tests/test_knowledge_coverage.py \
  tests/test_packaged_skills.py \
  tests/test_skill_validation.py \
  tests/test_artifact_replay.py
```

Expected: 全部通过，frozen replay 零新增 blocking regression。

- [ ] **Step 2: 运行完整测试套件**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: 不低于当前 `494 passed, 5 skipped`，新增测试全部通过；若 skip 数因环境变化，逐项说明。

- [ ] **Step 3: 构建 wheel 并检查 package data**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -B -m build \
  --wheel --no-isolation --outdir /tmp/foampilot-knowledge-skills-wheel-20260805
/home/edwin/feal-venv-py312/bin/python -B -c \
  'from pathlib import Path; import zipfile; wheel=next(Path("/tmp/foampilot-knowledge-skills-wheel-20260805").glob("*.whl")); names=set(zipfile.ZipFile(wheel).namelist()); required={"foampilot/knowledge/knowledge-manifest.json","foampilot/skills/openfoam-multiphase-vof/SKILL.md","foampilot/skills/openfoam-compressible-transient/SKILL.md","foampilot/skills/openfoam-buoyant-cht/SKILL.md","foampilot/skills/openfoam-incompressible-pressure-velocity/SKILL.md"}; missing=sorted(required-names); assert not missing, missing; print(wheel)'
```

Expected: wheel 构建成功且所改 package data 全部存在。

- [ ] **Step 4: 汇总阶段指标**

报告至少包含：

```text
deterministic tests
Skill validators
target/holdout case matrix
target solver started
solver normal completion
public validation pass
strict physics qualification（若执行）
model logical requests / transports / time
backend/environment blockers
remaining case failures
artifact manifest verification
```

对每个失败注明归属：Knowledge、Skill、TaskSpec、mesh、solver numerics、backend、Runner 或 evaluator。

- [ ] **Step 5: 决定是否启动 30 题复测**

仅当以下条件全部成立时启动同协议 30 题：

```text
全部确定性测试通过
全部修改 Skill validator 通过
holdout target-solver entry 无回退
没有新增 backend/environment blocker
目标失败不再重复本轮修正的 startup contract 错误
```

否则停止在阶段报告，继续修正已观察到的通用原因；不得通过增加题目专用知识追求数字。

- [ ] **Step 6: 最终工作区审计**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: 无 whitespace error；报告所有修改和未跟踪文件；不提交、不推送。
