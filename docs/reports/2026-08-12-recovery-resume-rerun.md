# Recovery、Strict Resume 与 Rerun 验证报告

日期：2026-08-12
范围：三项本机可靠性路线中的第三项；当前分支 `main`。

## 结论

FoamPilot 现在能根据 receipt、writer lock、heartbeat、完整进程身份和 artifact manifest，
确定性区分仍在运行、无响应、活动孤儿、停止孤儿、已固化和证据损坏的本机任务。Desktop 与
CLI 只开放证据支持的操作，不再把“重新连接”“固化中断”“恢复模型请求”和“完整重跑”混成
同一个 resume。

本轮没有实现或宣称 OpenFOAM 时间目录 continuation。严格 resume 仍只适用于已经由正常终止
路径声明可重试的模型生成/修复阶段；完整 rerun 从头执行规范 solve，并创建新 run。

## 已实现能力

- `job reconcile` 返回严格的 `RecoveryDecision`，且 reconcile 本身只读；
- 活动孤儿只能在 PID、PGID、boot ID 和 start token 全部匹配时受控终止；
- 停止孤儿可固化为 `WorkflowState.INTERRUPTED`，写入 `interruption.json`、中立 workflow
  blocker、终态 event 和 manifest，不伪造 native CFD 状态；
- manifest 与 recover-finalize 的关键 JSON 使用原子排他写入；在 summary 写完、manifest 尚未
  写入时被中断，重复 recover-finalize 可以幂等完成；
- strict resume 支持 parent 与 child 位于不同 job artifact root，并使用被 manifest 固化的累计
  continuation/transport/logical/execution 预算；
- `foampilot rerun` 区分 `rerun_same_input` 与 `rerun_with_changes`，记录 parent manifest、输入
  hash、change category 和零隐式复用的 `lineage.json`；
- Desktop 启动后优先连接活动/异常 job，没有待处理任务时回到最近已固化 job；界面提供取消、
  终止孤儿、固化中断、恢复模型生成/修复和完整重跑，并按决策矩阵禁用不合法操作；
- `LineageRecord` 不包含 `openfoam_continuation` 枚举，界面明确显示该能力当前不支持；
- strict resume 使用持久化 monotonic step elapsed 强制执行跨 parent/child 的累计 OpenFOAM
  wall budget；runtime/isolation、bubblewrap、MPI、OpenFOAM/Gmsh 路径与 rebuild-sensitive identity
  纳入兼容性证据；
- job 状态写入连续失败会停止后续工作、重试终态并留下独立控制面故障证据；正常 pre-run 取消
  被识别为 `JOB_CANCELLED_BEFORE_RUN`，不会误报为 artifact corruption。

## 自动化验证

最终 offscreen 全量门禁：

```text
840 passed, 12 skipped in 32.19s
```

覆盖范围包括恢复决策表、PID 重用、writer lock、孤儿进程组终止、recover-finalize 幂等与故障
注入、原子 manifest、跨 job strict resume、monotonic 累计预算、runtime/executable identity、
rerun lineage、parent 不可变性、CLI 取消竞态，以及 Desktop 全状态动作矩阵、启动发现和新任务
期间旧动作失效。最终独立代码审查结论为无 Critical、无 Important，代码层面 READY。

`git diff --check` 通过。OpenFOAM continuation 源码审计只发现“不支持”的界面/文档说明；执行
schema、CLI 和 Agent 均无该入口。

## 本机 Foundation OpenFOAM v10

运行根：`/home/edwin/workplace/OpenFOAM-10`；执行策略：`trusted_host`。

- preflight：`PASS`，Foundation v10 与 workspace blocking checks 通过；
- detached worker：真实执行 `blockMesh -> checkMesh -> icoFoam`；
- strict repair resume：先真实制造 solver failure 与后端 deferred，再恢复模型修复并重新执行
  solver，最终 `PUBLIC_VALIDATION_PASS`；
- cold rerun：parent 与 child 都真实执行完整 solver 流程，child 为 `rerun_same_input`；
- strict resume 与 rerun 均验证 parent manifest 字节未改变，parent/child manifest 校验为空。

上述 detached、strict resume 和 cold rerun 三项最终组合门禁为：

```text
3 passed in 50.62s
```

模型响应由确定性的本地 scripted/frozen gateway 提供，因此这里证明的是恢复编排、artifact 与
真实 Foundation v10 Runner，不是外部模型服务可用性或模型质量门禁。

## 构建与隔离导入

仓库根的 `build/` 会遮蔽未安装的同名 Python 包，本轮继续直接调用项目声明的 setuptools
backend 构建 wheel/sdist。最终产物：

```text
foampilot-0.2.0-py3-none-any.whl
sha256 = 3e7c047f366b105775dd66d55e30f3984ec4aeb070c390d9c29ba64d89d49122

foampilot-0.2.0.tar.gz
sha256 = c62522ac594d6156bb987312dd6c0ad629ac24ae254ddfcccabecf3144548441
```

wheel 安装到源码树外的临时 target 后，成功从该 target 导入 `foampilot.jobs`、
`FoamPilotMainWindow` 和 `LineageRecord`，并确认 CLI 包含 `job reconcile` 与 `rerun`。

## 尚未覆盖的边界

- 没有第二台干净 Ubuntu + Foundation v10 主机，因此跨机安装门禁仍未验证；
- Qt 使用 offscreen 自动化，未把本轮结果描述为真实鼠标点击/窗口管理器门禁；
- 没有外部模型网络请求，不能据此证明 provider 服务稳定性；
- OpenFOAM continuation、人工 repair、case revision、ParaView/三维视图和远程 HPC 仍属于后续
  独立任务。

## 对前三项串行任务的意义

第一项让长步骤持续给出结构化活动与残差，第二项让任务脱离窗口存活、可取消和可重连，第三项
让异常退出后可以安全判定、固化或创建有 lineage 的新运行。三项合起来解决的是“长时间无反馈、
关闭界面丢观察、异常后不知道能否继续”的本机可靠性问题；它们不把数值收敛、物理正确性或任意
OpenFOAM 断点续算伪装成已解决。
