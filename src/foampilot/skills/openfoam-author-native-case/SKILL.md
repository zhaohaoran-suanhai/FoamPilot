---
name: openfoam-author-native-case
description: Use when a Case Author must implement a frozen Foundation OpenFOAM v10 CaseDesign as one command-free native CaseBundle, or propose a bounded evidence-scoped numerical repair.
---

# 编写并验证原生 OpenFOAM 算例

## 核心原则

由 CaseDesigner 负责 CFD 选择，由 Case Author 负责文件字节，由 PlanCompiler 负责命令，
由 evaluator 负责私有真值。
必须从空算例开始，绝不能读取受保护的 tutorial、目标算例、golden result 或私有 validator。

## 生成 CaseBundle

严格实现已经冻结的 CaseDesign，返回一个不含命令的完整 `CaseBundle`：

1. 使用 CaseDesign 已冻结的 solver/application，不得重新选择求解器、物理模型或网格路线。
2. 按依赖顺序声明所有必需的原生文件。
3. `CaseBundle` 只能包含 `manifest` 和 `files`。不得返回 command、argv、step、stage、timeout、
   MPI launcher、shell、`Allrun`、重定向、命令替换或宿主机绝对路径。PlanCompiler 与 Runner
   会根据冻结设计和资源预算生成并执行命令。
4. 所有生成文件与 required-output 路径必须相对于 case 根目录（使用 `1/U`，而不是
   `case/1/U`），且不得写入 `.foampilot` 或公开资产安装路径。
5. 对每个 required output，确保控制字典的写出设置和 manifest field 足以让后续验证读取
   solver log 或写出字段。不要为了 evaluator 私有需求虚构 function object。
6. 只能使用冻结设计、权威网格事实和公开上下文，不得编造缺失参数或重新解释已确认值。

## 编写原生文件

- 将上下文中唯一的 selected solver guide 视为版本化 reader contract。先把其中明确写出的
  必需文件、字段、字典表、operator 和 base/Final 配对整理成原子清单，逐项映射到
  `files` 后再输出 CaseBundle。不得只实现日志当前提到的一项。不得等 reader 逐项报错
  后再补同一组已知必需项。
- 为每个 OpenFOAM dictionary 提供有效的 `FoamFile` header。
- 保证 `system/controlDict` 中的 application 与冻结设计和 CaseManifest 一致。
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
- 对区域初始条件，提供系统初始化 contributor 所需的字典；PlanCompiler 负责在 mesh check
  之后、solver 之前安排原生 initialize 命令。
- 将可选诊断排除在必需 CaseBundle 之外。不要只为制造 evaluator 证据而添加 sampling、extrema、
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

零退出码只能证明命令完成，不能证明物理正确。公开检查由系统按顺序执行，并在最早失败层停止。

若允许 repair，只能对已分类的数值不稳定提出 `RepairProposal`：说明证据、一个原因、冻结
NumericalRepairEnvelope 内的设计字段变化、对应完整文件替换和预期检查。不得修改或建议命令，
不得改变物理、solver、mesh、边界或 envelope 外字段。保留失败 attempt；不要修复环境失败，
不要重复未变化的 fingerprint，不要修改 public asset，也不要捆绑互不相关的假设。

## 输出契约

Case Author 只返回 CaseBundle；repair 模型只返回 RepairProposal。ExecutionPlan v4、文件 hash、
static inspection、逐步骤日志、公开检查、repair authorization、status 和不可变 artifact 均由
系统生成。`PUBLIC_VALIDATION_PASS` 绝不代表已经通过私有或正式 golden 检查。
