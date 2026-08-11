# Changelog

FoamPilot 的重要变更记录在此文件中。版本号遵循 Semantic Versioning；在 `1.0.0` 之前，
次版本可能调整仍在稳定过程中的接口。

## [Unreleased]

### Added

- 增加统一核心活动事件、模型/OpenFOAM 心跳、日志增长和增量残差事件。
- 增加工程内持久化本机 job、detached worker、进程身份校验、心跳、取消请求和 Desktop 重连。
- 增加确定性 job reconcile、身份安全的孤儿进程终止、`INTERRUPTED` 中立固化，以及
  `job reconcile`/`job recover-finalize` 命令。
- 增加跨 job 的严格模型阶段 resume、完整 `rerun` 与不可变 `lineage.json`；Desktop 只按
  恢复证据启用对应操作，并明确不支持通用 OpenFOAM 时间步 continuation。

### Changed

- Desktop 长任务不再由窗口进程持有；关闭窗口后继续运行，重新打开工程可恢复观察并显式取消。
- 活跃 run 改为增量读取 workflow/log，节流文件扫描、缓存 manifest 验证，并在后台线程构建
  projection。
- artifact manifest 改为原子、排他写入；被中断的 recover-finalize 可幂等完成，不会把中断
  错报为 solver failure 或成功。
- strict resume 现在强制执行跨 lineage 的累计 OpenFOAM wall budget，并把 runtime/isolation、
  OpenFOAM executable identity 与 bubblewrap identity 纳入兼容性指纹。
- job 状态持久化改为控制面关键写入；Desktop 孤儿终止、终态轮询和无 partial run 恢复动作按
  最终 reconcile 证据收紧。

## [0.2.0] - 2026-08-11

### Added

- 增加 PySide6 Desktop IDE，可在同一界面完成自然语言任务提取、TaskDraft 审阅、TaskSpec
  编译和规范 `foampilot solve`。
- 增加求解期间的 workflow、公开 Knowledge/Skill 引用、OpenFOAM 日志和残差曲线实时展示，
  并保留完成后的 case、报告和 artifact manifest 检查能力。
- 增加可序列化 Runtime 配置，以及 CLI、TOML、环境变量和有限自动发现的统一解析路径。
- 增加 `sandbox_required`、`sandbox_preferred` 和 `trusted_host` 三档执行策略，以及每次 run
  的 runtime、probe、风险扫描和 policy 决策证据。

### Changed

- 移除固定用户目录、固定 OpenFOAM 根目录和固定 Python executable 假设。
- OpenFOAM 命令、MPI launcher 和 Runner 上下文改为基于已发现规范路径的 typed execution。
- package version 改为从 `foampilot.__version__` 单点读取，避免源码版本与 wheel 元数据漂移。

### Security

- host fallback 在执行前扫描动态代码、`#calc`、`systemCall`、动态库、外部 include、变量展开、
  `timeActivatedFileUpdate` 和 Runner 上下文改写，并对未知风险 fail closed。
- `.foampilot` 保留为 Runner 内部命名空间，TaskSpec、公开资产和模型生成文件不得写入。
- `sandbox_required` 不允许静默退化为 host；qualification 固定要求 sandbox。

### Verification boundary

- 本版本在开发工作站上完成自动测试、Desktop offscreen 测试、wheel/sdist 构建、全新虚拟环境
  wheel 安装冒烟检查，以及受信任 host 的 Foundation OpenFOAM v10 最小真实门禁。
- 当前 Codex 执行环境无法通过 bubblewrap namespace probe，因此没有将该环境中的
  `sandbox_required` 失败描述为 sandbox 实机通过。
- 尚无第二台干净 Ubuntu + Foundation OpenFOAM v10 机器，跨机安装、preflight、自然语言求解
  和 Desktop 实机门禁均为未验证；本版本不是跨机器产品交付证明。
- 当前 wheel 仍包含仓库中现有 Knowledge、Skills 和 qualification/evaluator 数据。本版本只作为
  内部可追溯基线；在公开分发前，必须先完成资产分类和公开/私有包边界拆分。

[0.2.0]: https://github.com/zhaohaoran-suanhai/FoamPilot/releases/tag/v0.2.0
