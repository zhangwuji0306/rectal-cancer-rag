# STAGE A5 REMEDIATION EXECUTION REPORT

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。本报告只记录 A5 remediation 当前确认状态。

## 1. Stage information and remediation baseline

~~~
CURRENT_PHASE: Phase A
CURRENT_STAGE: A5 remediation — 正文获取与统一 Retry Client
BASE_COMMIT: 91031a7286ee77e41be1bb084da23fcc99d03560
END_COMMIT: ad2a45f284d1418b33232850b20d78ff5b89c267
DATE: 2026-09-16
START_TIME: 2026-09-16, before the first README read
EXECUTOR: New independent Worker (Luna/XHigh)
REVIEW_STATUS: prior A5 review rejected; this remediation addresses the two blocking findings
BLOCKING_FINDINGS: 03_downloader official API path was not unified; raw publication could leave an orphan article.xml; per-attempt detail could be stale
NEXT_ALLOWED_STAGE: A6, only after independent review approval
~~~

The remediation baseline is the supplied `BASE_COMMIT` plus the pre-existing uncommitted A5 partial work. The final A5 implementation has one shared retry policy/client for the actual full-text and official metadata paths, directory-level raw publication, and attempt-local error details.

## 2. Scope

### Allowed changes

- `UnifiedHttpClient`: connect/read timeout, 429, Retry-After, 5xx, timeout/connection classification, exponential backoff, jitter, maximum attempts, structured logs, and secret redaction.
- A5 full-text acquisition from A4 candidates in the inherited order: PMC AWS XML → Europe PMC XML → PMC AWS TXT → legal OA HTML/XML → legal OA PDF.
- `corpus_raw/PMID_{pmid}/` publication of article content and complete `source.json` as one directory-level operation.
- Shared client wiring for the actual `fetch_fulltext` entrypoint, OA resolver metadata calls, PubMed EFetch, DOI lookup, and the official Europe PMC/Crossref paths in `03_downloader.py`.
- Offline tests and this execution report.

### Forbidden changes

- No A3 taxonomy or A4 source-priority redefinition.
- No A6 bibliographic validation, A7 normalization, bulk acquisition, production download, network request, corpus/index update, or focus-queue work.
- No modification of AGENTS.md, Status.md, worklog/focus files, A4/A6+ task documents, or unrelated user changes.

## 3. Files in the A5 remediation commit

| File | Current change and effect |
|---|---|
| `直肠癌文献爬取/scripts/fulltext_client.py` | Loads the canonical project config, exposes one retry policy/client, records one A3 `fetch_attempts` row per transport attempt, redacts URL/detail/identifier data, and keeps each attempt's own error detail. |
| `直肠癌文献爬取/scripts/fetch_fulltext.py` | Constructs the client from the real project configuration in the production entry path; preserves A4 candidate order; stages article and `source.json` together and publishes/rolls back the whole PMID directory. |
| `直肠癌文献爬取/scripts/oa_resolver.py` | Routes Europe PMC and Unpaywall metadata requests through a shared configured client while preserving A4 discovery, parsing, and source priority. |
| `直肠癌文献爬取/scripts/pubmed_metadata.py` | Routes NCBI EFetch through the shared client and stops reading the non-contract `NCBI_EMAIL` environment variable. |
| `直肠癌文献爬取/scripts/06_doi_lookup.py` | Routes NCBI/OpenAlex/Crossref requests through the shared client and attributes candidate lookups to the owning task PMID for durable evidence. |
| `直肠癌文献爬取/scripts/03_downloader.py` | Minimal wiring of its Europe PMC search/PDF and Crossref official requests to the client configured at `Crawler` construction; existing bulk scheduling and non-official mirror session behavior remain unchanged. |
| `tests/test_a5_fulltext.py` | 15 offline regressions covering policy construction through `fetch_fulltext.run_cli`, legacy downloader wiring, retry behavior, redaction, attempts, source JSON, directory fault cleanup, status safety, source order, and NCBI parameter provenance. |
| `docs/reviews/stage-A5-execution-report.md` | This current A5 execution evidence and handoff. |

No `config.json` or `.env.example` change was needed in this remediation: the supplied base already contains the canonical `retry` object and empty `CROSSREF_MAILTO`, `UNPAYWALL_EMAIL`, and `NCBI_API_KEY` declarations. The client retains only a compatibility mapping from legacy retry counts into the single `max_attempts` setting.

## 4. Database, raw corpus, and index changes

No production database, raw corpus, index, task queue, PDF corpus, or vector store was changed. Tests create only temporary SQLite databases and temporary raw/PDF directories. No network client was allowed to reach an external endpoint.

Rollback is a normal `git revert` of the final A5 remediation commit. The raw writer's failure path removes staged/published new artifacts; an existing PMID directory is restored from its temporary backup if replacement publication fails. Existing A3 `fetch_attempts` rows are retained by the database transaction model.

## 5. Tests and exact results

| Exact command | Exit status | Result |
|---|---:|---|
| `$env:PYTHONIOENCODING = 'utf-8'; .\.venv\Scripts\python -m unittest discover -s tests -v` | 0 | Baseline before the final additions: 54 tests passed, `OK`. |
| `$env:PYTHONIOENCODING = 'utf-8'; .\.venv\Scripts\python -m unittest tests.test_a5_fulltext -q` | 0 | 15 A5 tests passed, `OK`. |
| `$env:PYTHONIOENCODING = 'utf-8'; .\.venv\Scripts\python -m py_compile '直肠癌文献爬取/scripts/fulltext_client.py' '直肠癌文献爬取/scripts/fetch_fulltext.py' '直肠癌文献爬取/scripts/03_downloader.py' '直肠癌文献爬取/scripts/06_doi_lookup.py' '直肠癌文献爬取/scripts/oa_resolver.py' '直肠癌文献爬取/scripts/pubmed_metadata.py' 'tests/test_a5_fulltext.py'` | 0 | All A5 implementation/test modules compiled. |
| `$env:PYTHONIOENCODING = 'utf-8'; .\.venv\Scripts\python -m unittest discover -s tests -q` | 0 | Final full offline regression: 59 tests passed, `OK`. |
| `git diff --check -- '直肠癌文献爬取/scripts/03_downloader.py' '直肠癌文献爬取/scripts/06_doi_lookup.py' '直肠癌文献爬取/scripts/fetch_fulltext.py' '直肠癌文献爬取/scripts/fulltext_client.py' '直肠癌文献爬取/scripts/oa_resolver.py' '直肠癌文献爬取/scripts/pubmed_metadata.py' 'tests/test_a5_fulltext.py'` | 0 | No whitespace errors. |
| `git -c core.quotePath=false diff --cached --name-only` after explicit A5 staging | 0 | Staged-name check passed for exactly the 7 implementation/test files before the implementation commit. |

The tests use injected transports, providers, writers, and temporary paths. They do not invoke default network transports, production writers, bulk workers, or acquisition commands.

## 6. Acceptance criteria evidence

| Criterion | Result | Evidence |
|---|---|---|
| A5-AC01 | PASS | `test_source_priority_matches_a5_sequence` asserts PMC AWS XML → Europe PMC XML → PMC AWS TXT → legal OA HTML/XML → legal OA PDF and excludes legal OA TXT; the A4 `_candidate_sort_key` remains the tie-break owner. |
| A5-AC02 | PASS | `UnifiedHttpClient.get` records source, URL, identifier, result, and retry metadata for every transport attempt. Tests cover success, 429, 5xx, timeout, storage failure, and the 03 Europe PMC official path. `test_each_fetch_attempt_keeps_its_own_error_detail` proves attempt-local details. |
| A5-AC03 | PASS | `test_429_honors_retry_after_and_then_succeeds`, `test_5xx_uses_exponential_backoff_and_jitter`, `test_timeout_is_retryable`, and `test_max_attempts_stops_after_unified_limit` prove the shared policy with an injected sleeper. Configured connect/read timeouts are asserted through the real `fetch_fulltext.run_cli` path and the wired 03 `Crawler` path. |
| A5-AC04 | PASS | `RawCorpusWriter` emits PMID, DOI, PMCID, source, URL, license, retrieved_at, sha256, format, and version. Source JSON, structured log, and attempt URL tests prove secret redaction; the NCBI regression proves `NCBI_EMAIL` is not loaded. |
| A5-AC05 | PASS | The canonical base config contains one `retry` object and no split retry/contact keys. `retry_settings_from_config` maps any legacy retry counts only into the single unified `max_attempts`; all actual official request paths use the shared client. |

## 7. Metrics

| Metric | Before remediation | After remediation | Delta |
|---|---:|---:|---:|
| A5专项离线测试 | 10 | 15 | +5 |
| Full offline test suite | 54 | 59 | +5 |
| Direct official `requests.get` call sites in `03_downloader.py` | 3 | 0 | -3 |
| New-directory raw fault-injection cases | 0 | 2 | +2 |
| Attempt-local error-detail regression cases | 0 | 1 | +1 |
| Production/network acquisition runs | 0 | 0 | 0 |

## 8. Known issues

- A5 intentionally stops before A6 validation; a successful raw fetch does not create `fulltext_ready` without the existing dual validation evidence.
- The client retains a compatibility mapping for legacy retry-count keys, but the canonical project configuration has only the unified `retry` object.
- `03_downloader.py` still contains the pre-existing non-official mirror/session workflow; only its official Europe PMC/Crossref request paths were wired in this remediation.

## 9. Out of scope

A6 bibliographic match and content acceptance; A7 normalization; A8 lease/heartbeat/batch; A9 reporting; all B/C/D stages; production or bulk downloads; new OA discovery; RAG/index work; focus queue/status/worklog orchestration; and unrelated user modifications.

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