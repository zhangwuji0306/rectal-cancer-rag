# Acquisition Baseline

## Freeze state

| Field | Value |
|---|---|
| CURRENT_PHASE | A |
| CURRENT_STAGE | A0 |
| REVIEW_STATUS | NOT_STARTED |
| BASE_COMMIT | `b2ce508bce49f181e64917bb3a4e049b758bc3fe` |
| Captured at | 2026-09-13 11:04:03 +08:00 |
| Next allowed stage | A1 after independent review approval |

## Acquisition and corpus metrics

| Metric | Value |
|---|---:|
| Total PMID | 9,171 |
| Distinct PMID | 9,171 |
| PMID unique | Yes |
| DOI coverage | 7,906 / 9,171 (86.21%) |
| PMCID coverage | 3,001 / 9,171 (32.72%) |
| Task status: `archived` | 1,582 |
| Task status: `done` | 2,419 |
| Task status: `failed` | 21 |
| Task status: `not_found` | 1,075 |
| Task status: `pending` | 4,074 |
| `pdfs_merged/*.pdf` | 2,422 |
| Valid PDF | 2,421 |
| Converted Markdown | 2,499 |
| Manifest entries | 2,499 |
| Indexed documents | 2,492 |
| Chroma chunks | 72,344 |

The PDF that does not pass the current validation rule is `直肠癌文献爬取/pdfs_merged/PMID_31567929.pdf`.

The manifest contains seven zero-chunk entries; the 2,492 non-zero manifest entries match the 2,492 Chroma sources. Manifest and Chroma chunk totals are both 72,344.

## Database integrity

`直肠癌文献爬取/tasks.sqlite` opened successfully. `PRAGMA integrity_check` returned `ok`. The database contains the existing `tasks` and `run_history` tables, with 9,171 and 5,336 rows respectively. No database, schema, status, or route values were changed during capture.

## Backup

All copies below were verified by SHA256 equality with their source files.

| Source | Backup | Bytes | Logical rows | SHA256 |
|---|---|---:|---:|---|
| `直肠癌文献爬取/tasks.sqlite` | `backups/20260913-110353/tasks.sqlite` | 5,963,776 | tasks 9,171; run_history 5,336 | `bdb61bc23b0f368c33b1bfab950d4938adac5bdb82091d63584788622f353120` |
| `直肠癌文献爬取/索引信息.csv` | `backups/20260913-110353/索引信息.csv` | 19,948,473 | 9,171 | `a575d2006bdd9c9c3071650d113e12a53beb45fe1d52273be403c1f8a0df1c8e` |
| `直肠癌文献爬取/reports/run_history.csv` | `backups/20260913-110353/run_history.csv` | 2,116,654 | 5,336 | `d054a552fb3129598300f9852416625afc36c35a182cae9a84ec9500c335922c` |
| `直肠癌文献爬取/config.json` | `backups/20260913-110353/config.json` | 878 | 26 | `8c6a25a9fbd06d97d2a01fac59c36e7eff0037a9be31e05e617dba4af3579402` |
| `index/manifest.json` | `backups/20260913-110353/manifest.json` | 375,663 | 2,499 | `cf0b848457733c9adf2a9b7bb7d4b3b1239d749d323dc4a7b82e56b213147947` |

## Compliance scan

The scan covered 79 source, configuration, metadata, report, and documentation files, excluding private/full-text corpus directories, model and virtual-environment files, Chroma storage, backups, and local `.claude` state.

- Credential-value pattern matches: 0
- Private endpoint matches: 0
- Email-like fields: 1, in `直肠癌文献爬取/config.json`
- Local absolute-path references: 6,634 lines, primarily in operating documentation and historical run reports

The scan is recorded in `acquisition-baseline.json` with file-level examples and verification flags.
