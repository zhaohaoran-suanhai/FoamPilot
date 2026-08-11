# Desktop B 交互式求解 v1 实施与验证

日期：2026-08-11

## 结论

Desktop B v1 已形成可用的本地闭环：用户可以在 PySide6 工作台中选择工程目录、输入自然语言、
审阅并确认 `TaskDraft`、编译 `TaskSpec`、启动规范 `foampilot solve`，并在同一窗口观察公开
Knowledge/Skill 上下文、workflow、OpenFOAM 日志和残差曲线。桌面层仍调用唯一的
`NativeAgent.solve()` 路径，不建立第二套 Agent 或 CFD 状态机。

一个全新的单窗口真实门禁从中文自然语言开始，无手工 YAML，最终达到：

```text
workflow_state = COMPLETED
native_status = PUBLIC_VALIDATION_PASS
manifest_state = verified
knowledge references = 6
skills = 3
residual fields = Ux, Uy, p
residual samples = 399
```

这证明该次参数化层流方腔运行的桌面闭环成立；`PUBLIC_VALIDATION_PASS` 仍不等于独立
qualification `PASS`、网格无关或工程适用。

## 实现范围

- 四个顶层工作区：任务、知识上下文、残差监控、产物；
- 自然语言请求、可见事实/假设/问题表、TaskDraft 与 TaskSpec 高级 YAML 视图；
- 工程内版本化 `requests/`、`drafts/`、`tasks/`，以及每次求解唯一的 `runs/job-*`；
- 固定 executable、子命令白名单和 argv 数组的 `QProcess` 控制器，不使用 shell；
- preflight、model doctor、validate、compile 和 canonical solve 串联；
- 唯一 run 自动发现与每秒 live refresh；
- `agent-context.json`、`repair-agent-context.json` 的公开知识/技能投影；
- Foundation solver stdout 的 residual 解析和 Qt 自绘 `log10(initial residual)` 曲线；
- batch root 识别和具体子 run 选择，避免把批次目录误当单次 run；
- 运行中阻止关闭窗口，避免 Qt 销毁仍在运行的规范子进程；
- TaskSpec 公开资产以所选工程目录为安全根。

## TaskBuilder 兼容修复

真实自然语言门禁先后暴露并修复了三类原有入口问题：

1. `JsonValue` 产生空 JSON Schema，Codex 0.147.0 严格结构化输出拒绝该 schema；传输模型
   现在用 JSON text 承载任意 fact/assumption/candidate 值，进入领域模型前确定性解析。
2. 路径、重复 ID 等错误原先在模型网关之后才被拒绝；现在提前进入传输模型校验，使已有的
   一次结构纠正机制能够处理。
3. 提取提示明确机器枚举、`GeometryInput`、`MeshIntent`、ASCII patch name 和压力参考职责；
   确定性 TaskDraft review 也在显示“可编译”之前直接校验 geometry/mesh 契约。

Task extraction 采用单请求 180 秒、阶段 390 秒、总计 420 秒和最多两次 transport attempt 的
有界预算；这与 case generation 的长模型调用特征相适应，但不会无限等待。

## 验证证据

### 自动测试

```text
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests

620 passed, 8 skipped in 21.84s
```

测试覆盖 batch/run 边界、公开上下文、残差解析、工作区原子写入、QProcess 白名单与流分离、
TaskDraft 确认/编译、唯一 run 发现、live refresh、运行中关闭保护，以及 TaskBuilder schema 与
geometry/mesh 预编译校验。

### 真实运行

| Gate | 入口 | 终态 | UI 证据 |
| --- | --- | --- | --- |
| 直接 TaskSpec | 桌面控制器启动 qualification `laminar-cavity` TaskSpec | `COMPLETED / PUBLIC_VALIDATION_PASS / verified` | 5 条 Knowledge、2 个 Skill、499 个残差点 |
| 单窗口自然语言 | 全新工程、中文请求、确认、编译、solve | `COMPLETED / PUBLIC_VALIDATION_PASS / verified` | 6 条 Knowledge、3 个 Skill、399 个残差点 |

最终自然语言 run：

```text
/tmp/foampilot-desktop-natural-gate-7wjobsbp/runs/
  job-20260811T081501315457Z-824c168e/
  run-20260811T081502052731Z-c90f9c20
```

该路径是本机临时真实门禁产物，不是仓库内伪造 fixture，也不作为长期 qualification 基线。

### 视觉和打包

- 使用 offscreen Qt 在 1440×900 检查任务、知识上下文和残差三个主视图；控件未发生关键遮挡，
  知识表保留横向滚动，残差图能显示 Ux/Uy/p 全部曲线。
- wheel 构建成功：`foampilot-0.1.0-py3-none-any.whl`，SHA256
  `25c06d6417d50f0019d5e12b410823a828d4d386fc296af7cc6e61b9480f7b90`。
- 从 wheel 路径直接导入 `FoamPilotMainWindow`、TaskBuilder transport model 成功。

## 当前边界

Desktop B v1 没有取消、resume、人工 repair、case revision、三维 VTK/PyVista、ParaView、远程
HPC 或多用户能力。运行中的正常窗口关闭会被阻止；进程级崩溃后的 detached worker/reconnect
仍属于后续设计。知识页只展示模型实际收到并固化的公开上下文，不展示隐藏思维过程。
