# STAGE A5 EXECUTION REPORT

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。本报告只记录 A5 执行证据。

## 1. Stage Information

~~~
Stage: A5 — 正文获取与统一 Retry Client
Phase: Phase A
Base commit: 7d543a55ee58f6e4a511124e774c2a8cf9d6b4fb
End commit: a12b6b7b0619aa20d229519064800ea7f29117db
Date: 2026-09-16
Executor: New independent Worker (Luna/XHigh)
Review status at start: A4 ACCEPT_WITH_FINDINGS; no blocker; A5 IN PROGRESS
Next allowed stage: A6, only after independent review approval
~~~

## 2. Scope

### Allowed changes

- 为 A5 建立统一 `UnifiedHttpClient`：connect/read timeout、429、Retry-After、5xx、指数退避、jitter、最大尝试次数、结构化日志和秘密脱敏。
- 从 A4 `source_candidates` 按 PMC AWS XML → Europe PMC XML → PMC AWS TXT → 合法 OA HTML/XML → 合法 OA PDF 获取单个 PMID。
- 在 `corpus_raw/PMID_<pmid>/` 原子写入正文和完整 `source.json`，并为每个 request 委托 A3 写入 `fetch_attempts`。
- 迁移 retry 配置为一个 `retry` policy，并让 Crossref contact 只从 `CROSSREF_MAILTO` 环境变量读取；仓库仅保留空值 `.env.example`。
- 增加完全离线的 A5 专项测试。

### Forbidden changes

- 不重定义 A3 error taxonomy 或 A4 source priority。
- 不实施 A6 bibliographic validation、A7 normalization、批量/生产下载或网络调用。
- 不修改 `tasks.sqlite`、focus 队列、Status/worklog、向量库或现有用户改动。

## 3. Files Changed

| File | Change | Reason | Behavior impact |
|---|---|---|---|
| `直肠癌文献爬取/scripts/fulltext_client.py` | A5 unified retry client；HTTP/异常分类委托 A3；attempt/log URL 脱敏 | 统一 source request 行为和 durable evidence | 每次 transport request 均记录；可按统一 policy 重试 |
| `直肠癌文献爬取/scripts/fetch_fulltext.py` | 候选排序、raw writer、单 PMID acquisition | 连接 A4 候选与 `corpus_raw` | 成功 raw fetch 仅保留 A3/A6 之前状态，不伪造 `fulltext_ready` |
| `tests/test_a5_fulltext.py` | 10 项离线专项测试 | 覆盖 A5 required tests 与 raw/status safety | transport、SQLite、writer 全部为注入/临时夹具 |
| `直肠癌文献爬取/config.json` | 删除 split retry 和仓库 Crossref contact；加入 `retry` | 统一 retry policy、移除配置内敏感 contact | 旧 retry 字段仅由 client 的单一兼容映射读取 |
| `.env.example` | 添加空值 `CROSSREF_MAILTO`、`UNPAYWALL_EMAIL`、`NCBI_API_KEY` | 说明环境变量入口且不保存秘密 | 实际值只来自进程环境/被忽略的本地 `.env` |
| `直肠癌文献爬取/scripts/03_downloader.py` | Crossref contact 改从环境变量读取 | 配置迁移的必要兼容修正 | 不再从 `config.json` 读取 contact |
| `直肠癌文献爬取/scripts/06_doi_lookup.py` | Crossref contact 改从环境变量读取 | 配置迁移的必要兼容修正 | 不再从 `config.json` 读取 contact |
| `docs/reviews/stage-A5-execution-report.md` | A5 执行证据和交接结论 | 满足 G-04 | 仅记录当前确认状态 |

## 4. Database or Index Changes

无生产数据库、索引、语料或 raw corpus 变更。A5 测试在临时目录创建 SQLite 数据库；`fetch_attempts` 证据由 A3 schema 在临时库中验证。没有运行生产 fetch，也没有写入网络来源。回滚使用本提交的 `git revert <end-commit>`；如未来已有 raw 数据，按 PMID 目录保留并按 G-07 使用可解释的 git/备份恢复，不删除未知数据。

## 5. Tests

| Command | Exit status | Result | Evidence |
|---|---:|---|---|
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m py_compile 直肠癌文献爬取/scripts/fulltext_client.py 直肠癌文献爬取/scripts/fetch_fulltext.py 直肠癌文献爬取/scripts/03_downloader.py 直肠癌文献爬取/scripts/06_doi_lookup.py tests/test_a5_fulltext.py` | 0 | PASS | `py_compile_exit=0` |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -p 'test_a5_fulltext.py' -v` | 0 | PASS | 10 tests: mock success, 429, 5xx, timeout, Retry-After, max attempts, source priority, source.json, redaction, raw-write/status safety |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -p 'test_a1_schema.py' -v` | 0 | PASS | 6 A1 tests |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -p 'test_a2_metadata.py' -v` | 0 | PASS | 9 A2 tests |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -p 'test_a3_state_machine.py' -v` | 0 | PASS | 20 A3 tests |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -p 'test_a4_oa_resolver.py' -v` | 0 | PASS | 6 A4 tests |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -p 'test_pipeline.py' -v` | 0 | PASS | 3 offline pipeline tests |
| `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m unittest discover -s tests -q` | 0 | PASS | 54 tests total; `OK` |
| `git diff --check` | 0 | PASS | no whitespace errors |

All A5 tests inject transport/provider/writer behavior and use temporary SQLite/raw paths. No default Europe PMC, PMC AWS, publisher, repository, or other network client was invoked.

## 6. Acceptance Criteria

| Criterion ID | Result | Evidence |
|---|---|---|
| A5-AC01 | PASS | `test_source_priority_matches_a5_sequence` asserts PMC AWS XML → Europe PMC XML → PMC AWS TXT → legal OA HTML/XML → legal OA PDF; legal OA TXT is excluded. |
| A5-AC02 | PASS | `UnifiedHttpClient.get` calls A3 `record_fetch_attempt` once for every transport request; tests verify success, 429/5xx/timeout failures, source, redacted URL, status, outcome, retryability and Retry-After. |
| A5-AC03 | PASS | `test_429_honors_retry_after_and_then_succeeds`, `test_5xx_uses_exponential_backoff_and_jitter`, `test_timeout_is_retryable`, and `test_max_attempts_stops_after_unified_limit`; injected sleeper proves no real wait. |
| A5-AC04 | PASS | `RawCorpusWriter` writes required PMID/DOI/PMCID/source/URL/license/retrieved_at/sha256/format/version fields; source JSON, structured logs and attempt URL tests prove configured secrets are absent. |
| A5-AC05 | PASS | `config.json` has one `retry` object and no split retry/contact keys; `retry_settings_from_config` maps legacy counts only into one `max_attempts`; `.env.example` contains empty variable declarations. |

## 7. Metrics

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| A5-specific offline tests | 0 | 10 | +10 |
| Split retry keys in `config.json` | 3 | 0 | -3 |
| Crossref contact value stored in repository config | 1 | 0 | -1 |
| Production task/raw/index writes | 0 | 0 | 0 |
| Full-text network requests | 0 | 0 | 0 |

## 8. Known Issues

- A5 intentionally stops before A6 validation; a raw fetch cannot produce `fulltext_ready` without the existing A3/A6 dual-validation evidence.
- `retry_settings_from_config` retains a single compatibility mapping for legacy split retry counts; the canonical repository configuration uses only `retry`.
- No production or bulk acquisition was performed in this stage.

## 9. Out of Scope

- A6 content validation and bibliographic match.
- A7 JATS/TXT/PDF normalization.
- A8 lease/heartbeat/batch and A9 reporting.
- Bulk/production downloads, new OA discovery, RAG/index changes, and any focus queue work.

## 10. Handoff

~~~
STATUS: PAUSED FOR INDEPENDENT REVIEW

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED
~~~
