# FoamPilot 内容来源说明

本文档说明仓库中不同类型内容的来源边界。发布 gate 由
`tools/audit_source_provenance.py` 执行，并由测试验证；审计工具不会读取凭据文件，
也不会把被比较仓库的正文写入报告。

## 版权与许可

FoamPilot 中由本项目独立创作的实现、测试、合成回放资产和主要说明文本版权归
Haoran Zhao 所有，并按仓库中的 MIT License 授权。Foam-Agent 的上游 MIT 声明继续
保留，用于满足授权条件和记录历史来源；该声明不改变 FoamPilot 独立创作内容的版权
归属。具体声明范围见 [NOTICE.md](NOTICE.md) 和
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## FoamPilot 原创代码与文本

`src/foampilot`、`tools`、`tests` 与主要项目文档由 FoamPilot 项目独立编写。
仓库不嵌入模型服务的私有 OAuth 协议，也不读取其他工具的私有认证文件。模型访问
通过固定参数的公开 CLI 后端或标准 OpenAI-compatible HTTP 后端完成。

发布前可以用 `--compare-root` 将候选仓库与历史参考仓库进行规范化长行和
12-token shingle 比较。审计结果只包含路径、计数、hash 和短 fingerprint；发现未解释
的高相似内容时发布失败。

## FoamPilot 合成资产

`tests/fixtures/artifact-replay` 中的 replay 资产由
`tools/generate_synthetic_replay.py` 确定性生成。每个条目声明
`source_kind: synthetic_foampilot`，索引记录生成器和逐文件 SHA256。这些资产不是
OpenFOAM tutorial、历史运行目录或其他仓库产物的快照。

## 带来源的事实总结

`src/foampilot/knowledge/openfoam10` 保存独立表述的工程事实总结。每条知识记录都包含
来源标题、相对 locator、来源 SHA256 和 SPDX 标识。OpenFOAM solver、字段、字典和
keyword 等标准标识会按其真实名称保留，但说明、规则、故障信号和验证方法由本项目
重新组织和表述。

locator 指向单个文件时，`source.sha256` 是该文件原始字节的 SHA256。locator 指向
目录或用分号分隔的 source set 时，先递归收集文件并按相对于 `OpenFOAM-10` 的路径
排序，再依次写入路径长度、路径字节、内容长度和内容字节，最后计算 SHA256。这样既
能复核多文件事实来源，又不会在 FoamPilot 中保存上游正文。

这些知识条目引用 OpenFOAM Foundation v10 源码或经过审查的 FoamPilot 工程规则，
并明确标注适用范围。事实摘要不等于复制或打包被引用源码；目标官方算例也不会作为
Agent 可见答案随 replay fixture 分发。

## 外部运行时依赖

OpenFOAM、MPI、bubblewrap、Codex CLI、Python 解释器和 Python 依赖是运行时或安装时
依赖，不属于 FoamPilot 源码。它们各自适用其上游许可证和安装方式。FoamPilot 只
调用公开命令或标准网络接口，不将这些程序的源码或二进制文件打包进项目 wheel。

具体发布字节边界见 `THIRD_PARTY_NOTICES.md`。
