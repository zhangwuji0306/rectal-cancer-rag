# Stage A4 Remediation Execution Report

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。
> A4 contract: [A4-OA-Resolver.md](../../整改手册/任务书/A4-OA-Resolver.md)。

## 1. Stage information

~~~text
Stage: A4 remediation — OA Resolver
Phase: Phase A
Depends on: A3 APPROVED WITH NON-BLOCKING NOTES
BASE_COMMIT: b841eb9 (A4: fix Unpaywall candidate normalization)
START_TIME: 2026-09-16T18:26:36+08:00
END_COMMIT: HEAD
Date: 2026-09-16 (Asia/Shanghai)
Executor: A4 remediation Worker
Review status: PAUSED FOR INDEPENDENT REVIEW
Next allowed stage: A5, only after independent review approval
~~~

## 2. Scope

### Allowed changes

- `直肠癌文献爬取/scripts/oa_resolver.py`: preserve A3 retryable failure semantics when no OA candidate is produced.
- `tests/test_a4_oa_resolver.py`: add the minimum retryable-provider/no-candidate regression test.
- `docs/reviews/stage-A4-execution-report.md`: record this remediation evidence.

### Forbidden changes

- No A3 state-machine or error-taxonomy changes.
- No A5 retry client or content fetching.
- No A6 validation, A7 normalization, A8 batching, or focus-queue work.
- No production SQLite, corpus, vector-index, or large-scale network writes.
- No changes to `AGENTS.md`, `整改手册/`, work logs, or `直肠癌文献爬取/focus/`.

## 3. Result

When all providers return no candidate, `resolve_one()` now promotes the task to
`retryable_error` if any caught provider failure is already classified by A3 as
retryable. It retains `metadata_only` only when no candidate exists and all
provider failures are non-retryable or absent. Candidate success still takes
precedence and remains `oa_resolved`; source priority, license preservation,
dry-run behavior, and Unpaywall normalization are unchanged.

The aggregate `metadata_only` count is incremented only for items whose actual
status is `metadata_only`; `no_candidates` continues to count every item with
zero candidate rows.

## 4. Before / after / delta

| Check | Before (BASE_COMMIT) | After | Delta |
|---|---:|---:|---:|
| A4 tests | 5 tests; exit 0 | 6/6; exit 0 | +1 regression test |
| Retryable provider failure + no candidate | no coverage; downgraded to `metadata_only` | covered; status `retryable_error` | semantic downgrade removed |
| Existing A4 behaviors | source priority/license/dry-run/Unpaywall tests present | all pass | unchanged |

## 5. Tests and actual exit status

All commands ran from `E:\writing-rag` in PowerShell with UTF-8 Python output.
Tests use temporary SQLite databases and do not open the production database for
writing.

| Command | Exit status | Result |
|---|---:|---|
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p 'test_a4_oa_resolver.py' -v` | 0 | 6/6 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p test_a1_schema.py -v` | 0 | 6/6 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p test_a2_metadata.py -v` | 0 | 9/9 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p test_a3_state_machine.py -v` | 0 | 20/20 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m unittest discover -s tests -p 'test_*.py' -v` | 0 | 44/44 passed |
| `$env:PYTHONIOENCODING='utf-8'; & '.venv\Scripts\python' -m py_compile '直肠癌文献爬取\scripts\oa_resolver.py' 'tests\test_a4_oa_resolver.py'` | 0 | syntax compilation passed |

No production database or index was batch-written. No content download or
unnecessary live network request was performed.

## 6. File changes and behavior impact

- `直肠癌文献爬取/scripts/oa_resolver.py`: retryable provider failures now
  retain the canonical A3 task status when candidate resolution is empty.
- `tests/test_a4_oa_resolver.py`: verifies Europe PMC timeout classification,
  `retryable_error` task status, zero `metadata_only` count, and no candidate
  rows.
- `docs/reviews/stage-A4-execution-report.md`: this execution evidence.

No database, migration, corpus, or index change occurred. Rollback is the
standard stage commit revert under G-07; no task rows need deletion.

## 7. Known issues

- Live Europe PMC and Unpaywall endpoints were not called in this offline
  remediation run.
- A5 remains responsible for actual retries and content acquisition.

## 8. Out of scope

A5 full-text fetching and unified retry client; A6 validation and bibliographic
matching; A7 normalization; A8 lease/heartbeat/batch; A9 reporting; focus queue
tasks; and all RAG indexing work were not started.

## 9. Acceptance criterion evidence mapping

| ID | Evidence |
|---|---|
| A4-AC01 | Existing `SOURCE_ORDER`, `SOURCE_PRIORITY`, and source-priority test remain unchanged and pass. |
| A4-AC02 | Existing PMCID/license/version/URL and Unpaywall provenance tests remain unchanged and pass. |
| A4-AC03 | Existing dry-run no-content-fetch test passes; no content fetcher was invoked. |
| A4-AC04 | Existing resolver/report fields remain covered; the new failure case records `retryable_error` instead of `metadata_only`. |

## Independent review handoff

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED

STATUS: PAUSED FOR INDEPENDENT REVIEW
