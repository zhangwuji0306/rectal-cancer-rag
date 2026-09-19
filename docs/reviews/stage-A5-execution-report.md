# STAGE A5 REMEDIATION EXECUTION REPORT

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。本报告记录 A5 remediation 的当前实现状态与复核边界。

## 1. Stage information and baseline

~~~
CURRENT_PHASE: Phase A
CURRENT_STAGE: A5 remediation — 正文获取与统一 Retry Client
BASE_COMMIT: 91031a7286ee77e41be1bb084da23fcc99d03560
REMEDIATION_BASE_COMMIT: 500990a82f16c57fc127fcf24b34b0769b93b6ef
END_COMMIT: a74c8dc671ad727037fe94a029623e030425bae0
DATE: 2026-09-19
EXECUTOR: Codex
REVIEW_STATUS: NEEDS REVISION findings implemented; pending independent re-review
NEXT_ALLOWED_STAGE: A6, only after independent review approval
~~~

END_COMMIT is the final implementation commit for this remediation review. The report-only update follows that commit and does not change the implementation range. The remediation is based on the previously reviewed A5 commit 500990a...; the review fixes are additive and remain within A5 scope.

## 2. Resolved review findings

- Metadata requests now reuse the unified transport/retry behavior without writing fetch_attempts or changing document status. This applies to PubMed EFetch, Crossref, OpenAlex, Unpaywall, and Europe PMC metadata/search routes.
- The client follows absolute and relative HTTP redirects for 301, 302, 303, 307, and 308 responses, with HTTP(S)-only targets and a maximum of five redirects. Redirects are not treated as retries.
- Raw publication can be staged transactionally with the task metadata update. SQLite update failure removes the new raw directory and restores the previous PMID directory.
- Any retryable response with a valid Retry-After value uses that delay before exponential backoff; this includes 429 and 5xx responses.
- Response parsing failures supplied by metadata handlers are recorded as parser_error; raw persistence failures remain storage_error.
- The execution report now references an existing Git commit.

## 3. Files changed

| File | Current effect |
|---|---|
| 直肠癌文献爬取/scripts/fulltext_client.py | Adds optional status-neutral attempt recording, bounded redirect handling, all-retryable-response Retry-After support, and handler-specific error classification. |
| 直肠癌文献爬取/scripts/fetch_fulltext.py | Adds staged raw publication with commit/rollback around the SQLite content update. |
| 直肠癌文献爬取/scripts/oa_resolver.py | Makes Europe PMC and Unpaywall metadata requests status-neutral and classifies JSON parsing failures. |
| 直肠癌文献爬取/scripts/pubmed_metadata.py | Makes PubMed metadata transport status-neutral. |
| 直肠癌文献爬取/scripts/06_doi_lookup.py | Makes NCBI/OpenAlex/Crossref metadata transport status-neutral and classifies JSON parsing failures. |
| 直肠癌文献爬取/scripts/03_downloader.py | Makes official Europe PMC search and Crossref metadata requests status-neutral and classifies JSON parsing failures; official PDF acquisition remains stateful. |
| tests/test_a5_fulltext.py | Adds regression coverage for metadata neutrality, redirects, 503 Retry-After, parser errors, and raw/SQLite rollback. |
| docs/reviews/stage-A5-execution-report.md | Records the current remediation state and real implementation commit. |

No production database, raw corpus, PDF corpus, vector index, task queue, or focus queue was changed.

## 4. Acceptance evidence

| Criterion | Result | Evidence |
|---|---|---|
| Metadata transport/state separation | IMPLEMENTED | Metadata calls pass record_attempts=False; full-text acquisition keeps the default stateful recorder. |
| Redirect correctness | IMPLEMENTED | Redirect status set, relative URL resolution, HTTP(S)-only validation, final URL propagation, and bounded chain are implemented in UnifiedHttpClient.get. |
| Raw/SQLite consistency | IMPLEMENTED | RawCorpusWriter retains a previous-directory backup until the task content update succeeds; failure invokes rollback. |
| Retry policy | IMPLEMENTED | Valid Retry-After is preferred for every retryable classification, including 5xx. |
| Error taxonomy | IMPLEMENTED | Metadata JSON handlers use parser_error; raw writer failures use storage_error. |
| Report traceability | PASS | BASE_COMMIT, REMEDIATION_BASE_COMMIT, and END_COMMIT resolve to existing Git commits. |

## 5. Tests

The prior A5 remediation recorded 59 offline tests passed at 500990a.... This review fix adds 12 A5 tests, bringing the expected suite size to 71 tests. The new tests are fully offline and use injected transports, temporary SQLite databases, and temporary raw directories.

Required verification commands:

~~~powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python -m py_compile '直肠癌文献爬取/scripts/fulltext_client.py' '直肠癌文献爬取/scripts/fetch_fulltext.py' '直肠癌文献爬取/scripts/03_downloader.py' '直肠癌文献爬取/scripts/06_doi_lookup.py' '直肠癌文献爬取/scripts/oa_resolver.py' '直肠癌文献爬取/scripts/pubmed_metadata.py' 'tests/test_a5_fulltext.py'
.\.venv\Scripts\python -m unittest tests.test_a5_fulltext -q
.\.venv\Scripts\python -m unittest discover -s tests -q
~~~

The connector session updated the remote implementation and tests but did not execute the repository's local Python environment. The 59-test result above is inherited evidence from the preceding A5 remediation commit; the 12 new tests require execution in the repository environment before final independent approval.

## 6. Handoff

~~~
STATUS: PAUSED FOR INDEPENDENT REVIEW

A6 has not been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED
~~~
