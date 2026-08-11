# 核心执行可观测性与活性实施报告

日期：2026-08-12

## 结论

三项串行任务中的第一项已经实现。长时间模型调用与 OpenFOAM 命令现在通过同一个 Qt 无关的
`ActivityEvent` 契约报告开始、心跳、日志增长、残差和终态；CLI 可在 stderr 输出 plain 或
JSONL 进度，最终 `--json` 仍独占 stdout。run 创建后，活动流持久化为
`activity-events.jsonl`，观测通道自身的降级状态写入 `observability.json`。

这项结果解决“长时间没有反馈时，无法区分仍在工作、已有数值进展、已经失败或已经超时”的
核心协议缺口。它不提供取消、Desktop 崩溃重连或 OpenFOAM 断点续算，这些属于后续两个规格。

## 实现范围

- 严格、线程安全、脱敏且连续编号的 `ActivityEvent`/`ActivityReporter`；
- append-and-flush JSONL、plain stderr 和 JSONL stderr 三种 sink；
- 固定 argv、独立进程组、心跳、deadline、超时杀组并 reap 的进程监督器；
- ModelGateway transport attempt 的 started/heartbeat/completed/failed 活性事件；
- OpenFOAM step 的即时 workflow lifecycle、日志字节增长和增量 residual 指标；
- `task draft`、`plan`、`solve`、`resume` 的 `--progress auto|plain|jsonl|none`；
- Desktop 对 JSONL 活动和普通 stderr 诊断的分流；
- `activity-events.jsonl` 与 `observability.json` run artifact。

事件不保存模型 prompt、response body、credential、环境变量值或隐藏推理。heartbeat 只表示进程
活性，不代替 CFD 完成、公开验证或 qualification 结论。

## 验证证据

### 自动测试

```text
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests

757 passed, 10 skipped in 24.57s
```

覆盖活动模型、sink 降级、真实静默子进程心跳、进程组超时清理、模型重试、OpenFOAM 事件顺序、
增量残差、CLI stdout/stderr 兼容、run 持久化与 Desktop JSONL 解码。

### 构建

仓库根目录的 `build/` 会遮蔽未安装的 Python `build` 包，因此使用项目声明的 setuptools
backend 直接完成等价的 wheel/sdist 门禁：

```text
foampilot-0.2.0-py3-none-any.whl
sha256 = 1ac1247b18848cdae381b4ddb28ac00a2e164155a86693a2712f5e0ab4306d71

foampilot-0.2.0.tar.gz
sha256 = 18d2fe7b4ae088ecd8629b34144f0f93da0a3f0fd0a7eda9296c359b681f767d
```

### 本机 Foundation v10

- `preflight --openfoam-root /home/edwin/workplace/OpenFOAM-10 --json`：`PASS`；Foundation v10
  和 workspace blocking checks 通过。当前会话的 bubblewrap 完整 namespace probe 失败，但
  `sandbox_preferred` 允许低风险 host fallback，因此该项是非阻塞诊断。
- `model doctor --json`：`codex-cli / gpt-5.6-sol` 被探测为 available。
- 冻结的 non-tutorial side-driven-box 计划在 `trusted_host` 下真实执行
  `blockMesh -> checkMesh -> icoFoam`：`1 passed in 7.67s`，断言
  `PUBLIC_VALIDATION_PASS` 且 manifest 无问题。

直接自然语言 CLI gate 在当前开发工具的只读宿主环境中没有获得模型结果。三次 transport
attempt 均以 `PROCESS_INTERRUPTED` 结束，原始脱敏诊断为 Codex app-server 无法在只读文件系统
建立运行状态。FoamPilot 如实固化为 `DEFERRED`，允许从
`MODEL_GENERATION_STARTED` strict resume；该 run 的 manifest 校验无问题，包含 8 条连续活动事件，
`observability.json` 为 `ok`。这不是求解 PASS，也没有被报告成求解失败。

## 当前边界

- 本轮证明确定性可观测性契约和本机 Foundation v10 冻结计划执行，未声称自然语言真实模型门禁
  在当前只读宿主中通过；
- Desktop 仍直接拥有 QProcess 生命周期，尚不能关闭后继续、重连或可靠取消；
- strict resume 仍只覆盖既有 generation/repair 可重试终点；
- 通用 OpenFOAM continuation 仍不支持。
