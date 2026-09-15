# Stage A4 Execution Report

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。
> 本报告记录 A4 OA Resolver 的实际执行证据；来源优先级与字段合同以 [A4-OA-Resolver.md](../../整改手册/任务书/A4-OA-Resolver.md) 为准。

## 1. Stage information

~~~text
Stage: A4 — OA Resolver
Phase: Phase A
Depends on: A3 APPROVED WITH NON-BLOCKING NOTES
BASE_COMMIT: e26705bf364b90a1d5b9e40b701f841e73c86ec4
START_TIME: 2026-09-15T19:31:59+08:00
END_COMMIT: HEAD
Date: 2026-09-15 (Asia/Shanghai)
Executor: Independent final Worker (Luna / XHigh)
Review status: PAUSED FOR INDEPENDENT REVIEW
Next allowed stage: A5, only after independent review approval
~~~

`END_COMMIT: HEAD` 指向本次唯一的 A4-only handoff commit；提交后以 `git rev-parse --verify HEAD` 核对其实际 SHA。工作区中预先存在的 `整改手册/` 修改未暂存、未提交。

## 2. Scope and files

### Allowed changes

- `直肠癌文献爬取/scripts/oa_resolver.py`: A4 source resolution in fixed priority order, candidate normalization and idempotent `source_candidates` persistence, PMCID/Unpaywall provenance fields, local PMC AWS inventory provider, Europe PMC/Unpaywall metadata clients, and dry-run CLI entry point.
- `tests/test_a4_oa_resolver.py`: A4 offline contract tests for source priority, provenance, dry-run no-download, metadata-only fallback, and Unpaywall field preservation.
- `docs/reviews/stage-A4-execution-report.md`: this execution evidence report.

### Forbidden changes

- No A5 content download or unified retry client.
- No A6 content validation or bibliographic match.
- No A7 normalization, A8 lease/heartbeat/batch, or any indexing work.
- No production SQLite migration or write, no production corpus change, and no large-scale network request.

## 3. Implemented behavior and defect correction

The resolver applies the canonical order `PMC AWS Article Dataset → Europe PMC fullTextXML → Unpaywall OA location → Publisher → InstitutionalRepository`, writes every normalized candidate before task status promotion, and records metadata-only when no candidate exists. PMCID candidates retain article version, license, XML/TXT/PDF URLs and `updated_at`; Unpaywall candidates retain `is_oa`, `oa_status`, `best_oa_location`, landing URL, PDF URL, `host_type`, version, license and response metadata.

The repaired defect was the single-response Unpaywall mapping path. A mapping shaped like an Unpaywall response was previously interpreted as a PMID/PMCID provider map and reduced to `[]` before normalization. `_provider_call(..., source=UNPAYWALL)` now preserves mappings containing Unpaywall response keys (`is_oa`, `best_oa_location`, or `oa_locations`), allowing the existing Unpaywall expansion and candidate writer to persist the row.

## 4. Before / after / delta

| Check | Before | After | Delta |
|---|---:|---:|---:|
| A4专项测试 | 4/5 passed, exit 1 | 5/5 passed, exit 0 | +1 passing test; failure closed |
| Unpaywall mock candidate rows | 0; `IndexError` while reading row 0 | 1 persisted candidate row | +1 row; all asserted OA fields available |
| A1回归 | not run in this worker before patch | 6/6, exit 0 | no regression observed |
| A2回归 | not run in this worker before patch | 9/9, exit 0 | no regression observed |
| A3回归 | not run in this worker before patch | 20/20, exit 0 | no regression observed |
| 全量离线测试 | not run in this worker before patch | 43/43, exit 0 | no regression observed |

## 5. Tests and actual exit status

All commands were run from `E:\writing-rag` in PowerShell with `$env:PYTHONIOENCODING='utf-8'`; tests use temporary SQLite databases.

| Command | Exit status | Result |
|---|---:|---|
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p 'test_a4_oa_resolver.py' -v` (before patch) | 1 | 5 tests; 4 passed, Unpaywall field test errored with `IndexError` |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p 'test_a4_oa_resolver.py' -v` (after patch) | 0 | 5/5 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p test_a1_schema.py -v` | 0 | 6/6 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p test_a2_metadata.py -v` | 0 | 9/9 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p test_a3_state_machine.py -v` | 0 | 20/20 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p 'test_*.py' -v` | 0 | 43/43 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m py_compile '直肠癌文献爬取\scripts\oa_resolver.py' 'tests\test_a4_oa_resolver.py'` | 0 | syntax compilation passed |

No production SQLite was opened for writing by the tests, and no content fetcher was called on the dry-run path. No large-scale download was attempted.

## 6. Known issues

- Europe PMC and Unpaywall clients are metadata-resolution adapters; live calls require their configured endpoint/contact settings and were not run against the production task set in this first dry-run round.
- Publisher and institutional-repository candidates are provider injection points in A4; source-specific crawling is not part of this stage.
- Candidate URL selection records provenance only. Fetching, retries, content validation, bibliographic matching, normalization and acquisition batching remain later-stage work.

## 7. Out of scope

A5 full-text fetching and unified retry client; A6 validation and bibliographic match; A7 JATS/TXT/PDF normalization; A8 lease, heartbeat and batch orchestration; A9 acquisition reporting and Phase B/C/D indexing or generation were not implemented or started.

## 8. Acceptance criterion evidence mapping

| ID | Evidence |
|---|---|
| A4-AC01 | `SOURCE_ORDER`, `SOURCE_PRIORITY` and `_candidate_sort_key()` in `直肠癌文献爬取/scripts/oa_resolver.py`; `test_source_priority_and_all_candidates_are_written` verifies PMC AWS selection and all five candidate rows are written. |
| A4-AC02 | `_pmcid()`, `A4_SOURCE_CANDIDATE_COLUMNS`, `_normalize_candidates()` and `_write_candidates_in_connection()` preserve PMCID, article version, license, XML/TXT/PDF URLs and timestamps; `test_pmcid_version_license_and_urls_are_traceable` and `test_unpaywall_fields_are_preserved` verify the stored fields. |
| A4-AC03 | `resolve_one()` discards `content_fetcher` and performs metadata-only resolution; `test_dry_run_never_calls_content_fetcher` verifies zero content-fetch calls and the execution commands above used only temporary databases. |
| A4-AC04 | `resolve_pmids()` returns `pmcid_count`, `pmc_aws_available`, `europe_pmc_available`, `unpaywall_available`, `publisher_available`, `institutional_repository_available`, `no_candidates` and `metadata_only`; the A4 tests exercise PMCID, PMC AWS, Europe PMC, Unpaywall and metadata-only paths, and this report records the before/after counts. |

## Independent review handoff

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED

STATUS: PAUSED FOR INDEPENDENT REVIEW
