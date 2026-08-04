---
name: openfoam-author-benchmark
description: Use when creating or revising an OpenFOAM Agent benchmark task, leakage group, evaluator contract, protocol freeze, immutable attempt policy, or aggregate report.
---

# 编制 OpenFOAM Agent 基准评测

## 核心原则

评测对象是算例编写与推理能力，而不是 tutorial 检索能力。Agent 的公开输入与
评测器的私有资产属于相互隔离的信任域。

## 必需流程

1. 定义评测主张与样本总体：Foundation 版本、算例族、任务数、重复次数、资源限制、
   Agent/model 配置、统计分母和成功标准。
2. 从物理意图编写公开任务卡。任务卡可以描述几何、物理、工况、必需观测量、资源
   边界以及允许使用的通用文档，但不得泄露：
   - 官方目标路径、basename、文件或具有辨识度的字典内容；
   - golden 数值、私有容差或 validator 实现；
   - 能唯一定位目标的检索提示。
3. 将官方目标映射、源文件副本、golden 生成过程、阈值和私有 validator 放入仅评测器
   可访问的存储。不得向 Agent 适配器提供可读取这些内容的路径或 API。
4. 为每个任务指定 leakage group，覆盖整个目标族、别名、变体、派生摘要和 pilot-derived
   知识。先按该组过滤，再审计 Agent 实际可见的语料。
5. 按顺序定义 validator gates：隔离/合规、算例完整性、网格、求解有效性、公开物理检查
   和私有 golden 一致性。区分 `FAIL_AGENT`、`BLOCKED_ENVIRONMENT`、
   `INVALID_AGENT_RUN` 和 `INVALID_BENCHMARK`；只有有效且可归因于 Agent 的 attempt
   才进入成功率分母。
6. 在第一次正式 attempt 前冻结完整协议，而不仅是选中的算例。冻结内容包括所有公开/
   私有 manifest、各 leakage 过滤后的语料状态、知识与 Skill 版本、evaluator/golden
   hash、环境、Agent 适配器、backend/model 和资源策略。任何漂移都必须产生新的协议版本。
7. 分配一个新的独占 attempt 目录，记录公开 prompt、生成文件、命令、日志、环境、hash、
   观测、verdict 和 reason code。不得覆盖不可变 attempt，也不得事后改写其标签。
8. 报告通过、失败、被阻断/无效的排除项、各 gate 通过率和 not-evaluated 指标。失败
   attempt 同样是可复用证据，必须保留。

## 发布门槛

- reviewer 确认公开任务忠实表达问题且没有泄漏。
- evaluator 在私有边界内验证 target/golden 来源和冻结 hash。
- 检索审计证明完整 leakage group 均不可见。
- dry run 在不暴露私有数值的前提下验证分类和产物完整性。
- 只有完整冻结状态稳定后，才能开始正式执行。

## 输出契约

返回以下内容：

1. 公开任务与允许使用的知识契约；
2. evaluator-only 资产清单及其所有权；
3. leakage group 与审计规则；
4. validator gates 与失败分类；
5. 完整 freeze manifest 的范围；
6. 不可变 attempt/report schema；
7. readiness verdict 或明确 blocker。

## 停止条件

拒绝将目标挂载到 Agent workspace、在 prompt 中提供 golden 数值、逐算例做局部冻结、
事后修改容差、删除失败记录或只报告成功 attempt。在编写本基准契约期间不得运行求解器。
