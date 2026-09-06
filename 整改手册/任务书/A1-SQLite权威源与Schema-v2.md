# 任务书 A1：SQLite 权威源与 Schema v2

## 目标

使 tasks.sqlite 成为文献 metadata 和业务状态的唯一权威源。

## 执行内容

迁移并支持 PMID、DOI、PMCID、标题、作者、年份、期刊、ISSN、pub_type、language、oa_status、license、reuse_allowed、content 状态与路径、content_sha256、时间字段、attempt_count、错误分类、重试字段、worker lease、retracted 和 excluded_reason。

新增 fetch_attempts，记录每次 source 请求的 source、route、URL、identifier、时间、HTTP 状态、outcome、错误、retry 信息和内容属性。

新增 source_candidates，记录来源 URL、格式、版本、许可证、OA、复用许可、优先级和解析时间。

提供 schema_version、迁移脚本和 rollback strategy。

CSV 改为 SQLite 的导出物；常规流程禁止 CSV 反向覆盖 SQLite。

## 约束

迁移必须保留原 status、已有 DOI/PMCID、历史路径和 attempts。不得以 DROP + rebuild 作为生产默认升级方式。

## 验收

迁移前后 row count 相同；PMID 唯一；旧状态、DOI、PMCID 保留；fetch_attempts 和 source_candidates 存在；迁移和回滚均可演练。

完成后提交独立 commit，并暂停等待审查。

