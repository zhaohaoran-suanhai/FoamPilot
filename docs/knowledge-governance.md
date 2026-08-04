# 知识治理

公开 Foundation v10 corpus 包含聚焦的方法与契约，不包含完整 tutorial solution。每个
YAML 条目都带有 ID、适用边界、source hash 与 license、leakage metadata 和验证指导。
`src/foampilot/knowledge/knowledge-manifest.json` 冻结每个条目的字节。

Retrieval 在相关性评分前应用 fork、version、solver、knowledge type、visibility 与
family filter。只要条目的 `leakage.families` 包含当前评测 family，该条目就不可用。

正式 retrieval 排除 `development_only` knowledge，除非协议为开发阶段显式 allowlist
当前 family。任何实验派生条目都必须标记为 `development_only` 并列出 leakage family。
这是 qualification 机制，不代表可以向其他 family 暴露评测目标。

## 语言与检索契约

知识条目采用稳定的双语边界：

- `title`、`applicability`、`summary`、`rules` 与 `validation` 使用中文说明；
- `id`、`tags`、`activation_terms`、`solvers`、`models`、enum、source identity 和
  OpenFOAM 原生 token 保持英文；
- `failure_signals` 中用于匹配原生日志的文本保持原始英文；
- 中文化不得改变来源 hash、leakage 语义或检索 metadata。

当前 lexical retrieval 以 ASCII token 进行稳定匹配，因此不能把 `tags` 或
`activation_terms` 翻译为中文。修改任何知识条目后都必须重建并验证 knowledge manifest。

开发证据 promotion 为公开知识需要：

1. 提取与具体 solver 无关或广泛适用的经验；
2. 尽可能用官方或独立审查来源替换目标专用证据；
3. 确认条目不包含 case file、source path、golden value 或目标专用参数集；
4. 记录来源、SHA256 与再分发 license；
5. 重新运行 corpus、leakage、retrieval 与 manifest 测试；
6. 正式评测前创建新的 protocol freeze。

Benchmark-private source mapping、validator、golden result 与官方 baseline observation
绝不属于该 corpus。
