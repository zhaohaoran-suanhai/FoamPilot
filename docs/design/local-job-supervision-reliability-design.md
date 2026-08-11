# FoamPilot 本机任务监督与 Desktop 可靠性规格

状态：已确认，按三项串行任务中的第二项实施。本文依赖
[核心执行可观测性与活性规格](execution-observability-liveness-design.md)，定义单机、单用户
FoamPilot 的可靠取消、窗口关闭后继续运行、崩溃重连和 Qt 响应性。

## 1. 背景与问题定义

当前 Desktop 使用一个 Qt `QProcess` 直接启动 FoamPilot CLI。该对象只在当前窗口进程内有效，
没有持久化 job identity、heartbeat、process group、取消协议或重新连接能力。正常关闭会被阻止；
若窗口或 CLI 异常退出，Desktop 不能可靠区分：

- 后台任务仍在运行；
- worker 已停止但 OpenFOAM/MPI 子进程仍在；
- run 已经完成但界面尚未刷新；
- run 未完成且需要恢复处理。

同时，当前 RunRepository 在 Qt 主线程中周期性全量读取 events、递归扫描文件、解析日志并校验
manifest。小算例尚可使用，但随着时间目录、场文件和日志增长，界面存在确定的扩展性风险。

## 2. 目标

1. Desktop 关闭或崩溃不影响已经提交的本机 job；
2. 重启 Desktop 后可以重新发现并连接仍在运行的 job；
3. 用户可以可靠取消 task draft、generation、repair 和 OpenFOAM command；
4. 取消完成前不宣称 `CANCELLED`，取消失败不隐藏残留进程；
5. worker、模型、OpenFOAM 和 MPI 子进程具有可核验的身份与所有权；
6. 活跃大算例的事件、日志和文件增长不会阻塞 Qt 主线程；
7. job 状态与 CFD workflow/public validation 保持分层；
8. 所有状态保留在用户选择的工程目录中，不依赖系统级服务。

## 3. 非目标

- 不实现常驻系统 daemon、systemd 服务、多用户服务或网络 API；
- 不实现远程/HPC scheduler、SSH 重连或跨机迁移；
- 不实现通用 OpenFOAM 时间目录 continuation；
- 不实现人工 case repair、ParaView 或内嵌三维视图；
- 不允许 Desktop 绕过 `NativeAgent.solve()` 直接执行 solver；
- 不把 job exit code 当作 CFD 成功结论。

## 4. 选择的架构

采用“每个 job 一个可分离 worker”，而不是让 Qt 直接拥有完整任务，也不引入常驻 daemon：

```text
Qt Desktop
  -> 创建不可变 JobSpec
  -> 启动 detached local worker
  -> 读取 JobStatus / ActivityEvent / Run artifact
  -> 通过受控 control request 请求取消

local worker
  -> 持有 job writer lock
  -> 通过同一 application service 调用规范 core/NativeAgent
  -> 监督每个外部进程组
  -> 写 heartbeat、状态和最终证据
```

选择该方案的原因：它能承受 Desktop 生命周期变化，又保持单机部署简单；每个 job 都有独立
故障边界，不需要维护长期运行的后台服务。worker 本身由固定 argv 的 FoamPilot 子命令启动，
内部复用 CLI 使用的 application service，不通过 shell，也不再生成一层嵌套 CLI 进程。

## 5. Job 持久化契约

每次 Desktop 操作使用现有独占 job root，目录形态固定为：

```text
PROJECT/runs/job-<timestamp>-<id>/
  job.json                   不可变 JobSpec
  job-status.json            原子替换的最新 JobStatus
  job-events.jsonl           追加式 ActivityEvent
  worker.stdout.log
  worker.stderr.log
  control/
    cancel-request.json      可选、原子创建、幂等
  run-<timestamp>-<id>/      solve/resume 创建的规范 run
```

`job.json` 不保存 secret、完整模型 prompt 或认证环境，只保存：

```text
schema_version
job_id
operation                   draft | compile | validate | solve | resume | report
created_at
project_root
input_paths                 工程目录内相对路径
input_sha256
requested_runtime_profile
worker_protocol_version
```

`job-status.json` 由 worker 单写，至少保存：

```text
job_id
revision
state
worker_pid
worker_start_token
boot_id
current_child_pid | null
current_child_pgid | null
current_child_start_token | null
current_stage | null
current_step_id | null
run_dir | null
started_at
last_heartbeat_at
finished_at | null
terminal_code | null
```

所有路径在创建时解析并验证位于显式 project/job root 内，拒绝 symlink 和路径穿越。

## 6. Job 状态机

持久化状态为：

```text
SUBMITTED
-> STARTING
-> RUNNING
-> CANCEL_REQUESTED
-> CANCELLING
-> CANCELLED | COMPLETED | FAILED
```

`ORPHANED` 是重连时根据 receipt、lock、heartbeat 和进程身份推导的诊断状态，不由仍在运行的
旧 worker 自报。`UNRESPONSIVE` 是 heartbeat 过期的展示状态，也不是 CFD terminal state。

约束：

- 单个 job 只有持有 writer lock 的 worker 可以更新 status；
- 多个 Desktop 可以只读观察，但控制请求必须幂等；
- `COMPLETED` 表示 worker 已得到并固化规范 terminal outcome，即使该 outcome 是 solver 或
  validation failure；它不代替 `PUBLIC_VALIDATION_PASS`；
- job `FAILED` 只表示 worker/protocol 未能产生可验证的规范 terminal outcome，不能用来覆盖
  run summary 中更具体的 CFD failure；
- solve run 只有在相关进程组已经确认退出、summary 已写入且 artifact 已固化后才能标为
  `CANCELLED`；
- 无法确认子进程全部退出时使用稳定错误 `JOB_CANCEL_INCOMPLETE`，不得误报取消成功。

## 7. 进程监督与取消

### 7.1 所有权

worker 本身与 Desktop 分离。每个模型或 OpenFOAM 外部调用建立独立、可记录的 Linux
process group，并保存 PID、PGID、boot ID 和 `/proc` start token，避免 PID 重用误杀。

worker 必须能监督：

- command-model backend；
- Foundation OpenFOAM utility/solver；
- `mpirun` 及其 ranks；
- bubblewrap 内部进程；
- trusted-host fallback 子进程。

bubblewrap `--die-with-parent` 只能作为额外保护，不能替代统一监督。worker 意外退出后，后续
reconciler 只能对身份完全匹配的残留进程执行清理。

### 7.2 取消协议

Desktop 不直接向 PID 发送信号，而是原子创建 `cancel-request.json`。worker 读取后按顺序执行：

```text
记录 CANCEL_REQUESTED
-> 停止启动新的 stage/command
-> 向当前进程组发送 SIGTERM
-> 等待统一配置的 grace period
-> 对仍存活且身份匹配的进程组发送 SIGKILL
-> 确认整组退出
-> 固化 partial logs、events、summary 和 manifest
-> 标记 CANCELLED
```

重复请求不改变第一次请求时间和最终语义。若命令恰好在取消请求前自然完成，worker 根据已确认
的事件顺序决定进入下一安全点还是完成取消，不能同时写入 `COMPLETED` 和 `CANCELLED`。

`WorkflowState` 增加 `CANCELLED`。取消不是 solver failure，不进入自动 repair，也不能触发
qualification PASS/FAIL 推导。

## 8. Desktop 重连

Desktop 启动或切换工程目录时：

1. 扫描受控 `runs/job-*`，不扫描任意用户目录；
2. 验证 `job.json`、status revision、路径和进程身份；
3. worker 存活且 heartbeat 新鲜时显示 running 并订阅增量事件；
4. worker 存活但 heartbeat 过期时显示 unresponsive，并允许请求取消；
5. worker 已消失但没有 terminal status 时显示 orphaned；
6. terminal job 继续从规范 run summary/manifest 渲染结果。

关闭 Desktop 不发送隐式 cancel。用户可以选择“仅关闭窗口”或先明确请求取消。Desktop 不再因
有任务运行而强制拒绝正常关闭。

## 9. Qt 响应性与增量读取

RunRepository 拆分为 Qt 无关的数据源和增量 cursor：

- workflow/activity/job JSONL 使用文件 identity + byte offset 读取新增完整行；
- 日志按 inode、size 和 offset 增量解析，截断或轮转时显式重置；
- 文件树根据目录快照增量更新，不在每次 tick 递归重建；
- finalized manifest 在文件 identity 未变化时只验证一次；
- 大型读取、hash 和解析进入受控后台线程；
- Qt 主线程只接收不可变 projection/diff 并渲染；
- 更新频率自适应，后台处理落后时合并中间帧，不无限排队。

不得在后台线程启动第二条 solver 路径。旧 snapshot 在一次刷新失败时继续显示，并明确标记
`DESKTOP_REFRESH_DEGRADED`，不能把读取错误归因于 CFD。

## 10. 崩溃与错误处理

- Desktop 崩溃：worker 继续，重启后 attach；
- worker 崩溃：job 进入 orphan diagnosis，由下一规格定义恢复动作；
- 当前外部进程意外退出：worker记录原始 return/signal，按现有 failure domain 完成工作流；
- status 原子写失败：worker停止启动新 command，保留日志并报告 `JOB_STATUS_WRITE_FAILED`；
- event sink 降级：沿用核心规格的 `OBSERVABILITY_DEGRADED`，不能伪造求解失败；
- manifest invalid：允许安全只读查看，但不允许 resume 或标记 verified；
- 无法确认 PID identity：只报告 orphaned/unknown，不发送信号。

## 11. 测试与验收

### 11.1 确定性 fake-worker gate

- Desktop 提交后关闭，worker 继续并完成；
- Desktop 被强制终止，重启后自动 attach；
- heartbeat 过期但 worker 存活，显示 unresponsive；
- worker 和 child 都消失且无终态，显示 orphaned；
- PID 被重用时拒绝 attach/kill；
- generation、solver、MPI 三类任务均能取消且无残留后代；
- SIGTERM 无效时按 grace period 升级 SIGKILL；
- cancel 与自然完成竞争时只有一个 terminal state；
- job/status/event 文件半写、尾部半行和 revision 回退被识别；
- 两个 Desktop 只读观察不产生第二个 worker。

### 11.2 Desktop 性能 gate

构造包含大量 workflow events、增长日志、residual 和时间目录的 synthetic run，证明：

- Qt 主线程不执行递归扫描、日志全文解析或 manifest 全量 hash；
- 用户交互持续响应；
- 后台解析落后时内存队列保持有界；
- 最终 projection 与一次完整重建结果一致。

### 11.3 本机 Foundation v10 gate

从 Desktop 提交规范 solve，依次验证：关闭窗口、重新打开、重新连接、观察 residual、主动取消
另一任务、确认无 OpenFOAM/MPI 残留，以及 completed/cancelled run 的 artifact 可检查。

## 12. 完成定义与后续依赖

本规格完成表示本机 Desktop 不再拥有任务生命周期，用户可以可靠关闭、重连和取消，且大型
active run 不阻塞 UI。它不表示任意 worker crash 都能继续原命令，也不表示 OpenFOAM 可以从
任意时间目录续算。

下一规格根据这里的 job receipt、identity、lock 和 terminal state，定义 orphan recovery、
strict resume、rerun 与未来 OpenFOAM continuation 的不同语义。
