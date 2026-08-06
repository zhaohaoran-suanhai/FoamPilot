---
name: openfoam-author-native-case
description: Use when an Agent must turn a public Foundation OpenFOAM v10 TaskSpec into native case dictionaries, direct typed commands, public run evidence, and bounded evidence-scoped repairs.
---

# 编写并验证原生 OpenFOAM 算例

## 核心原则

由 Agent 负责 CFD 选择和文件字节，由 runtime 负责命令策略，由 evaluator 负责私有真值。
必须从空算例开始，绝不能读取受保护的 tutorial、目标算例、golden result 或私有 validator。

## 生成执行计划

返回一个包含全部文件与命令的完整 `ExecutionPlan`：

1. 根据已声明的物理问题和已安装命令选择 application。
2. 按依赖顺序声明所有必需的原生文件。
3. 按阶段顺序声明直接命令：mesh、check、initialize、solve；并行时再声明 reconstruct。
   只有公开任务明确要求执行某个后处理命令时才添加它，不能仅因评测器需要派生量就添加。
   只能使用 argv，不得使用 shell、`Allrun`、重定向、命令替换或宿主机绝对路径。
   runtime 已将工作目录切换到 `/case`。不要添加 `-case case` 或任何其他 `-case` 参数。
   生成文件、依赖与 required-output 路径必须相对于该根目录（使用 `1/U`，而不是
   `case/1/U`）。Runner 负责 MPI launcher：将求解器写入 `executable`，设置
   `mpi_ranks`，绝不能生成 `mpirun` 或 `orterun`。
4. 所有步骤的 timeout 总和与 MPI ranks 必须处于 TaskSpec 预算内。命令 timeout 总和
   不得超过 `max_wall_seconds`；先为 mesh、initialize、decompose 和 reconstruct 预留
   时间，再将剩余预算分配给求解器。
5. 将每个 required output 映射到求解结束后 evaluator 可检查的 solver log 或写出字段。
   验证项应覆盖网格质量、正常结束、要求的最终时刻、有限字段以及相关守恒量或物理不变量。
   将 `finite_fields` 直接绑定到 solve step，由 validator 在该步骤日志中检查非有限值标记。
   不要为了证明有限性而虚构不可用的后处理 function。
6. 只能使用公开物理输入，不得编造缺失参数。

## 编写原生文件

- 将上下文中唯一的 selected solver guide 视为版本化 reader contract。先把其中明确写出的
  必需文件、字段、字典表、operator 和 base/Final 配对整理成原子清单，逐项映射到
  `files`/`commands` 后再输出 CaseBundle。不得只实现日志当前提到的一项。不得等 reader 逐项报错
  后再补同一组已知必需项。
- 为每个 OpenFOAM dictionary 提供有效的 `FoamFile` header。
- 保证 `system/controlDict` 中的 application 与执行计划一致。
- 每个字段都必须覆盖网格中的每个 patch。
- 对二维挤出网格，在受抑制方向只使用一个 cell，并在网格与字段中使用一致的 `empty` patch。
- 对多块网格，保证每个共享面在两个切向方向上的划分数相同，点位与 grading 兼容。为每类
  拓扑边的整数 cell 数定义具名变量，并在相邻 block 中复用，不能重复互不相关的整数字面量。
  对从低 z 向高 z 挤出的二维 hex，四个底面顶点在 x-y 平面按逆时针排列，四个顶面顶点
  保持同样映射；每个 block 都要验证手性，不能只检查第一个。OpenFOAM 字典替换不是算术：
  负坐标绝不能写成 `-$name`，应使用负数字面量，或单独定义负标量并替换完整 token。
  输出 `blockMeshDict` 前检查完整邻接图，包括局部 block 轴方向与 grading 方向；不能只修复
  一个被报告的 block pair，却留下其他不共形共享面。在流固界面上，界面两侧的切向划分
  必须相互匹配，且不能错误地与无关的上游、下游或径向 cell 数绑定。相邻 block 必须在共享
  面复用完全相同的 vertex label；同坐标却使用新 label 会生成断开的拓扑。Foundation v10
  `blockMesh` 不存在 `-merge-points` 修复选项，应修正 vertex/face 拓扑，不能虚构命令参数。
  每个外表面都必须归入具名 boundary patch。意外出现 `defaultFaces`，或 `checkMesh` 将网格
  判为一维，说明存在未声明外表面，不能把它当作可忽略的网格质量 warning。
- 几何发生旋转或弯曲时，必须变换完整局部坐标系与截面，包括 face normal、porous axis
  或 outlet axis。只移动中心线而让截面保持全局轴向，会改变建模面积和阻力。
- 约束 patch 类型要一致：mesh type 为 `symmetryPlane` 时，field type 也必须是
  `symmetryPlane`，不能写 `symmetry`。适当时可用
  `#includeEtc "caseDicts/setConstraintTypes"` 从网格推导 Foundation v10 字段约束。
- 对区域初始条件，在 mesh check 之后、solver 之前声明其 dictionary 和原生 initialize 命令。
- 将可选诊断排除在必需求解计划之外。不要只为制造 evaluator 证据而添加 sampling、extrema、
  conservation 或 convergence function object；写出字段和 solver log 已足够。
- 公开任务明确要求全时段日志证据时，使用动态检索到的、精确的 Foundation v10 诊断配方，
  并按要求频率记录。
- 普通输出时刻的测量应由 evaluator 检查写出字段，不要添加 function object。
- 在同一个 CaseBundle 中返回所有完整文件，并保证 patch、field 与 dictionary 依赖内部一致。

## 保持 VOF 有界性

当两流体 VOF 任务声明了公开相分数容差时：

1. 将 `maxCo` 和 `maxAlphaCo` 视为上限，而不是精度目标。遇到严格上限时，两者都应
   严格低于 TaskSpec 允许的最大值，并选择兼容的 `maxDeltaT`；照抄允许上限不会留下稳定裕量。
2. 显式声明 Foundation v10 alpha 控制，包括 `nAlphaCorr`、`nAlphaSubCycles`、
   `MULESCorr`，以及适用时的 limiter 设置。没有观测证据时，任何配置都不能证明有界性。
3. 求解成功后，由 evaluator 从写出字段计算 extrema 和 phase-volume history。
4. 如果完整、有限的求解违反界限，保持 mesh、physics、boundary 和 initialization 不变。
   先测试一个更小的 time-step/interface-Courant 参数族；若仍失败，再测试一个 alpha
   correction、sub-cycling 或 limiter 参数族。
5. 重跑完整时间区间，并同时要求有界性与守恒。绝不能通过放宽公开阈值制造通过结果。

## 评估与修复

只能运行通过安全校验的 typed plan。零退出码只能证明命令完成，不能证明物理正确。
按顺序执行公开检查，并在最早失败层停止。

若允许 repair，必须说明：证据、一个原因、一个最小安全的生成文件或已有 typed-command
修改、预期检查，以及一个保持不变的 control。当失败证明确实缺少必需 dictionary 时，
repair 可以新增一个安全的生成算例文件。保留失败 attempt。不要修复环境失败，不要重复
未变化的 fingerprint，不要修改 public asset，也不要捆绑互不相关的假设。

## 输出契约

返回 execution plan、生成文件 hash、static inspection、逐步骤日志、公开检查、存在时的
repair decision、限定范围的 status 和不可变 artifact 目录。`PUBLIC_VALIDATION_PASS`
绝不代表已经通过私有或正式 golden 检查。
