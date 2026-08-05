# Solver-family Skills 与公开自检

## 目的

工具包为六类常见 Foundation OpenFOAM v10 物理问题保留可移植 Skills，包括不可压缩
压力—速度耦合、可压缩流、多相 VOF、浮力/共轭换热、固体力学与标量/势场。它们描述
可复用 solver 语义和公开验收检查，不包含目标 tutorial 或私有 golden data。

使用共享 scenario suite 校验：

```bash
foampilot skill validate src/foampilot/skills/openfoam-incompressible-pressure-velocity
foampilot skill validate src/foampilot/skills/openfoam-compressible-transient
foampilot skill validate src/foampilot/skills/openfoam-multiphase-vof
foampilot skill validate src/foampilot/skills/openfoam-buoyant-cht
foampilot skill validate src/foampilot/skills/openfoam-solid-mechanics
foampilot skill validate src/foampilot/skills/openfoam-scalar-field-transport
```

运行时只装配通用 Skill + 至多一个物理族 Skill。这里的结构和 scenario 验证只证明 Skill
契约完整、触发边界可检查；coverage 不等于求解能力已经通过验证，仍需真实 OpenFOAM gate。

## 精确激波管审计

使用公开 pilot 输入的示例：

```bash
foampilot audit shock-tube \
  --left-pressure 100000 \
  --left-temperature 348.432 \
  --right-pressure 10000 \
  --right-temperature 278.746 \
  --molecular-weight 28.96 \
  --cp 1004.5 \
  --time 0.007 \
  --json
```

自检还会读取 `deltaT`、`adjustTimeStep`、`maxCo` 与 `maxDeltaT`，解析两种
Foundation Courant 日志格式，并以 cell width 为单位检查检测到的 rarefaction、contact
和 shock 位置。

## 真实壁面热流审计

对任一已完成且兼容的算例运行：

```bash
foampilot audit wall-heat-flux CASE_DIR \
  --openfoam-root /home/edwin/workplace/OpenFOAM-10 \
  --hot-patch HOT_PATCH \
  --cold-patch COLD_PATCH \
  --json
```

该命令将算例复制到临时目录，并以 `-postProcess -func wallHeatFlux -latestTime`
调用 case application。这样会构造 `buoyantFoam` 所需、而通用 `postProcess` utility
不会构造的 thermophysical transport model。源算例不会被修改。

对稳态 buoyant 结果，公开报告综合：

- 方程 initial residual 首/末窗口中位数；
- 终止 local 与 cumulative continuity error；
- 热壁/冷壁积分 transport-model `Q`；
- 归一化 wall-energy imbalance。

缺少证据时按失败处理。正常 `End` line 仍只是执行结果，不是 public physics verdict。

## Agent 集成

可选 Agent adapter 可以在 solver 完成后调用这些检查。失败检查应与 solver error 一样进入
保留证据的 repair loop。Callback contract 只能接收：

- 公开任务；
- Agent workspace；
- 当前 solver run 与日志。

它不得接收私有 validation model 或 golden manifest。
