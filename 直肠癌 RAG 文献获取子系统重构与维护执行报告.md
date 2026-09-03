# 直肠癌 RAG 文献获取子系统重构与维护执行报告

**适用项目：** `writing-rag(本地); rectal-cancer-rag(github仓库)`\
**适用目录：** `直肠癌文献爬取/`\
**适用对象：** 项目维护智能体、开发人员\
**核心目标：** 将当前“以 PDF 下载成功为中心”的文献爬取系统，重构为“以可信元数据、合法全文可用性和可追溯 RAG 语料为中心”的稳定文献获取流水线。

---

# 1. 执行摘要

当前项目已经形成以下基本流水线：

```text
PubMed NBIB
    ↓
索引信息.csv
    ↓
tasks.sqlite
    ↓
03_downloader.py
    ↓
pdfs_merged/PMID_*.pdf
    ↓
PDF → Markdown
    ↓
Chroma RAG
```

现有工程具备断点续跑、SQLite 状态管理、PDF 校验、批次管理、报告生成以及 RAG 下游同步等基础能力，因此**不建议推倒重建整个项目**。

需要重点重构的是：

```text
“元数据维护 → OA 全文定位 → 全文获取 → 状态判定”
```

这一中间层。

当前系统的核心问题不是爬虫能力不足，而是：

1. 将“是否拿到 PDF”近似等同于“是否存在全文”；
2. 将大量暂时性网络错误错误归类为 `not_found`；
3. 过度依赖不稳定的网页镜像、验证码与 Selenium fallback；
4. 没有充分利用 PMC 官方结构化全文数据；
5. SQLite、CSV 等多个文件同时承担“权威元数据源”角色；
6. DOI/PMCID 后续更新不能可靠同步到既有任务；
7. source-level fetch failure 没有独立记录；
8. PDF 校验只验证文件格式，没有充分验证“是不是目标文章”。

项目当前记录 9171 篇 PubMed 文献，其中 2000 年及以后进入主要获取范围；现有报告记录 `done=2419`，而 PDF 主目录存在 2422 个 PDF，其中 3 个为测试占位文件，因此生产数据和测试 fixture 已发生混杂。

此外，PMC 已在 2026 年 8 月调整 Article Dataset 分发架构。官方现在提供新的 AWS `pmc-oa-opendata` 数据集，包含 XML、TXT、JSON，以及存在时的 PDF，并支持匿名访问。旧 PMC OA Web Service/旧数据分发方式正在退出，因此项目应优先适配新架构。

---

# 2. 重构总原则

维护智能体以后执行文献获取任务时，必须遵循以下原则。

## 2.1 RAG 的成功定义

旧定义：

```text
PDF 下载成功 → done
```

新定义：

```text
获得可信、可追溯、许可条件明确、能被 RAG 使用的正文
→ fulltext_ready
```

全文可以是：

```text
JATS XML
TXT
HTML structured text
PDF
```

对 RAG 而言，优先级原则上应为：

```text
结构化 XML
    >
结构化纯文本
    >
可信 PDF
    >
仅摘要和元数据
```

不应强制所有文章必须经过：

```text
PDF → MinerU → Markdown
```

PMC 文献优先直接：

```text
JATS XML
→ section-aware parser
→ Markdown/JSON
→ RAG
```

Europe PMC 官方 REST 接口同样支持 OA 文献的 `/fullTextXML`。

---

## 2.2 官方、开放、稳定来源优先

新全文定位顺序：

```text
1. PMC AWS Article Dataset
2. Europe PMC fullTextXML / 官方全文
3. Unpaywall OA location
4. 出版商或机构知识库的合法 OA 地址
5. metadata + abstract only
```

不得将验证码绕过、反爬机制对抗或第三方镜像稳定性继续作为项目核心开发方向。

现有 Sci-Hub、Altcha、Selenium 相关代码在重构期间保留以便历史审计，但应逐步降级为 legacy，不作为新的生产主链。

---

## 2.3 SQLite 为唯一权威状态源

必须明确：

```text
tasks.sqlite
=
文献当前状态和规范元数据的唯一权威数据库
```

以后：

```text
索引信息.csv
download_report.csv
语料元数据.csv
pmc_batches.csv
```

全部视为：

```text
SQLite 的导出物
```

而不能再反过来成为长期权威数据源。

---

# 3. 当前主要问题及必须采取的措施

## 3.1 文献级状态被最后一个下载通道覆盖

当前 `03_downloader.py` 依次尝试多个 route，但最终状态主要由最后一次异常决定。

例如：

```text
Europe PMC → HTTP 503
Sci-Hub DOI → 无结果
Sci-Hub title → NotFound

最终：
not_found
```

这实际上无法说明文章不存在，只能说明“本轮所有通道未获得全文”。

现有代码最终执行：

```python
status = 'not_found' if isinstance(last_exc, NotfoundError) else 'failed'
```

因此 source-level 结果被压缩成单一文献级状态。

### 必须修改

新增独立表：

```text
fetch_attempts
```

建议字段：

```text
id
pmid
source
source_identifier
url

started_at
finished_at

http_status
outcome
error_class
error_detail

retryable
retry_after
worker_id
```

每次访问一个来源均写一条记录。

文献最终状态由所有 attempt 综合推导，不再依赖“最后异常”。

---

# 4. 建议的新状态机

废除含义过于模糊的：

```text
not_found
failed
```

作为主要业务状态。

建议文献状态：

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

其中：

### `pending`

尚未进行元数据规范化。

### `metadata_ready`

PMID、DOI、PMCID、题名、年份等规范元数据已经确认。

### `oa_resolved`

已经完成 OA availability 检查，知道可尝试哪些合法全文来源。

### `fetching`

worker 已获得该任务 lease，正在获取正文。

### `fulltext_ready`

已获得并验证全文，可以送入 RAG。

### `metadata_only`

未找到当前可合法自动获取的全文，但保留摘要和元数据进入 metadata corpus。

### `retryable_error`

网络、服务器或限流错误，应在未来再次尝试。

### `excluded`

撤稿、不符合研究范围、明确无效记录等。

### `archived`

当前项目暂时不处理，例如现有 2000 年以前记录。

---

# 5. 错误分类体系

禁止再使用一个自由文本 `last_error` 承担所有错误语义。

至少增加：

```text
last_error_class
last_error_detail
```

`error_class` 采用固定枚举：

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

## HTTP 推荐映射

```text
429
→ rate_limit
→ retryable

500 / 502 / 503 / 504
→ server_error
→ retryable

timeout
→ timeout
→ retryable

404
→ source_not_found
→ source-level non-retryable

403
→ access/license classification
→ 不等价于“文章不存在”
```

特别注意：

```text
HTTP 500 ≠ 非 OA
HTTP 503 ≠ not_found
非 PDF HTML ≠ 文献不存在
```

---

# 6. 建议的新数据库结构

保留 `tasks` 作为主表，但扩展字段。

## 6.1 tasks

建议至少包含：

```text
pmid              PRIMARY KEY

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

---

## 6.2 fetch\_attempts

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

---

## 6.3 source\_candidates

用于保存 OA locator 结果：

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

例如：

```text
PMC_AWS
EuropePMC
Unpaywall
Publisher
InstitutionalRepository
```

---

# 7. 完整维护工作流

以后维护智能体按照以下 Workflow 顺序执行。

---

# Workflow 0：安全检查和基线备份

任何 schema migration 或全局状态修改前必须执行。

检查：

```text
tasks.sqlite
索引信息.csv
reports/run_history.csv
pdfs_merged/
```

记录当前：

```text
总 PMID
各 status 数量
PDF 数量
有效 PDF 数
PMC 数
DOI 数
fulltext 数
```

数据库修改前生成：

```text
backups/YYYYMMDD-HHMMSS/
```

至少保存：

```text
tasks.sqlite
索引信息.csv
run_history.csv
config.json
```

### 验收标准

备份可正常打开；

主表行数与修改前一致；

PMID 主键无重复。

---

# Workflow 1：清理测试数据污染

当前：

```text
PMID_11111101
PMID_11111102
PMID_11111103
```

属于测试 fixture，不得位于生产 `pdfs_merged`。

迁移到：

```text
tests/fixtures/pdfs/
```

禁止以后通过：

```python
if pmid not in (...)
```

在各脚本中分散维护测试 PMID 黑名单。

### 验收

```text
生产 PDF 数
=
数据库中 production fulltext PDF 数
```

不得再出现：

```text
2422 PDF
但 done=2419
```

这一类不一致。

---

# Workflow 2：建立规范 PubMed 元数据基线

输入：

```text
当前所有 PMID
```

不要依赖历史 NBIB 永久保持最新。

采用 PubMed 官方批量接口：

```text
PMID list
   ↓
EPost / batched EFetch
   ↓
PubMed XML
```

重新获取：

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

原则：

```text
NBIB
=
原始检索快照

PubMed latest XML
=
当前规范 metadata
```

历史 NBIB 不删除、不覆盖。

---

# Workflow 3：元数据 UPSERT

现有 `02_build_queue.py` 使用：

```sql
INSERT OR IGNORE
```

这会导致新补充的 DOI/PMCID 无法更新已有任务。

必须改成：

```sql
INSERT ...
ON CONFLICT(pmid) DO UPDATE
```

更新：

```text
doi
pmcid
title
year
journal
authors
...
```

但绝不能自动覆盖：

```text
status
attempt history
content_path
retrieved_at
```

推荐逻辑：

```text
新 metadata 非空
→ 更新旧 metadata

新 metadata 为空
→ 一般不删除已有可靠 identifier

业务状态
→ 保留
```

### 验收

随机抽查：

```text
已存在 PMID
+
后来新增 PMCID
```

必须成功更新 PMCID，同时保持：

```text
attempt_count
content_status
历史 fetch attempt
```

不变。

---

# Workflow 4：OA availability resolution

完成 metadata reconciliation 后，对每篇文献定位可获取全文。

## 4.1 有 PMCID

优先检查：

```text
PMC AWS Article Dataset
```

PMC 当前 AWS Article Dataset 支持匿名访问，并提供文章版本对应的 JSON/XML/TXT/PDF URL。官方 inventory 每日更新。

记录：

```text
article_version
license
xml_url
text_url
pdf_url
updated_at
```

不能仅根据：

```text
存在 PMCID
```

推断：

```text
可自由再利用
```

必须保存 article-level license。

---

## 4.2 Europe PMC

PMC AWS 没有合适正文时，可检查：

```text
Europe PMC
```

优先：

```text
fullTextXML
```

而不是优先 PDF render。

Europe PMC REST 支持：

```text
/{id}/fullTextXML
```

用于 OA 全文 XML。

---

## 4.3 有 DOI

查询：

```text
Unpaywall API
```

记录：

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

Unpaywall API 要求提供真实 email，目前官方 API 限额为每日 100,000 次，远高于本项目当前规模。

---

## 4.4 DOI 补全

DOI resolver 优先：

```text
1. PubMed 同 PMID ArticleId
2. PubMed metadata
3. OpenAlex
4. Crossref
```

第三方候选必须综合：

```text
Title
Year
First author
Journal / ISSN
Volume
Pages
```

进行验证。

不得只依赖标题 similarity。

---

# Workflow 5：修复 DOI 作者匹配

现有 `06_doi_lookup.py`：

```python
_fau_surname()
```

按：

```text
Surname, Given
```

解析作者；

但主流程实际可能传入 PubMed ESummary 风格：

```text
Smith AB
```

从而导致：

```text
smith ab
!=
smith
```

作者验证规则失效。

应建立统一函数：

```text
normalize_author_family()
```

针对：

```text
Smith AB
Smith, Andrew B
Andrew B Smith
```

全部归一化成：

```text
smith
```

最好优先直接使用结构化 API 提供的 family name，不通过字符串猜测。

---

# Workflow 6：正文获取

每个 source candidate 按优先级获取。

建议：

```text
Priority 1
PMC AWS XML

Priority 2
Europe PMC XML

Priority 3
PMC AWS TXT

Priority 4
合法 OA HTML/XML

Priority 5
合法 OA PDF
```

RAG 不再要求每篇全文必须先保存为 PDF。

正文目录建议：

```text
corpus_raw/
    PMID_123/
        source.json
        article.xml

    PMID_456/
        source.json
        article.pdf
```

`source.json` 保存：

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

---

# Workflow 7：统一 HTTP retry 层

当前 config 中存在：

```text
network_retries
pdf_retries
pmc_retries
```

但核心代码部分仍使用硬编码 `range(2)`，导致配置和实际行为漂移。

建立统一：

```text
HttpClient
```

或：

```python
request_with_retry()
```

所有官方 API 共用。

必须支持：

```text
connect timeout
read timeout

Retry-After

exponential backoff
jitter

429
5xx

maximum attempts

structured logging
```

示意：

```text
attempt 1
↓
429
↓
读取 Retry-After
↓
等待
↓
attempt 2
```

不要通过“固定暂停 + 随机模拟人工访问”维护官方 API。

---

# Workflow 8：内容验证

获得内容不等于验证成功。

## XML

验证：

```text
XML 可以解析
article/title 存在
正文长度达到最低值
PMCID/PMID 与目标匹配
```

## PDF

现有验证只有：

```text
%PDF-
+
>= 30 KB
```

只能证明文件看起来像 PDF。

新增：

```text
PDF parser 可正常打开
page_count > 0

提取前 1–2 页文字

题名 similarity
DOI match
PMID/PMCID match
```

建议保存两个独立状态：

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

才进入 RAG。

---

# Workflow 9：结构化全文转换

PMC XML：

```text
JATS XML
    ↓
article parser
    ↓
sections
```

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

然后生成：

```text
normalized/PMID_<pmid>.md
```

或：

```text
normalized/PMID_<pmid>.json
```

推荐 Markdown：

```markdown
---
pmid:
doi:
pmcid:
title:
year:
journal:
source:
license:
---

# Title

## Abstract

...

## Introduction

...

## Methods

...
```

PDF 来源则继续使用现有 MinerU 路径。

因此下游形成：

```text
XML ───────────────┐
                   ↓
                Markdown
                   ↓
TXT/HTML ──────── parser
                   ↓
                Markdown
                   ↓
PDF → MinerU ─── Markdown
                   ↓
                  RAG
```

---

# Workflow 10：RAG 入库前校验

正文进入向量库之前必须满足：

```text
PMID 唯一
metadata match
content SHA256 已计算
非测试 fixture
非重复正文
正文长度足够
```

manifest 建议记录：

```text
PMID
content_sha256
metadata_sha256
source
format
embedding_model
chunker_version
indexed_at
```

这样正文变化或 chunking 算法变化时可以准确增量重建。

---

# Workflow 11：任务 lease 与并发控制

现有：

```text
downloading
updated_at
```

超过 15 分钟即重新变为 pending。

如果 worker 实际运行超过 15 分钟，可能造成重复领取。

修改为：

```text
worker_id
lease_until
heartbeat_at
```

领取任务：

```text
pending
↓
事务性 UPDATE
↓
fetching
lease_until = now + N
```

worker 周期更新：

```text
heartbeat_at
lease_until
```

只有：

```text
lease_until < now
```

才允许其它 worker 回收任务。

---

# Workflow 12：报告系统重构

当前 `05_report.py` 通过：

```python
err.split(':')[0]
```

粗略聚类错误，导致大量信息最终只显示为：

```text
scihub
```

无法说明真正失败原因。

以后至少输出以下指标。

## 文献级

```text
total
metadata_ready

fulltext_ready
metadata_only
retryable_error

archived
excluded
```

## Identifier

```text
with_doi
with_pmcid
without_doi
without_pmcid
```

## OA

```text
pmc_aws_available
europepmc_available
unpaywall_available

no_oa_fulltext
license_restricted
```

## Source success

```text
PMC_AWS_XML
EuropePMC_XML
Unpaywall_PDF
Publisher
Repository
```

## Failure

```text
timeout
rate_limit
server_error
invalid_content
metadata_mismatch
...
```

## RAG

```text
raw_fulltext
normalized_documents
indexed_documents
conversion_failed
index_failed
```

最终报告应该可以回答：

> 哪个来源贡献了多少全文？

> 哪个来源失败最多？

> 网络错误有多少？

> 真正无 OA 全文有多少？

> 当前 RAG 覆盖率是多少？

---

# 8. CSV 和数据库同步规则

以后数据只能沿一个方向流动：

```text
PubMed/API
    ↓
tasks.sqlite
    ↓
reports/*.csv
    ↓
RAG metadata export
```

禁止：

```text
tasks.sqlite
↓
CSV
↓
再次反向覆盖 tasks.sqlite
```

除非明确执行：

```text
import/migration
```

并经过专门校验。

---

# 9. 批次系统调整

目前 `12_build_pmc_batches.py` 通过 CSV 维护 `pmc_batches.csv`，存在重复 header 和重复 batch/PMID 的风险。

未来建议：

```text
batch
=
SQLite 查询条件
```

而不是独立状态数据库。

例如：

```sql
SELECT pmid
FROM tasks
WHERE
    pmcid IS NOT NULL
    AND status IN (...)
ORDER BY year DESC
LIMIT 400;
```

如果确实需要保存 batch，应新增：

```text
batches
batch_members
```

两个 SQLite 表。

不要：

```text
tasks_pmc_b1.sqlite
tasks_pmc_b2.sqlite
tasks_pmc_b3.sqlite
...
```

无限扩展独立数据库文件。

---

# 10. Crossref 使用规范

Crossref 只用于：

```text
metadata resolution
identifier resolution
```

不能直接证明存在 OA 全文。

所有请求：

```text
真实 mailto
明确 User-Agent
cache
HTTP 状态处理
backoff
```

Crossref 官方建议使用 `mailto` 标识客户端、缓存重复查询，并在响应变慢或出现异常时降低请求速度。

当前：

```json
"crossref_mailto": "research@example.com"
```

必须替换为环境变量，例如：

```text
CROSSREF_MAILTO
```

禁止把真实邮箱硬编码进仓库。

---

# 11. 配置管理

`config.json` 只保留非敏感默认配置。

例如：

```json
{
  "pubmed_batch_size": 200,
  "max_workers": 4,

  "connect_timeout": 15,
  "read_timeout": 60,

  "max_retries": 4,

  "backoff_base": 2,
  "backoff_max": 120
}
```

敏感/个人配置：

```text
CROSSREF_MAILTO
UNPAYWALL_EMAIL
NCBI_API_KEY
```

通过：

```text
environment variables
```

提供。

仓库只保留：

```text
.env.example
```

---

# 12. 建议代码结构

不要继续扩大单个 `03_downloader.py`。

推荐：

```text
scripts/
│
├── metadata/
│   ├── pubmed.py
│   ├── crossref.py
│   └── identifiers.py
│
├── sources/
│   ├── pmc_aws.py
│   ├── europepmc.py
│   ├── unpaywall.py
│   └── publisher.py
│
├── pipeline/
│   ├── resolve.py
│   ├── fetch.py
│   ├── validate.py
│   └── normalize.py
│
├── db/
│   ├── schema.py
│   ├── migrations.py
│   └── repository.py
│
└── cli/
    ├── refresh_metadata.py
    ├── resolve_oa.py
    ├── fetch_content.py
    ├── normalize_content.py
    ├── report.py
    └── sync_rag.py
```

---

# 13. 推荐 CLI 工作流

完成重构后，日常执行应该简化成：

```text
01 refresh metadata
↓
02 resolve OA
↓
03 fetch
↓
04 validate
↓
05 normalize
↓
06 sync RAG
↓
07 report
```

推荐未来 CLI：

```powershell
python -m scripts.cli.refresh_metadata
```

然后：

```powershell
python -m scripts.cli.resolve_oa
```

然后：

```powershell
python -m scripts.cli.fetch_content
```

然后：

```powershell
python -m scripts.cli.normalize_content
```

然后：

```powershell
python -m scripts.cli.sync_rag
```

最后：

```powershell
python -m scripts.cli.report
```

每一步必须：

```text
可重复执行
幂等
支持断点续跑
不会删除已成功结果
```

---

# 14. 周期性维护工作流

## 每次新增 PubMed 检索结果后

```text
导入新 PMID
↓
PubMed metadata refresh
↓
UPSERT
↓
OA resolution
↓
fetch
↓
normalize
↓
RAG
```

## 每周或定期

执行：

```text
retryable_error
+
metadata_only 中近期文献
```

重新检查：

```text
PMCID 是否新出现
Unpaywall 是否新出现 OA
PMC AWS inventory 是否新增
```

对于新发表文献必须允许：

```text
metadata_only
→ fulltext_ready
```

后续自动升级。

---

# 15. Retry 调度规则

建议：

```text
网络错误
1h → 6h → 24h

429
严格按照 Retry-After

5xx
15min → 1h → 6h → 24h

近期文章未进入 PMC
3d → 7d → 30d
```

而：

```text
license_restricted
```

不要频繁重试。

---

# 16. 实施优先级

## P0：必须首先完成

### P0-1

SQLite 成为唯一权威 metadata/status source。

### P0-2

建立 `fetch_attempts`。

### P0-3

修复 `not_found` 状态判断。

### P0-4

正确区分：

```text
404
429
5xx
timeout
license
no OA
```

### P0-5

metadata UPSERT。

### P0-6

接入新 PMC AWS Article Dataset。

### P0-7

移除三个生产目录中的测试 PDF。

---

## P1：紧接 P0

### P1-1

批量刷新 PubMed 元数据。

### P1-2

接入 Europe PMC XML。

### P1-3

接入 Unpaywall。

### P1-4

JATS XML → Markdown。

### P1-5

bibliographic validation。

### P1-6

统一 retry client。

---

## P2

### P2-1

修复 DOI 作者 family name。

### P2-2

重构 batch 系统。

### P2-3

lease + heartbeat。

### P2-4

新 reporting system。

---

## P3

### P3-1

整理 legacy crawler。

### P3-2

依赖锁定。

### P3-3

建立单元测试和 integration tests。

### P3-4

清理 Git 中运行态数据库和临时文件。

---

# 17. 第一阶段具体执行任务

维护智能体第一次实施时，不应直接修改全部代码。

严格按以下顺序：

```text
STEP 1
建立 baseline report

STEP 2
完整备份

STEP 3
新增 schema migration

STEP 4
保持旧 downloader 不动
先实现 PubMed metadata refresh

STEP 5
运行 metadata reconciliation
验证 DOI/PMCID 更新

STEP 6
实现 PMC AWS resolver
只做 availability dry-run
不下载

STEP 7
统计预计可获取全文数量

STEP 8
抽取 20–50 篇测试 PMC AWS fetch

STEP 9
验证 XML → Markdown

STEP 10
扩大到全部 PMCID

STEP 11
接入 Unpaywall

STEP 12
最后才逐步关闭旧 crawler 主链
```

这样可以最大限度降低迁移风险。

---

# 18. P0 阶段验收指标

P0 完成后必须满足：

```text
所有 PMID 唯一
```

```text
SQLite 与 CSV 不存在 DOI/PMCID 漂移
```

```text
HTTP 503 不会变成 not_found
```

```text
HTTP 429 可以自动 retry
```

```text
每次 source 请求都有 fetch_attempt
```

```text
测试 PMID 不存在于生产 corpus
```

```text
PMCID 后补能够通过 UPSERT 自动进入任务表
```

```text
PMC AWS availability 可以批量检查
```

---

# 19. P1 阶段验收指标

```text
有 PMC AWS XML 的文章
→ XML 获取成功率可统计
```

```text
Europe PMC XML
→ source success 可统计
```

```text
Unpaywall
→ OA availability 可统计
```

```text
XML 正文
→ 无需 PDF/MinerU
→ 可以进入 RAG
```

```text
PDF
→ bibliographic_match
→ 防止误文献进入语料库
```

---

# 20. 系统最终应提供的核心指标

维护智能体每次完成流水线后必须报告：

```text
Corpus target:
XXXX

Metadata ready:
XXXX

DOI coverage:
XX.X%

PMCID coverage:
XX.X%

OA fulltext resolvable:
XXXX / XX.X%

PMC AWS:
XXXX

Europe PMC:
XXXX

Unpaywall:
XXXX

Fulltext ready:
XXXX / XX.X%

Metadata-only:
XXXX

Retryable:
XXXX

License restricted:
XXXX

Validation failed:
XXXX

Normalized for RAG:
XXXX

Indexed:
XXXX
```

这组数字应该取代当前简单的：

```text
done
pending
not_found
failed
```

成为项目健康度指标。

---

# 21. 维护智能体禁止事项

维护智能体不得：

1. 在未备份数据库的情况下执行 schema migration；
2. 将 PubMed NBIB 重跑后直接覆盖现有下载状态；
3. 使用 `--fresh` 清除任务状态，除非明确要求重建；
4. 将 HTTP 429/5xx 归类为文献不存在；
5. 将“有 PMCID”直接等同于“允许无限制再利用”；
6. 将测试 PDF 放入生产 corpus；
7. 把 CSV 当成 SQLite 的平行权威状态源；
8. 在没有 bibliographic validation 的情况下把未知 PDF 直接进入 RAG；
9. 为提高覆盖率继续投入验证码绕过或反爬对抗作为生产主策略；
10. 删除旧 crawler 之前未完成新 pipeline 的平行验证。

---

# 22. Legacy 代码处理策略

以下代码暂不立即删除：

```text
03_downloader.py
altcha_solver.py
selenium_fallback.py
旧 mirror config
```

迁移为：

```text
legacy/
```

前提是：

```text
PMC AWS
+
Europe PMC
+
Unpaywall
```

新流水线经过完整验证。

迁移后：

```text
默认 CLI 不调用 legacy
```

只有人工明确指定时才允许运行历史路径。

---

# 23. 重构成功的最终判断标准

项目不再以：

> “下载了多少 PDF”

作为主要成功指标。

最终判断标准应为：

> “目标 PubMed 文献中，有多少已经获得可信规范元数据；其中多少具有许可条件明确的可用全文；这些全文中有多少完成正文验证、标准化并成功进入 RAG。”

最终架构：

```text
PubMed PMID
    ↓
Canonical metadata
    ↓
OA resolver
    ├── PMC AWS
    ├── Europe PMC
    ├── Unpaywall
    └── Authorized repository
    ↓
Content fetch
    ↓
Content + bibliographic validation
    ↓
Structured normalization
    ↓
Markdown / JSON
    ↓
Chunking
    ↓
Embedding
    ↓
Chroma
```

整个流程必须实现：

```text
可追溯
可重试
幂等
可恢复
可审计
许可信息明确
数据源唯一
正文身份可验证
```

---

# 24. 建议维护智能体当前立即执行的下一任务

当前不要继续直接扩大旧 `pmc_b6` 下载。

建议立即开始：

```text
任务 A
备份并生成数据库 baseline

任务 B
设计并迁移新 schema

任务 C
批量刷新全部 PMID 的 PubMed metadata

任务 D
比较更新前后的 DOI/PMCID

任务 E
建立 PMC AWS inventory resolver

任务 F
对当前有 PMCID 且尚无全文的文章做 availability dry-run

任务 G
输出新的 OA coverage report
```

完成这些步骤后，再决定实际全文下载顺序。

第一份新报告至少应给出：

```text
目标文献总数

有 DOI 数
有 PMCID 数

PMC AWS XML available
PMC AWS PDF available

Europe PMC XML available

Unpaywall OA available

metadata-only

需要 retry

identifier 异常
```

只有得到这份报告以后，项目维护智能体才应继续进行大规模全文抓取。

---

# 25. 总结

当前系统的主要问题不是“抓取速度不足”，而是“成功定义和状态管理不够准确”。

下一阶段应从：

```text
如何绕过网页限制拿 PDF？
```

转换到：

```text
如何确认文章身份？
如何确认 OA 与许可？
如何找到最可靠正文来源？
如何区分永久失败和暂时失败？
如何把结构化全文直接变成高质量 RAG 语料？
```

完成这一重构后，文献获取将从一个高维护成本的网页爬虫，转变为一个：

```text
PubMed 驱动
+
官方 OA 数据源驱动
+
结构化全文优先
+
SQLite 状态驱动
+
可审计
+
可增量维护
```

的长期文献语料供应系统。
