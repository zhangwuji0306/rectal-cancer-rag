# 任务书 A2：Metadata UPSERT 与 PubMed Canonical Metadata

## 目标

以当前 PubMed XML 作为 canonical metadata，修复历史 NBIB 与当前元数据不一致。

## 执行内容

对全部 PMID 使用 EPost 或批量 EFetch 获取 PubMed XML，更新 PMID、DOI、PMCID、标题、作者、期刊、ISSN、出版日期、出版类型、语言和 Article IDs。

实现 non-empty metadata UPSERT：新值非空时更新旧值；新值为空时保留已有可信值，尤其不得清空 PMCID。

metadata refresh 不得重置 status、attempt_count、content_status 或 content_path。

实现 normalize_author_family()，兼容 Smith AB、Smith, Andrew B、Andrew B Smith；有结构化 family name 时优先使用。

## 验收

- 新 PMCID 能更新，status 与 attempts 不变。
- 新 PMCID 为空时，旧 PMCID 保留。
- DOI、标题、作者、期刊和日期字段的 UPSERT 有专项测试。

完成后提交独立 commit，并暂停等待审查。

