# 任务书 B3：Passage Representation v2

## 目标

让 embedding 同时利用文献标题、章节路径和 chunk 内容，同时保留可展示的原始正文。

## 执行内容

embedding 输入为 paper title + section path + chunk text。数据库展示正文仍保存原 chunk text。

metadata 增加 paper_id、section_type、embedding_text_version；section_type 至少覆盖 abstract、introduction、methods、results、discussion、conclusion、references、supplement、other。

执行完整 rebuild，并报告 before、after、delta。

## 验收

检查索引 chunk 数、paper 数、缺失字段、重复情况和 B2 指标变化；指标下降必须如实保留，不得只汇报提升项。

完成后提交独立 commit，并暂停等待审查。

