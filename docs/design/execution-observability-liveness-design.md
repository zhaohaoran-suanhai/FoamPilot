# FoamPilot 核心执行可观测性与活性规格

状态：已实施并通过确定性门禁；本机真实模型门禁受当前宿主只读运行环境阻塞。本文只定义核心 CLI、模型后端与
OpenFOAM Runner 的长任务可观测性，不依赖 Desktop，也不实现取消、重连或求解断点续算。

## 1. 背景与问题定义

FoamPilot 当前可以完成规范求解，但模型 generation/repair 和 OpenFOAM command 都以同步
调用为主。调用返回前，外部观察者通常只能看到阶段入口，不能稳定回答：

- 当前正在执行哪个逻辑请求或 typed command；
- 子进程是否仍存在；
- 最近一次真实输出或数值进展发生在何时；
- 已运行多久、距离 deadline 还有多久；
- 当前属于正常静默、仍有进展、失去活性还是已经超时。

这不是 Desktop 特有问题。纯 CLI、未来 Web 客户端和远程调度器都会遇到同一缺口。
Desktop 增加动画或定时刷新不能修复核心执行协议。

现有 `workflow-events.jsonl` 是低频、可审计的业务里程碑。它不适合承载每数秒一次的
heartbeat，也不能把“进程存在”误写成“CFD 有进展”。

## 2. 目标

本规格完成后：

1. `task draft`、`plan`、`solve` 和 `resume` 的长模型请求都能报告活性；
2. 每个 OpenFOAM command 在真正启动前报告开始，在结束后立即报告结果；
3. solver log 增长、时间步、迭代和 residual 可以增量报告；
4. CLI 在无 Desktop 环境中也能展示当前阶段、elapsed、deadline 和 last activity；
5. 消费者能严格区分业务状态、进程活性和语义进度；
6. 观测数据不保存 prompt、模型响应正文、secret 或隐藏 chain-of-thought；
7. 现有最终 JSON、summary、manifest 和 `NativeAgent.solve()` 真相源保持兼容。

## 3. 非目标

- 不实现 Desktop cancel、窗口关闭后继续运行或崩溃重连；
- 不定义 job receipt、跨进程控制协议或常驻 daemon；
- 不实现任意 OpenFOAM 时间目录断点续算；
- 不推断模型内部百分比、token 级进度或隐藏推理过程；
- 不用 heartbeat 替代 timeout、public validation 或 qualification；
- 不建立第二套 CFD workflow 状态机。

## 4. 核心设计

### 4.1 两层事件

FoamPilot 保留两种不同用途的事件：

| 层 | 用途 | 典型内容 | 持久性 |
| --- | --- | --- | --- |
| `WorkflowEvent` | 业务里程碑与最终证据 | generation 开始、plan ready、command 开始/完成、validation | 必须可靠追加并进入 artifact |
| `ActivityEvent` | 运行期活性与增量观测 | heartbeat、log 增长、residual、elapsed、deadline | 可流式消费；run 存在后追加到 `activity-events.jsonl` |

`WorkflowEvent` 继续使用连续 sequence 和现有 append/fsync 语义。heartbeat 不写入
`workflow-events.jsonl`，避免高频事件膨胀业务时间线。

`ActivityEvent` 是 Qt 无关的严格模型，首版字段为：

```text
schema_version = 1
sequence                  单个 activity sink 内连续递增
operation_id              一次 CLI/worker 操作的稳定 ID
run_id | null             run 创建后填写
kind                      stage | command | heartbeat | log | metric | warning
state                     started | alive | progressed | completed | failed | timed_out
source                    task_builder | model | runner | validator | workflow
occurred_at               UTC wall-clock
elapsed_seconds           自 operation/stage 开始的 monotonic 时长
deadline_seconds | null   当前调用硬期限
attempt | null
stage | null
step_id | null
pid | null
detail_code | null        稳定英文 code
message                   无秘密、有限长度的人类可读消息
metrics                   只允许注册过的数值指标
evidence_path | null      run 内相对路径
evidence_offset | null    对增量日志的已确认字节位置
```

`ActivityEvent` 不能作为求解成功依据。最终成功仍由 summary、public validation、
qualification 和 manifest 决定。

### 4.2 活性与进展语义

以下词义固定：

- `alive`：受监督进程仍存在，监督循环仍能正常轮询；
- `progressed`：获得了新的真实证据，例如日志字节、模型后端状态、时间步或 residual；
- `silent`：进程 alive，但一段时间内没有新的语义或日志证据；
- `unresponsive`：消费者连续三个 heartbeat 周期没有收到新的 heartbeat；
- `timed_out`：执行器已到达硬 deadline，并开始终止该调用。

默认 heartbeat 周期为 5 秒，消费者在 15 秒无 heartbeat 后显示 `unresponsive`。这些默认值
可由 FoamPilot 应用配置调整，但不由模型或 TaskSpec 改写。`silent` 不触发自动失败；只有
timeout、明确进程退出或上层取消协议可以终止执行。

## 5. 组件边界

### 5.1 Activity sink

核心层定义同步、轻量的 `ActivitySink` 接口。生产者只提交结构化事件，不导入 Qt，不决定
如何渲染。组合 sink 可以同时写入：

- CLI stderr；
- run 内 `activity-events.jsonl`；
- 后续本机 worker 的 `job-events.jsonl`；
- 测试收集器。

业务 `WorkflowStore.record()` 失败属于 workflow 持久化失败，必须停止规范工作流。
Activity sink 暂时不可写时，不应把数值求解伪装成失败；系统必须记录
`OBSERVABILITY_DEGRADED`，并在最终 summary/report 中暴露该降级。

### 5.2 受监督外部进程

模型后端和 PlanRunner 共享一个 Qt 无关的受监督进程抽象，负责：

- 固定 argv、`shell=False` 和受控环境；
- 启动、poll、deadline 和返回码；
- 非阻塞排空 stdout/stderr，避免 pipe deadlock；
- 以 monotonic clock 计算 elapsed；
- 周期性 heartbeat；
- 将取消 token 和进程组终止留作下一规格接入点。

本规格不把所有外部命令强行合并成同一种业务结果；模型错误仍映射为
`BackendError`，OpenFOAM 结果仍映射为 `PlanStepResult`。

### 5.3 模型 generation/repair

模型调用必须在启动外部 backend 前发出 stage/command started，并在调用期间持续发出
heartbeat。可以报告：

- backend ID、model ID、purpose 和 logical/transport attempt；
- PID、elapsed、deadline；
- 已读取的诊断字节数；
- 成功、失败、timeout 和稳定 failure code。

不得报告或持久化：

- system/user prompt；
- 模型响应正文或结构化 case 内容；
- authentication header、token 或环境变量值；
- 推测的思维步骤、完成百分比或 token 数。

若外部 backend 不提供语义进度，系统只报告 alive/silent。生成的正式结果仍通过原有受控
output file 和 Pydantic schema 校验进入工作流。

### 5.4 OpenFOAM PlanRunner

PlanRunner 必须按以下时序工作：

```text
冻结 execution policy
-> 持久化 OPENFOAM_STEP_STARTED
-> 启动受监督 command
-> heartbeat / log offset / parsed metric
-> command 退出或 timeout
-> 固化 stdout/stderr 与 PlanStepResult
-> 持久化 OPENFOAM_STEP_COMPLETE 或 FAILED
```

不得再等全部 `PlanRunner.run()` 返回后批量补写 step started/completed。每个 step 的开始事件
必须先于对应 OS 进程创建或紧邻创建成功，并在后续事件中保持同一 `attempt + step_id`。

日志解析采用增量字节 offset。原始 stdout/stderr 仍是权威证据，ActivityEvent 只保存：

- 新增字节范围；
- Foundation v10 已支持格式中实际解析到的 simulation time、iteration、field、initial/final
  residual 和 solver iterations；
- 解析警告。

解析不到 residual 时显示“尚无可解析 residual”，不能合成零值。日志解析失败不得终止
OpenFOAM command。

## 6. CLI 契约

现有 `--json` 最终 stdout 必须保持单个最终结果，不被实时事件污染。新增统一选项：

```text
--progress auto|plain|jsonl|none
```

- `auto`：TTY 中原位刷新简明状态，非 TTY 不输出高频内容；
- `plain`：向 stderr 输出有限频率的人类可读行；
- `jsonl`：向 stderr 输出完整 `ActivityEvent` JSONL；
- `none`：关闭 CLI 实时渲染，但不关闭 run 内必要证据。

Desktop 后续使用 `--progress=jsonl` 或读取持久化事件，不解析自然语言状态行。

## 7. 错误、安全与资源限制

- 所有 message 经过现有 secret sanitization，并设置长度上限；
- model stdout/stderr 不作为可公开日志复制到 run；
- evidence path 必须是 run 内相对路径，不跟随 symlink；
- heartbeat 线程或 poll loop 不能突破原有 wall、memory、MPI 和 sandbox policy；
- activity sequence 中断或尾部半行由消费者标记为观测损坏，不推导 CFD 失败；
- wall-clock 用于展示，timeout 与 elapsed 计算必须使用 monotonic clock；
- Activity sink 的回调不得阻塞执行器，慢消费者需要有界队列和降级告警。

## 8. 测试与验收

### 8.1 确定性测试

- 静默 30 秒的 fake model：5 秒内出现 heartbeat，不能出现虚假 progress；
- 持续输出的 fake model：只暴露计数和安全状态，不泄露正文；
- fake command：started 事件严格早于子进程完成；
- 无 residual 日志：保持 alive/silent，不生成零曲线；
- 多时间步 residual：offset、顺序和字段关联正确；
- timeout：先记录 timed_out，再返回现有稳定 failure；
- activity sink 故障：求解结果不被伪装，最终报告包含 observability degraded；
- `--json --progress=jsonl`：stdout 仍只有最终 JSON，stderr 每行均为合法事件。

### 8.2 本机真实 gate

使用 Foundation OpenFOAM v10 最小真实算例证明：

1. CLI 在 `blockMesh -> checkMesh -> solver` 每步开始前可见当前 command；
2. solver 运行时能看到真实 residual 或明确的等待状态；
3. 最终 summary、manifest 和 public validation 与改造前语义一致；
4. Desktop 未安装时全部核心能力仍可工作。

## 9. 完成定义与后续依赖

本规格完成不代表任务可以可靠取消或 Desktop 可以重连。完成证据必须包含核心单元测试、
CLI fake-process gate 和本机真实 OpenFOAM gate。

下一规格只消费这里定义的 `ActivityEvent` 和受监督进程接口，不复制模型/OpenFOAM 进度逻辑，
也不创建第二套事件语义。
