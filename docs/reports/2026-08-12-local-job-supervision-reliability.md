# 本机任务监督与 Desktop 可靠性实施报告

日期：2026-08-12

## 结论

三项串行任务中的第二项已经实现。FoamPilot 的长任务现在由工程目录内的独立本机 worker
持有，而不是由 Qt 窗口持有；Desktop 可以关闭后继续、重启后重新连接、显式请求取消，并在
worker 心跳过期时区分 `UNRESPONSIVE`。模型、OpenFOAM、MPI 和 bubblewrap 外部调用统一进入
进程组监督，只有确认所拥有的进程组退出后才固化为 `CANCELLED`。

同时，活跃 run 的 workflow 与 solver log 改为 inode/offset 增量读取，残差历史有界，文件树
扫描节流，未变化的 finalized manifest 不重复全量哈希。Run projection 在 Qt 后台线程构建，
刷新失败保留上一个有效画面并报告 `DESKTOP_REFRESH_DEGRADED`。

## 实现范围

- 严格 `JobSpec`、`JobStatus`、取消请求、输入哈希、原子状态文件和单 writer lock；
- PID、process group、boot ID 与 `/proc` start token 组成的进程身份；
- `worker run`、`job status`、`job cancel` 本机 CLI 协议；
- detached worker heartbeat、durable stdout/stderr/activity events 与 terminal status；
- Desktop detached submit、受控 job 扫描、attach、取消、关闭后继续与状态恢复；
- generation/model retry/OpenFOAM command 的协作取消及 SIGTERM/SIGKILL 升级；
- `WorkflowState.CANCELLED`，取消不伪装成 solver failure，也不触发自动 repair；
- workflow JSONL、OpenFOAM residual 的增量 cursor，以及后台 projection 合并。

## 验证证据

### 自动测试

```text
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests

785 passed, 11 skipped in 26.22s
```

这套门禁覆盖 receipt/path/secret 边界、writer lock、PID 重用、heartbeat、detached parent exit、
重复取消、SIGTERM/SIGKILL、后代清理、cancel/complete 竞争、Desktop close/attach、过期心跳、
JSONL 半行与轮转、增量 residual、manifest cache、后台 Qt projection 和旧 CLI 兼容性。

### wheel/sdist 与隔离导入

setuptools backend 构建成功；从临时目录安装 wheel 后，在源码树外成功导入 `foampilot.jobs`、
`foampilot.desktop.cursors`，并确认 CLI 包含 `job` 与内部 `worker` 命令。

```text
foampilot-0.2.0-py3-none-any.whl
sha256 = ee2e8792295bac952ca6f735d0764ce4fac907c94dd18166200bbf1aa1d8efb5

foampilot-0.2.0.tar.gz
sha256 = 77cb038e6c9b9ba59ac9561c8e747cc69e033c4eaff445631a1b64a16b912027
```

### 本机 Foundation OpenFOAM v10

新增的 opt-in `test_real_detached_job_reuses_verified_plan_and_finalizes` 在
`/home/edwin/workplace/OpenFOAM-10`、`trusted_host` 下执行通过：先建立带 manifest 的
non-tutorial side-driven-box verified-plan 来源，再由真正 detached worker 复用该计划执行
`blockMesh -> checkMesh -> icoFoam`。结果为 `1 passed in 27.64s`，job terminal 为
`COMPLETED / CLI_EXIT_0`，run summary 为 `PUBLIC_VALIDATION_PASS`，manifest 校验为空。

真实求解门禁证明了 detached worker 与 Foundation v10 的组合路径。Desktop 关闭、重连和取消
使用 offscreen Qt 与真实 OS 子进程组完成确定性测试；本轮没有把 headless 自动化描述成用户
手工点击的图形桌面实机验收，也没有为了测试而人为延长真实 icoFoam 求解。

## 当前边界

- `UNRESPONSIVE` 和 worker 异常消失目前只能诊断；安全 recover-finalize、orphan child 处理、
  strict resume 与 rerun 的动作矩阵属于第三项任务；
- 当前 resume 仍仅恢复既有 generation/repair 可重试边界，不是任意 OpenFOAM 时间目录续算；
- 没有引入 daemon、systemd、远程/HPC 或第二条 solver 路径；
- 当前外部模型真实调用受开发宿主限制，未在本报告中伪装为可用门禁；
- 跨机安装和真实图形桌面点击门禁仍未验证。
