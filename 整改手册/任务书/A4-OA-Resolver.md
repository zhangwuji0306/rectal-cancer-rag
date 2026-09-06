# 任务书 A4：OA Resolver

## 目标

把正文获取从“下载 PDF”改为查找可信、合法、可审计的全文来源。

## 来源优先级

1. PMC AWS Article Dataset
2. Europe PMC fullTextXML
3. Unpaywall OA location
4. Publisher 或 institutional repository
5. metadata only

## 执行内容

有 PMCID 时记录 article_version、license、XML/TXT/PDF URL 和 updated_at；不得把 PMCID 自动等同于可再利用。

优先解析 Europe PMC fullTextXML。Unpaywall 保存 is_oa、oa_status、best_oa_location、URL、PDF URL、host_type、version 和 license。

所有候选先写入 source_candidates，不立即大规模下载。

## 验收

第一轮仅做 dry-run，并输出 PMCID 数、PMC AWS 可解析数、Europe PMC XML 可解析数、Unpaywall 可解析数及无 OA 候选数。

完成后提交独立 commit，并暂停等待审查。

