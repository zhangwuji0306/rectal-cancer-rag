# 任务书 A9：Reporting 与 Acquisition 总验收

## 目标

建立覆盖语料、来源、OA、失败原因和 RAG 准备度的统一报告，并验证 Phase A 入口条件。

## 报告内容

报告至少统计 Corpus target、Metadata ready、Fulltext ready、Metadata only、Retryable error、Archived、Excluded；DOI/PMCID 覆盖；PMC AWS、Europe PMC、Unpaywall 及其他来源成功数；timeout、rate_limit、server_error、invalid_content、metadata_mismatch；raw_fulltext、normalized_documents、conversion_failed。

## Acquisition Gate

进入 RAG 检索优化前必须确认：

- SQLite 是唯一权威源。
- fetch_attempts 与 source_candidates 已实际使用。
- 503 不进入 not_found，429 可重试。
- PMCID 可由 metadata refresh 补充。
- PMC AWS resolver、Europe PMC XML、JATS normalization 和 bibliographic validation 可用。
- 生产测试 fixture 已与真实语料隔离。

## 验收

报告数字可由 SQLite、文件和索引交叉复核；所有 Gate 条件有证据。完成后提交独立 commit，并暂停等待 Phase A 审查。

