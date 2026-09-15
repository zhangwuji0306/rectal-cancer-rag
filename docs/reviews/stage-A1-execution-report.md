# Stage A1 Execution Report

## 1. Baseline and scope

- **Stage:** A1 — SQLite canonical authority, Schema v2, and PMID incremental ingestion
- **Dependency:** A0 APPROVED (user-confirmed)
- **Base commit:** `e61d0491b9b1b0badb83fa8c8dad0acb42c68da4` (`docs: record A0 stage commit`)
- **End commit:** `95df6f8631a43318f027bfe73817ad25f14fd912` (A1 implementation; this report is committed immediately after it)
- **Date:** 2026-09-15 (Asia/Shanghai)
- **Executor:** Codex final execution worker
- **Review status at start:** A0 APPROVED; no parallel A1 predecessor was used
- **Next allowed stage:** A2, only after independent review approval

The execution was limited to the A1 Allowed scope: Schema v2, additive migration, structural rollback strategy, schema versioning, provenance tables, PMID ingestion API/CLI, focused tests, live migration, and this report.

Forbidden and not implemented: PubMed EFetch, metadata refresh/UPSERT, DOI reconciliation, OA resolution, content fetching, retry state machine, full-text validation, normalization, and indexing.

## 2. Actual files and behavior

- `直肠癌文献爬取/scripts/a1_schema.py`
  - Defines and verifies the Schema v2 task contract.
  - Performs an explicit transactional additive migration from the legacy task table.
  - Maps legacy `pmc`, `attempts`, `route`, `last_error`, `pdf_path`, and `updated_at` into the corresponding v2 fields while retaining legacy columns for current compatibility tools.
  - Creates and verifies `fetch_attempts`, `source_candidates`, `discovery_batches`, and `task_discoveries`, plus `schema_version` and `PRAGMA user_version=2`.
  - Provides a structural rollback command that refuses to discard provenance or post-migration ingested task rows; the documented recovery path for such a database is restoration of the verified pre-migration backup.
- `直肠癌文献爬取/scripts/ingest_pmids.py`
  - Provides `ingest_pmids(...)` and the `ingest_pmids.py` CLI.
  - Validates positive decimal PMIDs, strips surrounding whitespace, de-duplicates within the input, creates pending tasks only for new PMIDs, and records discovery provenance in one explicit transaction.
  - Repeated `(batch_id, PMID)` discovery is idempotent; a different batch records a new discovery without changing the canonical task.
  - Existing task metadata, status, attempts, content path, and fetch history are not updated by discovery ingestion.
- `直肠癌文献爬取/scripts/migrate_schema.py`
  - Thin CLI entry point for `migrate`, `verify`, and `rollback`.
- `tests/test_a1_schema.py`
  - Offline migration, schema, provenance, idempotency, state-preservation, count-delta, and transaction-rollback tests.
- `docs/reviews/stage-A1-execution-report.md`
  - This execution evidence and handoff report.

The canonical authority remains `直肠癌文献爬取/tasks.sqlite`; no parallel workflow database was created.

## 3. Database migration and rollback evidence

Before migration, the canonical database was backed up through SQLite's backup API to:

`E:\writing-rag\.claude\tmp\crawler-backups\tasks-a1-pre-20260915.sqlite`

The backup was created before the live migration and was not overwritten. A separate temporary copy of that backup was used for a forward/rollback rehearsal and was removed after verification.

### Before / after / delta

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| `tasks` rows | 9,171 | 9,171 | 0 |
| distinct PMID | 9,171 | 9,171 | 0 |
| DOI populated | 7,906 | 7,906 | 0 |
| PMCID populated (`pmc` → `pmcid`) | 3,001 | 3,001 | 0 |
| content path populated (`pdf_path` → `content_path`) | 2,419 | 2,419 | 0 |
| total attempts (`attempts` → `attempt_count`) | 5,333 | 5,333 | 0 |
| `run_history` rows | 5,336 | 5,336 | 0 |
| `user_version` | 0 | 2 | +2 |
| `fetch_attempts` rows | not present | 0 | new empty table |
| `source_candidates` rows | not present | 0 | new empty table |
| `discovery_batches` rows | not present | 0 | new empty table |
| `task_discoveries` rows | not present | 0 | new empty table |

Live forward migration command output:

```text
{"after": 9171, "before": 9171, "distinct_pmids": 9171, "tasks": 9171, "verified": true}
```

Live verification command output:

```text
{"distinct_pmids": 9171, "tasks": 9171, "verified": true}
```

The live database additionally passed `PRAGMA integrity_check` (`ok`) and `PRAGMA foreign_key_check` (no rows). `schema_version` contains one v2 record with `previous_version=0`, `task_count_before=9171`, and all required task columns listed as added.

Rollback rehearsal on a copy of the pre-migration canonical database:

```text
forward: {"after": 9171, "before": 9171, "distinct_pmids": 9171, "tasks": 9171, "verified": true}
rollback: {"after": 9171, "before": 9171, "rolled_back": true}
post-rollback: user_version=0, task_count=9171, schema_version absent,
              legacy sample fields retained (pmid/doi/pmc/status/attempts/pdf_path)
```

## 4. Test commands and results

All Python commands used `.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`.

| Command | Exit | Result |
|---|---:|---|
| `.venv\Scripts\python.exe -m unittest -v tests.test_a1_schema` | 0 | 6 tests passed |
| `.venv\Scripts\python.exe 直肠癌文献爬取\scripts\ingest_pmids.py --help` | 0 | CLI parsed and displayed usage |
| `.venv\Scripts\python.exe tests\test_pipeline.py` | 0 | 3 offline regression tests passed |
| `.venv\Scripts\python.exe 直肠癌文献爬取\scripts\migrate_schema.py migrate --db 直肠癌文献爬取\tasks.sqlite` | 0 | live forward migration verified |
| `.venv\Scripts\python.exe 直肠癌文献爬取\scripts\migrate_schema.py verify --db 直肠癌文献爬取\tasks.sqlite` | 0 | live Schema v2 verification passed |
| `.venv\Scripts\python.exe 直肠癌文献爬取\scripts\migrate_schema.py rollback --db <temporary copy>` | 0 | structural rollback rehearsal passed |

## 5. Acceptance criteria evidence

| ID | Evidence |
|---|---|
| A1-AC01 | Live before/after count is 9,171/9,171. DOI, PMCID, content path, attempts, status distribution, and `run_history` count were preserved; v1→v2 field mappings were verified by migration tests and live counts. |
| A1-AC02 | `pmid` remains the `INTEGER PRIMARY KEY`; live `verify` passed. `schema_version`, `fetch_attempts`, `source_candidates`, `discovery_batches`, and `task_discoveries` exist with foreign keys and required columns. |
| A1-AC03 | `test_new_duplicate_and_cross_batch_ingestion` verifies new PMIDs create one pending task and one discovery record each. |
| A1-AC04 | The same batch test verifies repeated `(batch_id, PMID)` ingestion adds neither tasks nor discoveries. |
| A1-AC05 | The cross-batch test verifies a second batch adds discoveries only and does not add tasks. |
| A1-AC06 | `test_existing_task_state_and_fetch_history_are_untouched` verifies status, DOI, PMCID, content path, attempts, and fetch history remain unchanged for an existing task. |
| A1-AC07 | `test_mid_transaction_failure_rolls_back_batch_and_tasks` verifies a forced discovery insert failure leaves no batch, discovery rows, or new tasks; invalid PMID validation also occurs before database mutation. |

## 6. Known issues

- Existing legacy crawler/report scripts still read compatibility columns (`pmc`, `attempts`, `route`, `last_error`, `pdf_path`). A1 retains these columns and populates their v2 counterparts; updating downstream consumers is outside A1.
- The live database has an A1 pre-migration backup in `.claude/tmp/crawler-backups/`. Structural rollback intentionally refuses after provenance/task ingestion; restore the verified backup for that case.
- This repository contains unrelated pre-existing handbook modifications in the working tree; they are not part of the A1 change set.

## 7. Out of scope

No PubMed metadata retrieval or refresh, DOI reconciliation, OA resolver, content acquisition, retry orchestration, full-text validation, normalization, RAG indexing, or downstream A2 work was performed.

## 8. Handoff

The A1 implementation commit is `95df6f8631a43318f027bfe73817ad25f14fd912`. This report is committed immediately after it. No subsequent stage has been started.

STATUS: PAUSED FOR INDEPENDENT REVIEW

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED
