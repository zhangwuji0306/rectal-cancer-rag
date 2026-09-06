# Rectal Cancer RAG 综合整改与升级手册
## 文献获取、语料治理、索引、检索、排序、评估与生成一体化整改方案

**适用项目：** `E:\writing-rag`(本地)/`zhangwuji0306/rectal-cancer-rag`  (github)
**适用范围：** 整个直肠癌科研 RAG 项目  
**执行模式：** 严格串联、阶段冻结、独立审查、批准后继续  
**目标系统：** 可追溯、可复现、可量化评估、面向科研文献检索与证据生成的 RAG 系统

---

# 一、当前项目状态

当前项目已经具备：

```text
PubMed / NBIB
    ↓
索引信息.csv
    ↓
tasks.sqlite
    ↓
03_downloader.py
    ↓
PDF
    ↓
MinerU / Markdown
    ↓
BGE-M3
    ↓
Chroma
    ↓
Dense Retrieval
```

已有较好的基础包括：

```text
SQLite 任务队列
PDF 基础校验
断点续跑
批次管理
Markdown 转换
BGE-M3 embedding
Chroma 持久化
heading-aware chunking
SHA256 去重
增量索引
DOI/source 去重
Recall/MRR/nDCG 评估代码
manifest reconciliation
基础 regression tests
```

但项目目前同时存在两个层面的主要问题。

第一类是**上游语料获取与治理问题**：

```text
SQLite 尚未成为真正唯一权威源
not_found / failed 语义混乱
缺少 fetch_attempts
缺少 source_candidates
没有 PMC AWS 主链
没有 Unpaywall
没有 JATS XML 主链
仍然 PDF-centric
PDF bibliographic validation 不完整
缺少 lease/heartbeat
reporting 仍基于旧状态机
```

第二类是**下游 RAG 检索质量问题**：

```text
manifest 与 Chroma 缺乏强一致性门禁
rag_core cache 可能跨 DB / model 污染
没有正式 Gold Standard
仍为 dense-only retrieval
文献级覆盖不足
没有 BM25 / sparse hybrid
没有 semantic reranker
缺少 document-level index
没有统一 paper registry
没有文献质量 prior
没有 citation graph
生成层尚未完整实现
```

因此本次整改必须遵守：

> **Corpus correctness before retrieval optimization.**

即：

> **先保证语料正确，再优化检索。**

---

# 二、整改总体原则

整个项目整改按照四大阶段群执行：

```text
Phase A
文献获取与语料治理
        ↓
Phase B
索引与检索基础设施
        ↓
Phase C
高级排序与证据质量
        ↓
Phase D
生成、端到端评估与工程治理
```

严格顺序：

```text
Stage N
    ↓
执行
    ↓
测试
    ↓
生成阶段报告
    ↓
Git commit
    ↓
STOP
    ↓
外部独立智能体审查
    ↓
APPROVED
    ↓
Stage N+1
```

禁止：

```text
Stage 2 尚未审查
→ 顺便开始 Stage 3
```

禁止：

```text
因为下一阶段需要接口
→ 提前实现下一阶段功能
```

禁止执行智能体自行批准自己的修改。

---

# 三、角色划分

## 3.1 执行智能体

执行智能体负责：

```text
读取当前 Stage 任务
↓
检查 BASE_COMMIT
↓
只修改本 Stage 允许范围
↓
补充测试
↓
运行测试
↓
生成执行报告
↓
提交 stage commit
↓
停止
```

执行智能体不得：

```text
批准自己的 Stage
修改下一阶段功能
删除无法解释的数据
修改 Gold Standard 使结果更好
隐藏性能下降
```

---

## 3.2 外部独立审查智能体

外部智能体必须：

```text
直接查看 Git diff
直接查看代码
直接查看测试
直接查看 benchmark
直接检查产物
```

不得仅依据执行智能体的总结。

允许的最终决定只有：

```text
APPROVED

APPROVED WITH NON-BLOCKING NOTES

NEEDS REVISION

REJECTED
```

只有：

```text
APPROVED
APPROVED WITH NON-BLOCKING NOTES
```

允许进入下一阶段。

---

# 四、阶段冻结协议

每个 Stage 开始：

```text
BASE_COMMIT=<当前 commit>
STAGE=<N>
START_TIME=<timestamp>
```

结束：

```text
END_COMMIT=<stage commit>
```

独立审查范围：

```text
BASE_COMMIT..END_COMMIT
```

如果需要返工：

```text
END_COMMIT
   ↓
revision commit
   ↓
REVIEW_COMMIT
   ↓
重新审查
```

审查期间：

> **禁止继续开发。**

---

# 五、总体目标架构

最终系统应演进为：

```text
PubMed / External Metadata
          │
          ▼
Canonical Metadata Layer
          │
          ▼
       tasks.sqlite
   （唯一权威状态源）
          │
      ┌───┴───────────────┐
      ▼                   ▼
source_candidates      fetch_attempts
      │                   │
      └────────┬──────────┘
               ▼
       Fulltext Resolver
               │
   ┌───────────┼──────────────┐
   ▼           ▼              ▼
PMC AWS XML  EuropePMC XML  OA PDF
   │           │              │
   └───────────┼──────────────┘
               ▼
        Content Validation
               │
               ▼
       Normalized Corpus
        Markdown / JSON
               │
               ▼
          Paper Registry
               │
       ┌───────┴─────────┐
       ▼                 ▼
Document Index       Passage Index
       │                 │
       ├──── Dense ──────┤
       └──── Sparse ─────┘
               │
               ▼
          Hybrid Retrieval
               │
               ▼
             RRF
               │
               ▼
        Semantic Reranker
               │
               ▼
     Literature-level Ranking
               │
      ┌────────┼─────────┐
      ▼        ▼         ▼
 relevance   quality   citation graph
               │
               ▼
        Coverage Selection
               │
               ▼
          Evidence Pack
               │
               ▼
          LLM Generation
               │
               ▼
       Claim ↔ Evidence ↔ DOI
```

---

# 六、PHASE A —— 文献获取与语料治理

---

# Stage A0 —— 当前状态冻结与完整备份

## 目标

在开始文献获取子系统重构前建立可恢复基线。

## 必须统计

```text
total PMID
status distribution
DOI coverage
PMCID coverage
PDF count
valid PDF count
converted Markdown count
indexed documents
Chroma chunks
```

检查：

```text
tasks.sqlite
索引信息.csv
reports/run_history.csv
pdfs_merged/
converted/
index/manifest.json
```

## 备份

建立：

```text
backups/YYYYMMDD-HHMMSS/
```

至少包含：

```text
tasks.sqlite
索引信息.csv
run_history.csv
config.json
manifest.json
```

并记录：

```text
SHA256
file size
row count
```

SQLite 必须实际：

```text
open
PRAGMA integrity_check
```

成功。

## 交付

```text
docs/baseline/acquisition-baseline.md
docs/baseline/acquisition-baseline.json
docs/reviews/stage-A0-report.md
```

## 禁止

不得：

```text
修改 downloader
修改 schema
重新下载文献
修改 status
```

## 验收

必须确认：

```text
PMID unique
数据库可打开
backup 可恢复
基线统计完整
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A1 —— SQLite 权威源与数据库 Schema v2

## 目标

把：

```text
tasks.sqlite
```

真正变为：

> 文献 metadata 和业务状态唯一权威源。

## 新增或迁移字段

tasks 至少逐步支持：

```text
pmid
doi
pmcid

title
title_norm
year
journal
issn

authors
first_author
first_author_family

pub_type
language

oa_status
license
reuse_allowed

content_status
content_source
content_format
content_path
source_url

content_sha256

metadata_updated_at
source_updated_at
retrieved_at

attempt_count

status
last_error_class
last_error_detail
next_retry_at

worker_id
lease_until
heartbeat_at

retracted
excluded_reason

created_at
updated_at
```

## 新增表

### fetch_attempts

```text
attempt_id
pmid
source
route
url
identifier

started_at
finished_at

http_status
outcome

error_class
error_detail

retryable
retry_after

content_type
content_length
```

### source_candidates

```text
pmid
source
url
format
version
license
is_oa
reuse_allowed
priority
resolved_at
```

## Migration原则

必须：

```text
保留原 status
保留已有 DOI
保留 PMCID
保留历史路径
保留 attempts
```

不得：

```text
DROP + rebuild production DB
```

作为默认升级方式。

必须提供：

```text
migration
rollback strategy
schema_version
```

## CSV关系

以后：

```text
API
↓
SQLite
↓
CSV export
```

禁止：

```text
SQLite
↓
CSV
↓
常规情况下反向覆盖 SQLite
```

## 验收

必须通过：

```text
migration before/after row count 相同
PMID unique
旧状态保留
旧 DOI/PMCID 保留
fetch_attempts exists
source_candidates exists
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A2 —— Metadata UPSERT 与 PubMed Canonical Metadata

## 目标

解决：

```text
历史 NBIB
≠
当前 PubMed metadata
```

的问题。

## PubMed metadata refresh

输入：

```text
所有 PMID
```

通过：

```text
EPost / batched EFetch
↓
PubMed XML
```

更新：

```text
PMID
DOI
PMCID
Title
Authors
Journal
ISSN
Publication date
Publication type
Language
Article IDs
```

## 数据原则

```text
NBIB
=
historical search snapshot
```

而：

```text
PubMed XML
=
canonical current metadata
```

## UPSERT规则

必须：

```text
non-empty new metadata
→ update old metadata
```

但：

```text
new NULL
≠
delete existing trusted identifier
```

尤其禁止：

```text
新的 PMCID 为空
→ 把旧 PMCID 清空
```

业务状态：

```text
status
attempt_count
content_status
content_path
```

不得被 metadata refresh 重置。

## DOI 作者归一化

同时实现：

```text
normalize_author_family()
```

能够正确处理：

```text
Smith AB
Smith, Andrew B
Andrew B Smith
```

全部归一化：

```text
smith
```

结构化 API 有 family name 时优先使用结构化字段。

## 验收

专项测试：

```text
existing PMID + new PMCID
→ PMCID updated
→ status unchanged
→ attempts unchanged
```

以及：

```text
new PMCID empty
+
old PMCID exists
→ old PMCID preserved
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A3 —— 状态机与错误分类重构

## 目标

废除：

```text
最后一个异常
→ 决定整篇文章状态
```

的错误模型。

## 新状态

推荐：

```text
pending
metadata_ready
oa_resolved
fetching

fulltext_ready
metadata_only

retryable_error

excluded
archived
```

## Error taxonomy

固定枚举：

```text
timeout
connection_error

rate_limit
server_error

source_not_found
no_oa_fulltext
license_restricted

invalid_pdf
invalid_xml
content_too_small

metadata_mismatch
identifier_mismatch

parser_error
storage_error

unknown
```

## HTTP映射

```text
429
→ rate_limit
→ retryable

500/502/503/504
→ server_error
→ retryable

timeout
→ timeout
→ retryable

404
→ source_not_found
→ source-level failure

403
→ access / license classification
```

不得：

```text
503 → not_found
```

不得：

```text
一个 source 404
→ 整篇文章不存在
```

## fetch_attempts

每一次：

```text
source request
```

必须写入：

```text
fetch_attempts
```

包括失败 attempt。

## 文献级 status

根据全部 source attempt：

```text
综合推导
```

而不是：

```python
isinstance(last_exc, NotFoundError)
```

## 验收

必须存在测试：

```text
test_503_is_retryable
test_429_uses_retry_policy
test_404_is_source_level_not_found
test_timeout_is_retryable
test_fetch_attempt_written_on_failure
test_fetch_attempt_written_on_success
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A4 —— OA Resolver：PMC AWS / Europe PMC / Unpaywall

## 目标

把“下载 PDF”转变为：

> **查找可信合法全文。**

## Source优先级

```text
1 PMC AWS Article Dataset
2 Europe PMC fullTextXML
3 Unpaywall OA location
4 Publisher / institutional repository
5 metadata only
```

## PMC AWS

有 PMCID 时记录：

```text
article_version
license
xml_url
text_url
pdf_url
updated_at
```

不得：

```text
有 PMCID
=
自动允许再利用
```

必须保存 article-level license。

## Europe PMC

优先：

```text
fullTextXML
```

不是 PDF render。

## Unpaywall

保存：

```text
is_oa
oa_status
best_oa_location
url
url_for_pdf
host_type
version
license
```

## source_candidates

每个候选写入：

```text
source_candidates
```

而不是立即下载。

## 第一轮只做 dry-run

本 Stage 默认：

```text
resolve only
```

暂不大规模下载。

## 输出指标

```text
PMCID count
PMC AWS resolvable
EuropePMC XML resolvable
Unpaywall resolvable
No OA candidate
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A5 —— 正文获取与统一 Retry Client

## 目标

建立稳定、合规、可审计正文获取层。

## Source顺序

```text
PMC AWS XML
↓
Europe PMC XML
↓
PMC AWS TXT
↓
合法 OA HTML/XML
↓
合法 OA PDF
```

PDF 不再是唯一正文形式。

## 文件组织

```text
corpus_raw/
└── PMID_<pmid>/
    ├── source.json
    └── article.xml / article.txt / article.pdf
```

source.json：

```text
pmid
doi
pmcid
source
url
license
retrieved_at
sha256
format
version
```

## Unified HttpClient

所有官方 API 共用：

```text
connect timeout
read timeout
Retry-After
429 handling
5xx retry
exponential backoff
jitter
maximum attempts
structured logging
```

配置中禁止继续存在：

```text
network_retries
pdf_retries
pmc_retries
```

各自行为不一致的情况。

## Crossref / Unpaywall身份参数

通过环境变量：

```text
CROSSREF_MAILTO
UNPAYWALL_EMAIL
NCBI_API_KEY
```

仓库只允许：

```text
.env.example
```

不得硬编码真实邮箱或 key。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A6 —— 内容验证与 Bibliographic Match

## 目标

解决：

```text
文件是 PDF
≠
文件就是目标文献
```

的问题。

## XML验证

```text
XML parse success
article-title exists
body length sufficient
PMCID/PMID match where available
```

## PDF验证

必须增加：

```text
PDF parser opens
page_count > 0
text extraction works

title similarity
DOI match
PMID match
PMCID match
```

保存独立状态：

```text
file_valid
bibliographic_match
```

只有：

```text
file_valid = true
AND
bibliographic_match = true
```

才允许：

```text
content_status = fulltext_ready
```

## 防误配优先级

```text
PMID/PMCID exact
>
DOI exact
>
title + author + year
```

不得只根据：

```text
title similarity
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A7 —— JATS XML / TXT / PDF 统一 Normalization

## 目标

建立真正的 normalized corpus。

## JATS parser

至少识别：

```text
Title
Abstract
Introduction
Methods
Results
Discussion
Conclusion

Tables
Table captions

Figures
Figure captions

Supplementary information
References
```

## 输出

```text
normalized/PMID_<pmid>.md
```

推荐 front matter：

```yaml
---
pmid:
doi:
pmcid:
title:
year:
journal:
source:
license:
content_sha256:
---
```

正文保持：

```markdown
# Title

## Abstract

...

## Introduction

...
```

PDF 仍可继续：

```text
PDF
→ MinerU
→ normalized Markdown
```

最终：

```text
XML ─────┐
TXT ─────┼→ normalized Markdown → RAG
PDF ─────┘
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A8 —— Lease、Heartbeat 与 Batch 系统

## 目标

解决多 worker 重复领取和多个 batch SQLite 膨胀。

## Lease

新增：

```text
worker_id
lease_until
heartbeat_at
```

领取：

```text
pending
↓
transactional UPDATE
↓
fetching
↓
lease_until = now + N
```

worker 定时：

```text
heartbeat_at
lease_until
```

只有：

```text
lease_until < now
```

才允许其他 worker 回收。

禁止继续：

```text
updated_at 超过15分钟
→ 自动 pending
```

## Batch

逐步停止：

```text
tasks_pmc_b1.sqlite
tasks_pmc_b2.sqlite
...
```

推荐：

```text
tasks
+
batches
+
batch_members
```

或直接 query-based batch。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage A9 —— Reporting 与 Acquisition P0/P1 总验收

## 新报告至少回答

### Corpus

```text
Corpus target
Metadata ready
Fulltext ready
Metadata only
Retryable error
Archived
Excluded
```

### Identifier

```text
with DOI
with PMCID
without DOI
without PMCID
```

### OA

```text
PMC AWS available
EuropePMC available
Unpaywall available
no OA fulltext
license restricted
```

### Sources

```text
PMC_AWS_XML success
EuropePMC_XML success
Unpaywall_PDF success
Publisher success
Repository success
```

### Failure

```text
timeout
rate_limit
server_error
invalid_content
metadata_mismatch
```

### RAG preparation

```text
raw_fulltext
normalized_documents
conversion_failed
```

## Acquisition Gate

进入 RAG 检索优化前必须至少满足：

```text
SQLite 单一权威源
fetch_attempts 已使用
source_candidates 已使用
503 不会进入 not_found
429 可以 retry
PMCID 可以 metadata refresh
PMC AWS resolver 可用
EuropePMC XML 可用
JATS normalization 可用
bibliographic validation 可用
生产测试 fixture 已隔离
```

## 完成后

> **STOP FOR PHASE-A INDEPENDENT REVIEW**

只有 Phase A 总审查批准后，才能开始 Phase B。

---

# 七、PHASE B —— RAG 索引与检索基础设施

---

# Stage B0 —— RAG Baseline 冻结

## 目标

在改变 retrieval 之前建立固定 baseline。

记录：

```text
normalized document count
unique content count
indexed papers
chunks
duplicate groups
missing DOI
missing PMID
missing title
missing year

embedding model
model revision
chunk size
overlap
Chroma version
```

运行：

```text
tests
reconcile manifest
current retrieval evaluation
```

若正式 qrels 不存在：

```text
NO_VALID_GOLD_SET
```

不得使用两三个 query 宣称完成 benchmark。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage B1 —— Index Integrity Preflight

## 目标

修复：

```text
manifest 说存在
≠
Chroma 实际存在
```

风险。

实现：

```text
index_preflight()
```

检查：

```text
DB directory exists
collection exists
collection count
manifest exists
manifest total_chunks
source count
model fingerprint
```

任何不一致：

```text
raise IndexIntegrityError
```

禁止静默建新库。

必须测试：

```text
manifest exists
+
chroma_db deleted
```

此时：

```text
build_index
```

必须失败。

## rag_core cache

缓存按：

```text
(model_path, device)
(db_path, collection)
```

分键。

或者：

```text
RAGRetriever
```

实例化。

必须测试：

```text
DB A
DB B
```

不会复用错误 collection。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage B2 —— 正式 Gold Retrieval Benchmark

## 目标

建立后续所有检索改进的统一评估基础。

## 规模

```text
最低 100 queries
推荐 150–300
```

## 分层

至少：

```text
MRI staging
EMVI / CRM
mucinous phenotype
neoadjuvant therapy
TNT / CRT
pCR / TRG
DFS / OS / recurrence
surgery
pathology
CEA / MSI
radiomics
deep learning
guidelines
trial names
exact terminology
broad review questions
```

Query类型：

```text
short
long natural language
exact keyword
abbreviation
synonym
```

## Graded relevance

```text
3 = directly answers
2 = strongly relevant
1 = partially relevant
0 = irrelevant
```

## Dev / Test

```text
70% development
30% locked test
```

test set 冻结以后不得用于反复调参。

## 指标

```text
Recall@5
Recall@10
Recall@20
Recall@50

MRR@10

nDCG@10
nDCG@20

UniqueRelevantPaperRecall
```

对于科研检索优先：

```text
Recall
>
nDCG
>
MRR
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage B3 —— Passage Representation v2

## 目标

让 embedding 不再只有裸正文。

embedding 输入：

```text
paper title
+
section path
+
chunk text
```

但数据库展示正文仍保存原：

```text
chunk text
```

metadata 增加：

```text
paper_id
section_type
embedding_text_version
```

section_type：

```text
abstract
introduction
methods
results
discussion
conclusion
references
supplement
other
```

必须完整 rebuild 并比较：

```text
before
after
delta
```

不得只汇报变好的指标。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage B4 —— Hybrid Retrieval

## 架构

```text
Query
 │
 ├── BGE-M3 Dense
 │
 └── BM25 / Sparse
        │
        ▼
       RRF
        │
        ▼
Candidate Pool
```

实现：

```text
retrieve_dense()
retrieve_sparse()
fuse_rrf()
```

必须支持：

```text
dense-only
sparse-only
hybrid
```

## 验收

至少比较：

```text
Dense
BM25
Hybrid
```

重点：

```text
Recall@20
Recall@50
nDCG@10
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage B5 —— Document-Level Index 与文献覆盖

## 目标

解决：

```text
一个论文几十个 chunk
占满 top-k
```

的问题。

## Passage聚合

增加：

```text
max_chunks_per_paper
target_unique_papers
```

推荐初始：

```text
max_chunks_per_paper = 3
```

动态扩大 candidate pool：

```text
while unique_papers < target
    retrieve_more
```

## Document index

每篇文献建立 card：

```text
title
abstract
keywords
publication type
year
journal
```

候选：

```text
passage candidates
UNION
document candidates
```

## 新指标

```text
UniquePaperRecall@20
UniquePaperRecall@50
mean_chunks_per_paper
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage B6 —— Semantic Reranker

## 架构

```text
Hybrid retrieval
↓
100–200 candidates
↓
semantic reranker
↓
20–50 papers
```

推荐：

```text
cross-encoder reranker
```

Reranker只能：

```text
rerank
```

不得取代第一阶段 recall retrieval。

## 重点指标

```text
nDCG@10
MRR@10
Recall@20
```

要求：

```text
nDCG/MRR 提升
Recall 不显著下降
```

## 完成后

> **STOP FOR PHASE-B INDEPENDENT REVIEW**

---

# 八、PHASE C —— Paper Registry、文献质量与高级排序

---

# Stage C1 —— Paper Registry

## 目标

从：

```text
filename-centric
```

改为：

```text
paper-centric
```

永久 ID：

```text
DOI
↓
PMID
↓
PMCID
↓
normalized title hash
```

schema：

```text
paper_id
doi
pmid
pmcid
title
authors
year
journal
publication_type
license
source
version
is_supplement
duplicate_of
content_sha256
metadata_updated_at
```

重复类型：

```text
exact duplicate
near duplicate
alternate version
supplement
```

不得仅依赖：

```text
SHA identical
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage C2 —— 文献质量 Metadata

## 目标

引入：

```text
近五年期刊指标
外部被引数
```

但本 Stage：

> **只存 metadata，不参与排序。**

建议：

```text
journal_metric
journal_metric_year
journal_percentile

external_citation_count
citation_source
citation_updated_at
citation_rate
```

影响因子等指标建议转化：

```text
field-normalized percentile
```

而不是直接使用 raw IF。

缺失 IF：

```text
missing
```

不能等同：

```text
0
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage C3 —— Corpus Citation Graph

## 目标

从 References 构建：

```text
Paper A
→ cites →
Paper B
```

匹配优先级：

```text
DOI
PMID
title fuzzy match
```

模糊匹配必须保存：

```text
confidence
```

低 confidence：

```text
不建立 edge
```

计算：

```text
in_degree
out_degree
PageRank
```

保存：

```text
citation_graph_version
```

注意：

```text
citation graph
≠
relevance
```

不能让老论文因为高引用自动压过新论文。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage C4 —— Evidence-Aware Ranking

## 最终排序原则

Quality只对：

```text
已经进入 relevance candidate pool
```

的文献进行 rerank。

绝不能在第一阶段：

```text
按 IF 过滤
```

## 初始透明基线

可以实验：

```text
70% relevance
10% journal quality
10% external citations
10% corpus citations
```

但这不是最终固定值。

必须做：

```text
relevance only
+ journal
+ external citations
+ corpus citations
+ publication type
full model
```

ablation。

## 人工检查

抽取：

```text
30–50 queries
```

检查：

```text
高IF但无关文章是否上浮
新文章是否被系统性压制
高相关低引用文章是否被误降
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage C5 —— Query Expansion / Decomposition

复杂问题自动拆分，例如：

```text
mucinous rectal cancer MRI prognosis after neoadjuvant therapy
```

拆成：

```text
mucinous rectal cancer MRI
mucin pool imaging
neoadjuvant response
DFS OS recurrence
```

要求：

```text
保留原 query
限制 max_subqueries
union
dedupe
rerank
```

重点指标：

```text
Relevant-paper coverage
Recall@50
```

## 完成后

> **STOP FOR PHASE-C INDEPENDENT REVIEW**

---

# 九、PHASE D —— Generation、E2E Evaluation 与工程治理

---

# Stage D1 —— Evidence Pack

生成模型不能直接接收杂乱：

```text
top-k chunks
```

先形成：

```text
Evidence Pack
```

每条：

```text
paper_id
title
DOI
PMID
section
passage
semantic score
quality metadata
```

同一 paper 的多个 passage 应先整合。

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage D2 —— Answer Generation 与 Citation Grounding

内部 answer schema：

```text
claim
evidence_ids
confidence
```

重要医学结论：

```text
必须绑定 evidence
```

模型不得引用：

```text
未检索到的文献
```

如果：

```text
evidence insufficient
```

必须：

```text
abstain / explicitly state uncertainty
```

不得用参数模型自身知识补成“文献结论”。

## 冲突证据

如果文献不一致：

```text
不要强行合并
```

应展示：

```text
Evidence supporting A
Evidence supporting B
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage D3 —— End-to-End Evaluation

至少评估：

```text
retrieval recall
citation correctness
citation completeness
claim support
unsupported claim rate
hallucination
answer completeness
conflict handling
```

至少：

```text
50–100 questions
```

人工核验。

核心指标：

```text
invalid citation rate
unsupported claim rate
missing citation rate
```

不能只评价：

```text
语言是否流畅
```

## 完成后

> **STOP FOR INDEPENDENT REVIEW**

---

# Stage D4 —— CI、Branch Protection 与发布治理

建立：

```text
GitHub Actions
```

流水线：

```text
unit tests
↓
integration tests
↓
schema validation
↓
index integrity
↓
retrieval regression
↓
secret scan
```

随后启用：

```text
main branch protection
required status checks
```

## Repository cleanup

整理：

```text
README.md
```

至少包括：

```text
Purpose
Architecture
Quick Start
Corpus Policy
Retrieval
Evaluation
Compliance
Known limitations
```

逐步移除或隔离：

```text
_altcha_*
_probe_*
debug scripts
runtime SQLite
temporary reports
driver binaries
machine-specific artifacts
```

但任何删除都必须：

```text
先形成清理清单
↓
独立审查
↓
再删除
```

## 合规

公开仓库不得包含：

```text
PDF corpus
converted article full text
Chroma DB
private backup
API keys
personal credentials
```

legacy 非 OA provider 不得作为生产默认路径。

## 完成后

> **STOP FOR FINAL INDEPENDENT REVIEW**

---

# 十、每个 Stage 的统一执行报告模板

路径：

```text
docs/reviews/stage-<stage>-execution-report.md
```

必须包含：

## 1. Stage Information

```text
Stage:
Base commit:
End commit:
Date:
Executor:
```

## 2. Scope

列出：

```text
Allowed changes
Forbidden changes
```

## 3. Files Changed

```text
file
change
reason
behavior impact
```

## 4. Database Changes

若适用：

```text
schema before
schema after
migration
rollback
```

## 5. Tests

必须列具体命令：

```text
command
exit status
result
```

不得仅：

```text
Tests passed
```

## 6. Metrics

任何涉及 retrieval / acquisition 的 Stage：

```text
before
after
delta
```

## 7. Known Issues

必须主动写：

```text
unresolved issues
limitations
```

## 8. Out of Scope

列出：

```text
intentionally not addressed
```

防止 scope drift。

## 9. Handoff

报告最后固定写：

```text
STATUS: PAUSED FOR INDEPENDENT REVIEW

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED
```

---

# 十一、独立审查统一模板

审查报告标题：

```text
STAGE <N> INDEPENDENT REVIEW
```

检查：

| 项目 | 结果 |
|---|---|
| 是否符合 Stage scope | PASS/FAIL |
| 是否存在越权修改 | PASS/FAIL |
| 核心任务是否完成 | PASS/FAIL |
| Migration 是否安全 | PASS/FAIL/N/A |
| 测试是否充分 | PASS/FAIL |
| 是否有数据损坏风险 | PASS/FAIL |
| 是否有回归 | PASS/FAIL |
| benchmark 是否可信 | PASS/FAIL/N/A |
| 文档是否完整 | PASS/FAIL |
| 是否可以进入下一阶段 | YES/NO |

必须区分：

```text
BLOCKING FINDINGS

NON-BLOCKING FINDINGS
```

最终：

```text
DECISION:
```

只能是四种之一。

---

# 十二、审查失败后的处理

如果：

```text
NEEDS REVISION
```

只能：

```text
修当前 Stage
↓
补测试
↓
更新 report
↓
commit
↓
STOP
↓
重新审查
```

不得：

```text
一边修 Stage N
一边开发 Stage N+1
```

---

# 十三、回滚规则

所有 Stage：

```text
独立 commit
```

任何出现：

```text
corpus corruption
wrong metadata merge
wrong document matching
index corruption
benchmark collapse
unexpected deletion
```

优先：

```text
git revert
```

数据库：

```text
restore tested backup
```

不要：

```text
继续打补丁掩盖错误
```

---

# 十四、Gold Standard 防污染规定

严格禁止：

```text
为了让指标提高修改 relevant label
```

禁止：

```text
在 test set 上反复调权
```

禁止：

```text
看到 test failure 后删除 query
```

禁止：

```text
LLM 自动生成 gold label
→ 不人工审核
→ 直接当真值
```

建议：

```text
dev
用于优化
```

```text
test
冻结
```

---

# 十五、文献质量指标使用原则

禁止：

```text
IF 高
=
一定更相关
```

禁止：

```text
引用多
=
一定更可靠
```

排序必须始终：

```text
Relevance first
Quality second
```

即：

```text
candidate retrieval
↓
relevance reranking
↓
quality prior
```

而不是：

```text
quality filtering
↓
retrieval
```

---

# 十六、性能优化延后

下列工作不能成为前期主要目标：

```text
GPU tuning
embedding batch size
HNSW tuning
parallelism
cache tuning
latency tuning
```

除非性能已经阻塞当前 Stage。

优先原则：

```text
Correctness
↓
Evaluation
↓
Retrieval quality
↓
Generation quality
↓
Performance
```

---

# 十七、推荐实际执行顺序

建议第一轮只执行：

```text
A0
基线 + backup
↓
A1
SQLite/schema
↓
A2
metadata refresh/upsert
↓
A3
状态机/fetch_attempts
↓
A4
PMC AWS + EuropePMC + Unpaywall resolver
↓
A5
fetch + retry
↓
A6
bibliographic validation
↓
A7
JATS normalization
↓
A8
lease/batch
↓
A9
Acquisition 总验收
```

完成 Phase A 后进行：

> **一次大型独立架构审查。**

只有 Phase A APPROVED，才开始：

```text
B0
↓
B1
↓
B2
↓
B3
↓
B4
↓
B5
↓
B6
```

到 B6 再进行第二次大型审查。

只有此时证明：

```text
corpus稳定
+
benchmark可信
+
retrieval recall提升
+
ranking提升
```

才建议投资：

```text
C1–C5
```

最后才：

```text
D1–D4
```

---

# 十八、当前仓库对应的优先级判断

依据当前代码状态，近期不建议直接开发：

```text
IF weighting
citation graph
LLM generation
```

因为当前最大的基础缺口仍然是：

```text
P0
SQLite权威状态
fetch_attempts
error taxonomy
PMC AWS
JATS全文链
bibliographic validation
```

紧接着才是：

```text
P0/P1 Retrieval
index integrity
gold benchmark
hybrid retrieval
document coverage
reranker
```

因此正确顺序不是：

```text
直接优化 RAG
```

而是：

```text
先完成 Acquisition v2
↓
冻结 corpus
↓
建立 retrieval benchmark
↓
再优化 RAG
```

---

# 十九、整个项目最终验收标准

整改完成后系统必须能够回答以下问题。

## 文献获取层

```text
这篇文献的 canonical metadata 来自哪里？
```

```text
这篇文献有没有合法自动可获取全文？
```

```text
尝试过哪些 source？
每次失败原因是什么？
```

```text
为什么它现在是 metadata_only？
```

```text
全文是否与目标 PMID/DOI 匹配？
```

---

## Corpus层

```text
当前有多少 unique papers？
```

```text
哪些是 duplicate/version/supplement？
```

```text
每篇正文的 source、license、SHA 是什么？
```

---

## Retrieval层

```text
Recall@20 是多少？
```

```text
Hybrid 比 Dense 提高多少？
```

```text
reranker 是否真正提高 nDCG？
```

```text
每个 query 能召回多少不同的相关论文？
```

---

## Ranking层

```text
IF/citation 到底有没有提升结果？
```

```text
会不会系统性压制新文献？
```

```text
哪些权重是经过 benchmark 决定的？
```

---

## Generation层

```text
每个 claim 的证据是什么？
```

```text
引用的 DOI 是否真实来自检索结果？
```

```text
证据不足时系统是否拒绝过度推断？
```

```text
存在冲突证据时是否能够保留争议？
```

---

# 二十、整改完成的定义

本项目不能因为：

```text
功能变多
```

就认为整改完成。

真正完成必须同时满足：

```text
语料来源可追溯

SQLite 状态唯一可信

source-level fetch 可审计

错误分类结构化

全文身份可验证

XML/PDF均可规范化入库

索引损坏可自动发现

正式 Gold Standard 已冻结

检索 Recall 可量化

Hybrid / reranker 有实证收益

paper identity 稳定

文献质量不会覆盖 relevance

Citation graph 可审计

生成 claim 可以追溯到 evidence

benchmark 不被污染

所有重要修改有测试

每个 Stage 都经过独立审查

任何 Stage 可以安全回滚
```

---

# 二十一、总执行原则

整个项目遵循四句话：

> **Metadata before content.**

> **Content correctness before retrieval optimization.**

> **Evaluation before complexity.**

> **Independent review before progression.**

对应中文即：

> **先把文献身份弄清楚，再获取正文；先保证正文正确，再优化检索；先能量化评价，再增加复杂度；每个阶段必须经独立审查后才能继续。**

---

# 二十二、总控状态机

总控智能体应始终维护：

```text
CURRENT_PHASE
CURRENT_STAGE
BASE_COMMIT
END_COMMIT
REVIEW_STATUS
BLOCKING_FINDINGS
NEXT_ALLOWED_STAGE
```

正常状态：

```text
EXECUTING
↓
PAUSED_FOR_REVIEW
↓
APPROVED
↓
NEXT_STAGE
```

失败状态：

```text
EXECUTING
↓
PAUSED_FOR_REVIEW
↓
NEEDS_REVISION
↓
REVISING_CURRENT_STAGE
↓
PAUSED_FOR_REVIEW
```

任何时候：

```text
REVIEW_STATUS != APPROVED
```

则：

```text
NEXT_ALLOWED_STAGE = NONE
```

这是整个整改任务的最高执行约束。
