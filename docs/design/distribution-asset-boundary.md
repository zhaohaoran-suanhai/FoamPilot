# FoamPilot 公开与私有资产分发边界

日期：2026-08-11

## 决策

FoamPilot 的代码可安装性与资产可公开性是两个独立问题。`v0.2.0` 先形成可追溯的内部发布
基线，不在本次发布中拆分 evaluator；在任何公开 wheel 发布前，必须对 Knowledge、Skills、
qualification task、validation contract 和 reference value 逐项分类。

## 目标边界

公开 Agent wheel 只应包含允许交付给使用者和模型运行时访问的内容：

- Agent、TaskBuilder、Runtime、Desktop 和公开 validation 代码；
- 明确批准公开的 Knowledge 与 Skills；
- 公开 schema、示例和文档。

私有资产包或受信任 evaluator 工作目录保存不应随公开 wheel 分发的内容：

- 私有 Knowledge 与 Skills；
- holdout task、suite 编排和目标 case 映射；
- evaluator-only validation contract、golden/reference value 和容差；
- 仅用于离线评分、对比或改进分析的实现和数据。

“私有”指分发边界，不等于运行时提示词可见性。即使 Agent prompt 没有引用某份文件，只要它被
装入公开 wheel，安装者仍可读取，因此不能把同包但未注入 prompt 当作真正保密。

## `v0.2.0` 现状

当前 `pyproject.toml` 仍把以下目录作为 `foampilot` package data：

- `knowledge/knowledge-manifest.json` 与 `knowledge/openfoam10/**/*.yaml`；
- `skills/*.yaml`、`skills/**/*.md` 与 `skills/**/*.yaml`；
- `qualification/data/**/*.yaml` 与 `qualification/data/**/*.json`。

因此 `v0.2.0` wheel 是内部制品，不是完成私有资产隔离后的公共制品。创建 Git tag 和本地构建
不会自动授权上传 wheel、发布 GitHub Release 或公开这些资产。

## 后续拆分原则

1. 建立显式资产清单，每项标记 `public` 或 `private`，默认未分类资产不得进入公开包。
2. 公共 wheel 的构建门禁检查文件清单，并拒绝 evaluator/reference/私有 Knowledge/Skill 泄漏。
3. 私有资产通过独立包、插件或受信任目录注入；公开 Agent API 不依赖其必然存在。
4. qualification 在 evaluator 不可用时给出结构化缺失错误，不回退到公开规则冒充严格评分。
5. sandbox、模型上下文和 artifact 导出继续遮蔽私有根目录；分包不能替代运行时访问控制。
