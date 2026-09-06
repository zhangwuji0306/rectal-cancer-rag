# 任务书 A5：正文获取与统一 Retry Client

## 目标

建立稳定、合规、可审计的正文获取层，降低 PDF-centric 依赖。

## 来源顺序

PMC AWS XML → Europe PMC XML → PMC AWS TXT → 合法 OA HTML/XML → 合法 OA PDF。

## 执行内容

按 corpus_raw/PMID_<pmid>/ 保存 source.json 和 article.xml、article.txt 或 article.pdf。source.json 至少包含 PMID、DOI、PMCID、source、URL、license、retrieved_at、sha256、format、version。

所有官方 API 共用 Unified HttpClient，统一处理 connect/read timeout、Retry-After、429、5xx、指数退避、jitter、最大尝试次数和结构化日志。

删除 network_retries、pdf_retries、pmc_retries 等分裂配置。

Crossref、Unpaywall、NCBI 参数只从 CROSSREF_MAILTO、UNPAYWALL_EMAIL、NCBI_API_KEY 读取；仓库只保留 .env.example。

## 验收

使用 mock 响应覆盖成功、429、5xx、timeout、Retry-After 和最大尝试次数；验证每次尝试都可审计且不泄露密钥。

完成后提交独立 commit，并暂停等待审查。

