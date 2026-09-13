# STAGE A0 EXECUTION REPORT

## 1. Stage Information

- Stage: A0 — current-state freeze and complete backup
- Base commit: `b2ce508bce49f181e64917bb3a4e049b758bc3fe` (candidate supplied for the current `main` baseline)
- End commit: pending; will be recorded in the local A0 Git commit
- Date: 2026-09-13
- Executor: Codex

## 2. Scope

### Allowed changes

- Read-only inspection of acquisition, corpus, manifest, and Chroma state
- SQLite open and `PRAGMA integrity_check`
- Required metadata/state backup under `backups/20260913-110353/`
- Baseline and execution-report artifacts under `docs/`
- Offline regression and manifest dry-run checks

### Forbidden changes

- Downloader, resolver, worker, route, retry, or schema changes
- Status or metadata mutation
- New downloads or network requests
- Index, chunking, retrieval, evaluator, or Gold Standard changes

## 3. Files Changed

| File | Change | Reason | Behavior impact |
|---|---|---|---|
| `docs/baseline/acquisition-baseline.md` | Added | Human-readable frozen baseline | Documentation only |
| `docs/baseline/acquisition-baseline.json` | Added | Machine-readable metrics, hashes, and verification record | Documentation only |
| `docs/reviews/stage-A0-execution-report.md` | Added | Stage handoff and audit record | Documentation only |
| `backups/20260913-110353/*` | Added five verified copies | Restore point for required state files | No runtime impact |

The capture did not modify `tasks.sqlite`, `索引信息.csv`, `run_history.csv`, `config.json`, `index/manifest.json`, the downloader, or the vector store.

## 4. Database Changes

- Schema before: existing `tasks` and `run_history` tables
- Schema after: unchanged
- Migration: none
- Rollback: restore the verified `tasks.sqlite` copy from `backups/20260913-110353/tasks.sqlite` after stopping writers

`tasks.sqlite` opened successfully and `PRAGMA integrity_check` returned `ok`. PMID uniqueness was confirmed: 9,171 total rows and 9,171 distinct PMIDs.

## 5. Tests

| Command | Exit status | Result |
|---|---:|---|
| `$env:PYTHONIOENCODING='utf-8'; & .venv\Scripts\python tests\test_pipeline.py` | 0 | 3 tests passed |
| `$env:PYTHONIOENCODING='utf-8'; & .venv\Scripts\python index\reconcile_manifest.py --dry-run` | 0 | Chroma 2,492 sources / 72,344 chunks; manifest 2,499 entries / 72,344 chunks; no write |

## 6. Metrics

This is the first frozen capture, so `Before` is not applicable and `Delta` records the baseline establishment rather than a data change.

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| Total PMID | N/A | 9,171 | Baseline established |
| Distinct PMID | N/A | 9,171 | Baseline established |
| DOI coverage | N/A | 7,906 / 9,171 (86.21%) | Baseline established |
| PMCID coverage | N/A | 3,001 / 9,171 (32.72%) | Baseline established |
| Valid PDF | N/A | 2,421 / 2,422 | Baseline established |
| Converted Markdown | N/A | 2,499 | Baseline established |
| Indexed documents | N/A | 2,492 | Baseline established |
| Chroma chunks | N/A | 72,344 | Baseline established |
| Manifest–Chroma chunk delta | N/A | 0 | Baseline established |
| Backup hash equality | N/A | Pass | Baseline established |

## 7. Known Issues

- The local workspace is not a Git worktree, so the supplied base commit cannot be verified locally and an independent end commit cannot be created here.
- `直肠癌文献爬取/pdfs_merged/PMID_31567929.pdf` fails the current PDF validation rule; it was not modified or redownloaded.
- The manifest contains seven zero-chunk entries while Chroma contains 2,492 non-zero sources; chunk totals still match exactly.
- The public-facing source/state scan found 6,634 local absolute-path lines and one email-like configuration field; it found no credential values or private endpoints under the scan scope.
- Existing acquisition-state semantics and retrieval/cache/evaluator issues remain unchanged and are outside A0.

## 8. Out of Scope

- A1 and all later acquisition stages
- Downloader/status/schema/error-taxonomy changes
- Index preflight, cache isolation, chunking, BM25, hybrid retrieval, reranking, and benchmark changes
- Gold Standard creation or modification
- Removal, repair, or redownload of the invalid PDF

## 9. Handoff

STATUS: PAUSED FOR INDEPENDENT REVIEW

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED
