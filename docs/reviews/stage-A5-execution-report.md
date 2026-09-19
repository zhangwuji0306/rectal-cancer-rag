# STAGE A5 REMEDIATION EXECUTION REPORT

> 本报告记录 A5 最终整改的当前确认状态；A6 未启动。

## 1. Stage information

~~~
CURRENT_PHASE: Phase A
CURRENT_STAGE: A5 remediation — 正文获取与统一 Retry Client
BASE_COMMIT: a83dbc9d1100b3403c6ab11345fca41661b6315c
IMPLEMENTATION_END_COMMIT: dd4a345c8f2610f416203c6fcd2b074de0b5c3f7
REPORT_COMMIT: separate report-only commit; SHA is recorded in the final handoff
REVIEW_STATUS: APPROVED WITH NON-BLOCKING NOTES
BLOCKING_FINDINGS: None
NEXT_ALLOWED_STAGE: A6 — 内容验证与 Bibliographic Match
REVIEWED_HEAD: c4e60fe3fbb9e129ad37e2a6fde3e85ba6306547
~~~

The implementation baseline is the resolved local target branch codex/a5-remediation-review-fix at BASE_COMMIT. The initial local main observation before resolving that target branch was 500990a82f16c57fc127fcf24b34b0769b93b6ef; its unrelated uncommitted user changes were preserved.

## 2. Scope and changed files

The final remediation changes only:

- 直肠癌文献爬取/scripts/fetch_fulltext.py
- tests/test_a5_fulltext.py
- docs/reviews/stage-A5-execution-report.md

No fulltext_client.py, schema, state model, A4 source priority, production database, production corpus, index, or A6 file was changed in this final remediation.

## 3. Fix

The successful full-text path now has two explicit phases:

1. _update_task_content() runs after raw publication and retains the existing writer.rollback(artifact) behavior if SQLite update fails.
2. writer.commit(artifact) runs only after SQLite durable commit. Cleanup exceptions are isolated as cleanup failures and do not call writer.rollback(artifact), so the newly published raw directory remains authoritative. A failed cleanup may leave the old .backup-* directory for later cleanup.

## 4. Regression test

Added:

- test_backup_cleanup_failure_does_not_rollback_committed_raw

The test uses temporary SQLite and raw directories, preloads OLD raw content, fetches NEW content, injects an OSError during backup cleanup, and verifies:

- acquisition outcome remains raw_fetched;
- article.xml remains NEW and OLD raw is not restored;
- SQLite content_sha256, content_path, content_source, content_format, and content_status match NEW raw content;
- the existing task status remains metadata_only;
- a backup directory may remain.

## 5. Exact verification results

| Exact command | Exit status | Actual result |
|---|---:|---|
| $env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m unittest tests.test_a5_fulltext -q | 0 | Ran 28 tests; OK; no warnings/errors |
| $env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m unittest discover -s tests -q | 0 | Ran 72 tests; OK; no warnings/errors |
| $env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m py_compile '直肠癌文献爬取/scripts/fetch_fulltext.py' '直肠癌文献爬取/scripts/fulltext_client.py' 'tests/test_a5_fulltext.py' | 0 | No output; no warnings/errors |
| git diff --check | 0 | No whitespace errors; Git emitted non-failing LF-to-CRLF normalization warnings for pre-existing unrelated user-modified documents |

No production acquisition, network full-text request, production SQLite write, corpus write, vector-index operation, ingestion, or A6 action was performed.

## 6. Known issues

- A5 is approved with non-blocking notes; raw content is not promoted to fulltext_ready without the existing A6 validation evidence.
- Post-commit backup cleanup failures are currently swallowed silently; future maintenance may add structured warning or audit logging.
- If post-commit backup cleanup fails, the old .backup-* directory can remain by design; the NEW raw and SQLite metadata are retained as the consistent durable state.
- Existing unrelated user modifications in the working tree were preserved and were not included in the implementation commit or report-only commit.

## 7. Independent review disposition

~~~
STAGE: A5 — 正文获取与统一 Retry Client
REVIEWED_HEAD: c4e60fe3fbb9e129ad37e2a6fde3e85ba6306547
DECISION: APPROVED WITH NON-BLOCKING NOTES
BLOCKING_FINDINGS: None
NEXT_ALLOWED_STAGE: A6 — 内容验证与 Bibliographic Match
~~~

Non-blocking notes:

1. Post-commit backup cleanup failures are currently swallowed silently; future maintenance may add structured warning or audit logging.
2. A failed cleanup may intentionally leave a .backup-* directory to preserve DB/raw consistency; later maintenance tooling may remove stale backups safely.

A6 has not been started.
