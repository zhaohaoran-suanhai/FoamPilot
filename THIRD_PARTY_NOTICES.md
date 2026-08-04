# 第三方内容声明

当前 FoamPilot 源码分发包中没有已知的第三方源码、官方 tutorial、模型凭据、历史
求解产物或第三方二进制文件。

## Foam-Agent 历史参考

FoamPilot 的开发曾研究公开的
[Foam-Agent](https://github.com/csml-rpi/Foam-Agent)。该项目以 MIT License 发布，
上游声明为 `Copyright (c) 2025 Ling Yue`。本仓库在 [LICENSE](LICENSE) 中保留完整
上游声明和 MIT 授权正文，以满足授权条件并记录历史来源。

当前来源审计未发现 FoamPilot 分发包中存在未说明的 Foam-Agent 源码文本重用。保留
上游声明不表示 FoamPilot 打包或运行 Foam-Agent，也不表示双方存在从属或背书关系。

## OpenFOAM 与其他外部运行时

本仓库不打包 OpenFOAM 源码。OpenFOAM Foundation v10 是 GPL-3.0-or-later 许可的
外部运行时与知识事实来源，用户需要独立安装。知识库中的 solver、字段、字典和
keyword 名称用于描述兼容接口；对应来源及 hash 记录在每条知识 YAML 中。

Codex CLI、其他 OpenAI-compatible 模型服务、MPI 和 bubblewrap 同样是可选的外部
运行时。Python 包依赖由安装工具单独解析和安装，不作为本仓库维护的源码副本提交。

若未来实际加入第三方字节，必须在本文件列出内容路径、来源、版本、许可证和必要
通知，并同步扩展来源审计测试。
