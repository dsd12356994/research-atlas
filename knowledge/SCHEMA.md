# Research Atlas 知识库约定

采用 Karpathy LLM Wiki 的 raw / wiki / schema 工作方式，原始说明：
https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

## 三层与所有权

- `raw/` 是内容哈希命名的不可变快照。论文快照包含元数据及已有阅读卡，是派生材料，不冒充原始论文。真正下载的文件继续保存在项目 `data/originals/`，提取清单在 `data/fulltext/`。用户材料也以独立快照保存。
- `wiki/` 是可阅读的 Markdown：sources、concepts、questions、syntheses、answers、notes。文件以 JSON 值兼容的 YAML frontmatter 记录 id、type、status、sources、raw 等；使用 `[[页面id|显示文字]]` 互链。模型整理的内容仍是待复核草稿。
- 本文件定义知识维护约定。操作入口为 `python -m hub.wiki`。每日采集结束时自动进行确定性 Wiki 整理；不会因页面刷新而调用模型。

## 必须保持的边界

1. 不改写 raw，不删除历史证据来掩盖修正。新的内容保存为新的快照。
2. 论文阅读覆盖率、方法适用条件、作者陈述与审阅者推测必须保留。metadata、已归档原文、部分阅读、全文可提取文本处理完毕是不同状态。
3. 概念关联由已配置关键词和明确链接产生；不能自动赋予支持、反对、因果、新颖性或已复现等关系。
4. 综合笔记和问答的每个判断引用真实输入页面。程序检查引用身份，不能证明推理正确。模型生成的 answers/syntheses 不作为下一轮问答的独立事实来源，防止错误循环自证。
5. 自动编译检测到人工修改时保留文件、登记待协调项，不静默覆盖。人工整理时先阅读 index，再修改所需页面，维护链接和状态，最后运行 lint。
6. 索引 `wiki/index.md` 每次整理更新；`wiki/log.md` 只追加，记录新增材料、综合与问答。
7. 新材料更新后，旧综合/问答记录输入页哈希；lint 报告 stale_input。陈旧不自动等于错误，也不自动重写。

## 操作

- `python -m hub.wiki build`：从现有阅读卡整理 Wiki，更新索引与图谱，不调用模型。
- `python -m hub.wiki synthesize "Federated retrieval"`：基于有限已有阅读卡生成有引用的综合草稿，调用已有模型服务一次。
- `python -m hub.wiki ask "研究问题" --keywords "federated retrieval"`：检索 Wiki 摘录，回答并保存到 answers，一次模型调用。不是全网检索或整库全文综述。
- `python -m hub.wiki ingest path.md --title "笔记标题"`：保存用户 Markdown/txt 材料，不自动验证其事实。
- `python -m hub.wiki lint`：检查断链、孤立页面、raw 哈希与陈旧输入。不宣称自动理解所有语义矛盾。

已有阅读模型的每天五次预算不因本次升级而改变。用户主动问答/综合另外计量；每日 Wiki 编译本身不花模型额度。
