# 任务书 A6：内容验证与 Bibliographic Match

## 目标

确认文件既是可解析正文，也是目标文献，而不是仅凭扩展名或标题相似度接收入库。

## 执行内容

XML 必须解析成功、存在 article-title、正文达到最低长度，并在可用时匹配 PMCID/PMID。

PDF 必须通过 parser 打开、有页数、可提取文本，并检查标题相似度、DOI、PMID、PMCID。

保存独立的 file_valid 与 bibliographic_match 状态。只有两者都为 true 才能标记 fulltext_ready。

匹配优先级：PMID/PMCID exact > DOI exact > title + author + year。不得只依赖 title similarity。

## 验收

补充合法 PDF、截断 PDF、错误文献 PDF、正确和错误 XML 的 fixture，验证身份不匹配不能进入 fulltext_ready。

完成后提交独立 commit，并暂停等待审查。

