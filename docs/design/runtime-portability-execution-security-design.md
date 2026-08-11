# FoamPilot Runtime 可移植性与执行安全设计

状态：已冻结，待实施

日期：2026-08-11

适用范围：P0-A；Foundation OpenFOAM v10 本地工作站运行时、执行后端与安全降级

## 1. 目标

FoamPilot 必须能够在不同 Ubuntu 用户、Python 环境和 Foundation OpenFOAM v10 安装路径下，
通过同一份 wheel 完成配置、预检和规范求解。同时，不能把 bubblewrap 不可用等同于可以无条件
在宿主机执行模型生成的 OpenFOAM 内容。

本阶段形成以下单一运行时边界：

```text
CLI / environment / user TOML / bounded discovery
  -> ResolvedRuntimeConfig + config provenance
  -> environment discovery
  -> ExecutionRiskReport
  -> isolation policy decision
  -> bubblewrap or audited typed host
  -> immutable runtime and execution evidence
```

本阶段不改变 `TaskSpec -> NativeAgent.solve()` 状态机，不增加第二条求解路径。

## 2. 为什么保留双后端

typed command policy 可以限制 executable、argv、MPI 和资源预算，但不能限制 OpenFOAM 读取
字典后触发的全部行为。Foundation v10 支持 coded function object、`#codeStream`、动态代码、
include 和动态库；FoamPilot 的公开知识也允许在确有物理需求时生成 coded initializer。

因此：

- bubblewrap 是 live model-authored case 的文件系统和网络隔离边界；
- audited host 是受信任工作站、嵌套容器或 namespace 不可用时的兼容执行后端；
- 两者共享 typed command policy，但不能宣称具有相同安全性；
- host fallback 必须同时受 isolation policy 和内容风险检查控制。

完全删除 bubblewrap 会让模型生成的动态代码以当前用户权限运行。所有场景强制 bubblewrap 又会
使部分受限工作站、嵌套容器和 HPC 环境不可用。故本设计保留双后端，并显式区分三档安全策略。

## 3. 范围与非目标

### 3.1 本阶段包含

- 严格、可序列化的 Runtime 配置；
- CLI、环境变量、用户 TOML 与有限自动发现；
- 移除生产源码中的个人目录和固定 Python 路径；
- 动态 bubblewrap mount layout；
- 与真实 Runner 等价的 sandbox probe；
- 模型生成 OpenFOAM 内容的执行风险检查；
- 三档 isolation policy；
- preflight、run artifact 和 Desktop 状态中的有效后端证据；
- 当前内部 `execution_backend` 三值语义的一次性迁移。

### 3.2 本阶段不包含

- OpenFOAM 自动安装或编译；
- ESI OpenFOAM、其他 Foundation 版本或任意第三方 solver qualification；
- Podman、Docker、Slurm 或远程 worker；
- evaluator 私有资产拆包；
- Desktop 取消、detached worker 或 crash reconnect；
- CI、依赖锁、release tag、wheel/sdist 发布流程；
- 把静态风险扫描描述为完整恶意代码证明。

后四项分别属于后续 P0-B、P0-C 或 P1，不与 Runtime 迁移混合实施。

## 4. 配置模型

### 4.1 用户 TOML

规范用户配置如下：

```toml
schema_version = 1

[openfoam]
distribution = "foundation"
version = "10"
root = "/opt/OpenFOAM/OpenFOAM-10"

[execution]
isolation = "sandbox_preferred"
bubblewrap = "auto"
max_mpi_ranks = 4
allow_dynamic_code_on_host = false
trusted_readonly_roots = []
```

约束如下：

- `distribution` 只能是 `foundation`；
- `version` 在本阶段只能是字符串 `"10"`；
- `root` 可以省略，由 resolver 发现；
- `bubblewrap` 可以是 `"auto"` 或绝对 executable 路径；
- `isolation` 只能是 `sandbox_required`、`sandbox_preferred`、`trusted_host`；
- `allow_dynamic_code_on_host` 默认 `false`；
- `trusted_readonly_roots` 只能来自用户配置或 CLI，TaskSpec 和模型响应无权增加挂载；
- `trusted_readonly_roots` 不得是 `/`、用户 home 或其父目录；与任务 protected path、tutorial
  目标或 evaluator root 相交的条目在具体 run 中拒绝；
- 未知字段一律拒绝。

Runtime 配置不再保存 `python_executable`。实际解释器从 `sys.executable` 读取并作为诊断事实记录，
而不是可配置的求解依赖。`etc/bashrc` 从 OpenFOAM root 派生；tutorial root 从 source 后的
`FOAM_TUTORIALS` 读取，不在配置中重复保存。

### 4.2 配置来源与优先级

有效配置按叶子字段合并，优先级固定为：

```text
CLI 显式覆盖
> FOAMPILOT_* 环境变量
> 显式 --runtime-config TOML
> FOAMPILOT_RUNTIME_CONFIG 指向的 TOML
> ${XDG_CONFIG_HOME:-~/.config}/foampilot/runtime.toml
> bounded automatic discovery and defaults
```

支持的环境变量首期限定为：

- `FOAMPILOT_RUNTIME_CONFIG`；
- `FOAMPILOT_OPENFOAM_ROOT`；
- `FOAMPILOT_EXECUTION_ISOLATION`；
- `FOAMPILOT_BUBBLEWRAP`；
- `FOAMPILOT_MAX_MPI_RANKS`；
- `FOAMPILOT_ALLOW_DYNAMIC_CODE_ON_HOST`。

布尔值只接受明确的 `true/false`，整数和路径使用严格解析。空字符串不是有效覆盖。配置 provenance
记录每个有效字段的来源类别和源文件路径，但不记录环境变量的秘密值。

CLI 的 Runtime 参数由 `preflight`、`plan`、`solve`、`resume`、`inspect`、`qualify` 和 Desktop
子进程共享，不允许各命令自行构造默认 Runtime。

### 4.3 安全等级所有权

TaskSpec、TaskDraft、ExecutionPlan、模型响应、case 文件和工程本地文件都无权降低 isolation。
本阶段不自动读取项目目录中的 `.foampilot/runtime.toml`，避免不受信任工程把执行策略改成 host。

qualification 的有效策略固定为 `sandbox_required`。如果用户配置或 CLI 明确要求其他策略，
qualification 返回 `RUNTIME_POLICY_CONFLICT`，不静默覆盖也不运行。

## 5. Runtime resolver 与自动发现

新增一个纯解析边界：

```text
resolve_runtime_config(cli, environ, explicit_toml, user_toml)
  -> ResolvedRuntimeConfig
  -> RuntimeConfigProvenance
```

自动发现顺序为：

1. 显式 `openfoam.root`；
2. 当前进程环境中的 `WM_PROJECT_DIR`；
3. 由已知 OpenFOAM 环境命令可确定的 project root；
4. 有限、可测试的 Ubuntu/Foundation v10 候选路径。

不递归扫描整个 home 或文件系统。每个候选都必须存在 `etc/bashrc`。resolver 对候选 source
`etc/bashrc` 后验证：

- `WM_PROJECT`/发行版属于 Foundation；
- `WM_PROJECT_VERSION` 精确为 `10`；
- source 后的 `WM_PROJECT_DIR` 与候选 root 指向同一路径；
- `FOAM_APPBIN` 和至少一个基础 solver 可解析；
- `FOAM_TUTORIALS` 若存在，则解析为 tutorial root。

没有候选时返回 `OPENFOAM_DISCOVERY_FAILED`。多个有效候选且用户未显式选择时，同样停止并返回
候选摘要，不按路径顺序猜测。

默认只把 OpenFOAM root 内的 executable 视为可隔离执行的 OpenFOAM 命令。位于
`FOAM_USER_APPBIN`、第三方目录或 root 外部的 solver，只有其解析路径位于
`trusted_readonly_roots` 时才进入 capability snapshot 和 sandbox mount plan。

## 6. 三档 isolation policy

### 6.1 决策矩阵

| 策略 | bubblewrap 可用 | bubblewrap 不可用 | host 上存在高风险内容 |
| --- | --- | --- | --- |
| `sandbox_required` | bubblewrap | `SANDBOX_REQUIRED_UNAVAILABLE` | 不适用 |
| `sandbox_preferred` | bubblewrap | 低风险时 audited host | `HOST_DYNAMIC_CODE_BLOCKED` |
| `trusted_host` | 不选择 bubblewrap | audited host | 默认 `HOST_DYNAMIC_CODE_BLOCKED` |

`trusted_host` 只有在 `allow_dynamic_code_on_host=true` 时才允许高风险内容，并必须把显式 opt-in、
风险代码和未隔离警告写入 artifact。该开关不能来自 TaskSpec、模型或 Desktop 工程文件。

无用户配置时，普通 CLI solve 和 Desktop 使用 `sandbox_preferred`；qualification 使用
`sandbox_required`。

### 6.2 后端选择时点

初始 preflight 证明配置与 sandbox mechanism 是否可用。具体 run 在 materialize 和静态 inspection
完成后生成 `ExecutionRiskReport`，再做最终 backend decision。这样 `sandbox_preferred` 不能在尚未
看到生成内容时提前、无条件承诺 host fallback。

每次 repair 产生新文件后重新计算风险报告。不得沿用 parent attempt 的低风险结论。

具体 run 在第一条求解命令之前，使用实际 case 和实际 mount plan 再做一次 launch probe，并据此一次性
冻结 backend。`sandbox_preferred` 只有在尚未执行任何 step、实际 launch probe 失败且风险为 low 时
才能降级到 audited host。任何 step 开始后不得在同一 attempt 内切换 backend；此后的 sandbox
故障返回 `SANDBOX_SETUP_FAILED`，由正常 repair/resume 边界处理。

## 7. ExecutionRiskReport

风险扫描覆盖规范 plan 的全部生成文件及最终 materialized case 中由 Agent 控制的文本，先去除
注释，再识别至少以下构造：

- `#codeStream`；
- `#calc`、`systemCall` 与 `timeActivatedFileUpdate`；
- coded function object；
- coded boundary condition；
- `dynamicCode` 或等价动态编译入口；
- 解析后逃出 case root 的 `#include`、`#includeIfPresent`；
- 绝对 include；
- 带绝对路径或路径分隔符的动态库加载项；
- 宏展开的 include、type 和 `*Libs`，以及命令行 case/distributed-root 覆盖；
- 指向未授权 root 的外部文件引用。

相对 include 只有在规范化后仍位于 case root 才是低风险。`#includeEtc` 只有解析目标处于已验证的
OpenFOAM root 时才允许。普通内置 function object 不因名称本身被判为动态代码。

报告至少包含：

```text
schema_version
risk_level = low | high | unknown
findings[] = {code, path, line, detail}
scanned_file_sha256
policy_decision
```

无法可靠解析的执行相关 directive 记为 `unknown`。在 host fallback 决策中，`unknown` 与 `high`
同样阻断；在 bubblewrap 内保留为 advisory 和审计证据。

该扫描器的职责是控制已知 host 降级风险，不声称证明 OpenFOAM parser、第三方库或内核没有漏洞。

## 8. 动态 bubblewrap mount plan

sandbox builder 不再创建固定 `/home/edwin` 或 `/home/edwin/workplace`。它接收
`ResolvedRuntimeConfig`，为所有可信只读 root 动态创建必要父目录，并保持 OpenFOAM root 在
sandbox 内的绝对路径不变，使 `etc/bashrc`、platform bin 和 library 路径继续成立。

默认 mount plan 只包含：

- 运行所需的只读系统目录与动态链接器路径；
- 只读 OpenFOAM root；
- 经过配置验证的额外只读 root；
- 绑定为 `/case` 的唯一可写 attempt case；
- 独立 tmpfs `/tmp`；
- 隔离 HOME `/home/agent`；
- `/proc` 和最小 `/dev`；
- 关闭 network、PID、IPC 和 UTS namespace；
- `prlimit` CPU 与 address-space 限制。

只读挂载 OpenFOAM root 可能间接包含 `FOAM_TUTORIALS`。builder 必须在所有较宽只读 bind 完成后，
用空 tmpfs 覆盖解析出的 tutorial root，以及位于已挂载树下的任务 protected path 和 evaluator
root；如果目标无法被可靠遮蔽，则拒绝该 mount plan。额外 trusted root 也执行同样的相交检查与
遮蔽规则。

不得暴露用户 home、源码仓库、tutorial 目标或 evaluator data，除非其中的显式公开资产已经复制进
`/case`。sandbox 内仍使用固定模板 source bashrc 并 `exec` typed argv，不执行 Agent 编写的 shell。

## 9. Preflight、执行与证据流

### 9.1 等价 sandbox probe

preflight 创建临时空 case，使用与 Runner 相同的 mount-plan builder、namespace flags、环境清理、
资源限制和 bashrc source 路径执行一个无副作用命令。不得再使用比 Runner 更小的 namespace probe
代表完整可用性。

`sandbox_preferred` 下 probe 失败是非阻断事实，但 preflight 必须明确报告 host fallback 只有在
case 风险扫描为 low 时才可能发生。`sandbox_required` 下同一失败为阻断。

### 9.2 Run artifact

每个 run 固化：

- `runtime-config.json`：有效非秘密配置；
- `runtime-config-provenance.json`：字段来源；
- `sandbox-probe.json`：完整 probe；
- 每个 attempt 的 `execution-risk-report.json`；
- 每个 step 已有的 actual backend 与 fallback reason；
- `execution-policy.json`：最终策略、决策和显式 host opt-in。

这些文件进入 artifact manifest。Desktop 只读取这些规范产物显示安全状态，不自行推断。

### 9.3 稳定错误

- `RUNTIME_CONFIG_INVALID`：TOML、环境变量或 CLI 字段无效；
- `RUNTIME_POLICY_CONFLICT`：qualification 或调用方要求与安全策略冲突；
- `OPENFOAM_DISCOVERY_FAILED`：无唯一有效候选；
- `OPENFOAM_VERSION_MISMATCH`：不是 Foundation v10；
- `SANDBOX_REQUIRED_UNAVAILABLE`：required 策略的完整 probe 失败；
- `SANDBOX_SETUP_FAILED`：mount plan 或真实 sandbox launch 失败；
- `HOST_DYNAMIC_CODE_BLOCKED`：host 路线遇到 high/unknown 风险；
- `TRUSTED_RUNTIME_ROOT_INVALID`：额外 root 缺失、不可解析或违反路径约束。

这些状态属于 environment/execution policy，不得归因为模型 transport、case physics 或 solver
convergence。错误保留稳定英文 code、中文 message 和 recovery。

## 10. CLI、Python 与 Desktop 接口

所有 Runtime 相关命令共享以下 CLI 参数：

```text
--runtime-config PATH
--openfoam-root PATH
--execution-isolation sandbox_required|sandbox_preferred|trusted_host
--bubblewrap auto|ABSOLUTE_PATH
--max-mpi-ranks N
--allow-dynamic-code-on-host
```

危险 opt-in 必须是显式 flag 或受信任用户 TOML；Desktop v1 不自动打开它。Desktop 环境检查显示：

- 配置来源；
- OpenFOAM root/version；
- 请求与实际 isolation；
- sandbox probe；
- 当前 run 风险等级；
- host fallback 和未隔离警告。

Python API 提供严格的配置加载、resolve、preflight、risk scan 和 policy decision 函数。外部调用方
仍把最终配置传给同一个 `NativeAgent.solve()`，不得绕过 Runner。

## 11. 迁移策略

删除带个人路径的 `RuntimeConfig.local_foundation_v10()`。现有内部三值一次性映射为设计术语：

```text
bubblewrap -> sandbox_required
auto       -> sandbox_preferred
host       -> trusted_host
```

代码和测试在同一变更中迁移到新 schema，不长期维护第二套 Runtime authoring schema。历史 run
仍按已有 artifact 只读展示，不通过兼容 adapter 重新执行。

`python_executable` 从 Runtime schema 删除；preflight 记录 `sys.executable` 作为非阻断诊断事实。
`tutorial_root` 从有效 OpenFOAM 环境派生；如果 qualification 无法解析 tutorial root，则环境阻断，
普通 solve 只在其任务不需要该路径时继续。

## 12. 测试与验收

### 12.1 确定性测试

- TOML strict schema、环境变量类型和 CLI 覆盖；
- 每个字段的配置优先级与 provenance；
- 无候选、唯一候选、多个候选和版本/发行版不匹配；
- 不再有 `/home/edwin` 生产源码硬编码；
- `sys.executable` 与 `shutil.which("bwrap")` 事实；
- dynamic parent directories 和 trusted root mount plan；
- 完整 probe 与 Runner 使用同一个 builder；
- 三档 policy、bubblewrap 成败和 low/high/unknown 风险的决策矩阵；
- coded、codeStream、absolute/external include、path-bearing library 的风险 fixture；
- repair 后重新扫描；
- qualification 非 required 配置被拒绝；
- artifact、manifest 和 Desktop 安全状态投影；
- 核心 CLI 在未安装 Qt 时仍可导入。

### 12.2 真实 gate

1. 当前工作站使用 `trusted_host` 跑一个最小非 tutorial Foundation v10 case；
2. 普通宿主环境使用 `sandbox_required` 跑同一 case；
3. `sandbox_preferred` 在 bubblewrap 不可用时让 low-risk case 走 host；
4. 同一条件下让 coded fixture 在执行前得到 `HOST_DYNAMIC_CODE_BLOCKED`；
5. wheel 安装目录中的 `preflight` 和 `solve` 不依赖源码树或个人路径；
6. 一份显式 TOML 在第二个 Ubuntu 用户/路径环境中完成 preflight；
7. 后续 P0-B 的干净机器 gate 再执行自然语言、Desktop 和正式 release 验收。

确定性测试通过不替代真实 OpenFOAM gate；host case 成功不替代 sandbox gate；public validation
不替代 qualification。

## 13. 完成标准

P0-A 只有同时满足以下条件才完成：

- 生产源码没有个人目录和固定虚拟环境路径；
- CLI、Python、Desktop 与 qualification 使用同一个 Runtime resolver；
- preflight 和 Runner 不再使用不同强度的 bubblewrap probe；
- host fallback 不能执行未显式授权的 high/unknown 风险内容；
- qualification 不能降级到 host；
- 有效配置、来源、风险和实际后端进入不可变证据；
- 完整测试、wheel gate、trusted-host 真实 gate 和 bubblewrap 真实 gate 均通过；
- 文档明确区分 typed host 与 sandbox isolation，不把 fallback 描述成等价安全执行。

完成 P0-A 后，按既定顺序进入 P0-B 发布与跨机验收，再进入 P0-C evaluator 隔离。
