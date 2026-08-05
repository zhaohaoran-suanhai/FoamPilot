# 第一阶段：知识库与 Skills 实施记录

## 结论范围

本阶段保持 `NativeAgent.solve()`、ExecutionPlan v3、Runner、公开验证和有限 repair 主流程
不变，只增强进入模型前的上下文质量与失败后的知识路由。本文记录当前工作区已经执行的
确定性验证和一个不可压缩真实 gate；不更新历史 30 题通过率，也不据此宣称其他物理族的
求解准确率已经提高。

## 已实现内容

- 增加六物理族、七知识类型的静态 coverage 报告和 CLI；
- 将两个窄 solver Skill 合并为六个可复用物理族 Skill；
- 运行时固定为“通用 Skill + 至多一个物理族 Skill”；
- repair 使用公开验证反馈与失败日志尾部选择 error playbook；
- 失败日志只进入 error-playbook 检索槽位，且归一化后限制为 4 KiB；
- 增加四类通用失败 playbook：缺失 `fvSchemes` 算子、缺失 `fvSolution` 字段求解器、
  thermo 状态失稳和 boundary/patch 不一致；
- 增加多块 `blockMesh` 拓扑一致性知识；
- 所有新增 Knowledge 使用 Foundation v10 源码定位、源文件 SHA256 和公开 leakage metadata。

## 当前确定性证据

| 证据 | 当前结果 | 能证明什么 |
| --- | --- | --- |
| 六个 family Skill 结构/scenario 测试 | 通过 | Skill 可打包、触发边界完整 |
| solver 到 family Skill 映射测试 | 通过 | 每次至多加载一个 family Skill |
| repair evidence 检索测试 | 通过 | 精确日志可以路由到匹配 error playbook |
| Knowledge schema、retrieval、manifest 测试 | 通过 | 41 条 corpus 可解析、可复现且无 manifest drift |
| coverage 语义测试 | 通过 | opt-in 条目不会伪装成通用覆盖 |
| 不可压缩真实 gate | `blockMesh -> checkMesh -> icoFoam` 全部返回 0，`PUBLIC_VALIDATION_PASS` | 新上下文可以生成、检查并执行一个完整原生 case |

真实 gate 使用 `laminar-cavity` 公开 TaskSpec。首次 parent run 在外层工具沙箱内启动外部模型
进程时因应用目录只读而记录为可续跑 `DEFERRED/PROCESS_INTERRUPTED`；严格 child continuation
在正常本机权限下恢复生成。第一次结构化输出 schema 校验失败，Gateway 进行一次预算内重试，
第二次生成有效 ExecutionPlan。之后三个原生步骤均在 bubblewrap Runner 中正常完成，公开
网格、终止时间、字段有限性、连续性和输出检查全部通过。这个过程同时验证了模型错误没有被
误记为 CFD 失败，parent 产物也没有被修改。

## 解释边界

`covered` 表示某物理族和知识类型存在公开候选条目；它不是 benchmark PASS、求解器正常完成
或数值准确性的替代指标。物理族 Skill 是模型行为提示，不是确定性 case renderer。Agent 仍然
负责从公开任务编写完整 OpenFOAM 文件，错误仍需通过静态检查、真实执行和验证闭环发现。

当前全仓确定性结果为 `397 passed, 3 skipped`。后续仍应按可压缩、VOF、浮力/CHT 和固体
四类执行小型真实 gate；只有这些证据产生后，才能推广本报告的运行结论。
