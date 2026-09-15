# Stage A2 执行报告：Metadata UPSERT 与 PubMed Canonical Metadata

## 基线与交接状态

```text
CURRENT_PHASE: Phase A
CURRENT_STAGE: A2
BASE_COMMIT: 2fb5dc3da42ef390acd8773d15bde69805eaf334
END_COMMIT: 6d980e9fae71c3938db1bc4d8ba74562b611f5b3
START_TIME: 2026-09-15T14:56:38+08:00
EXECUTION_DATE: 2026-09-15
REVIEW_STATUS: A1 ACCEPT；A2 待独立审查
BLOCKING_FINDINGS: none
NEXT_ALLOWED_STAGE: A3（仅在 A2 APPROVED 或 APPROVED WITH NON-BLOCKING NOTES 后）
```

执行者为本次 A2 最终叶节点 Worker。A1 已由独立 Reviewer 判定 ACCEPT；本次未创建或调用其他 session、agent、subagent 或 delegated executor。

## 范围

本 Stage 仅实现 A2 合同：以 PubMed XML 为 canonical metadata，解析结构化字段，按允许字段执行非空 UPSERT，保留旧可信值和 A1 业务/provenance 字段，并提供 PMID 单个/列表、discovery batch 和显式全库 refresh 入口。

未执行生产数据库的大规模网络 refresh，未运行旧 NBIB queue 链路；所有行为测试使用临时 SQLite 和离线 XML fixture/mock fetcher。

## 实际文件与行为影响

本次 A2 commit 包含以下四个文件：

- `直肠癌文献爬取/scripts/pubmed_metadata.py`：PubMed XML parser、结构化作者与 family normalization、Article ID/日期解析、官方 EFetch client、受限 canonical metadata UPSERT、批处理 refresh、失败保留和模式选择。允许写字段集中在 `ALLOWED_METADATA_FIELDS`；数据库更新按批次事务执行，数据库异常回滚。
- `直肠癌文献爬取/scripts/refresh_pubmed_metadata.py`：CLI 入口，注册 `--pmid`、`--input`、`--batch-id` 和显式 `--all`。
- `tests/fixtures/pubmed_a2_sample.xml`：包含完整字段样本和缺失 DOI/PMCID 的部分 metadata 样本。
- `tests/test_a2_metadata.py`：A2 parser、normalization、UPSERT、new PMID、batch、失败保留、业务字段/provenance 保护、事务回滚和 CLI 离线回归。

本次未修改 A1 schema、生产 `tasks.sqlite`、fetch history、discovery provenance、向量索引或任何 A3+ 文件。

## Allowed / Forbidden

Allowed scope：PubMed XML canonical metadata；DOI、PMCID、Title、Authors、First Author、First Author Family、Journal、ISSN、Publication Date、Year、Publication Type、Language 和 Article IDs 解析；允许字段非空 UPSERT；single/list/batch/full refresh API/CLI；临时失败诊断和保留。

Forbidden scope：A3 完整状态机和 error taxonomy、OA resolver、JATS/TXT/PDF 获取、全文下载、content validation、bibliographic match、normalization、indexing、lease/heartbeat、retry orchestration，以及旧 NBIB queue 链路。

## 数据库、迁移与回滚

- A2 复用 A1 `migrate_to_v2` 和 Schema v2，不新增或修改 schema 列。
- canonical 更新只写入 `doi`、`pmcid`、`title`、`title_norm`、`year`、`journal`、`issn`、`authors`、`first_author`、`first_author_family`、`pub_type`、`language`、`metadata_updated_at`、`updated_at`。
- canonical XML 的空值不会清除旧 DOI、PMCID、title、authors、journal、ISSN、publication date 或 language；A2 不会修改 `status`、attempt/content/lease/retry 字段。
- fetch 失败或 XML 解析失败按批次返回诊断，不执行任务状态转移、重试调度或 metadata 写入。
- 每个成功 fetch batch 使用 SQLite 事务；测试中的强制更新异常验证了整批 rollback。
- 如需回滚代码，使用 `git revert 6d980e9fae71c3938db1bc4d8ba74562b611f5b3`；本次未对生产数据库写入，因此没有生产数据回滚动作。

## 测试命令、退出状态与结果

以下命令均在 `E:\writing-rag` 执行，并使用 `PYTHONIOENCODING=utf-8`：

| 命令 | 退出状态 | 结果 |
|---|---:|---|
| `PYTHONIOENCODING=utf-8 .venv\Scripts\python tests\test_a2_metadata.py` | 0 | A2 专项 9/9 通过 |
| `PYTHONIOENCODING=utf-8 .venv\Scripts\python tests\test_a1_schema.py` | 0 | A1 回归 6/6 通过 |
| `PYTHONIOENCODING=utf-8 .venv\Scripts\python tests\test_pipeline.py` | 0 | pipeline 回归 3/3 通过 |
| `PYTHONIOENCODING=utf-8 .venv\Scripts\python -m unittest discover -s tests -p test*.py` | 0 | 全部离线测试 18/18 通过 |
| `PYTHONIOENCODING=utf-8 .venv\Scripts\python 直肠癌文献爬取\scripts\refresh_pubmed_metadata.py --help` | 0 | CLI 四种选择模式均已注册 |

## Before / After / Delta

| 项目 | Before | After | Delta |
|---|---|---|---|
| A2 实现 | 已有未完成草稿，支持核心 parser/refresh 结构 | 保留原结构并补齐 `pmc`/`pmcid` Article ID 兼容、CLI 离线注入点与验证 | 仅 A2 行为补强 |
| A2 专项测试 | 7/7 通过 | 9/9 通过 | +2 个覆盖：完整业务/provenance 保护、CLI 五种实际调用形态 |
| 必要回归 | A1/pipeline 本次前未由本执行报告确认 | A1 6/6、pipeline 3/3、全量离线 18/18 | 无回归失败 |
| 生产数据库/索引 | 未纳入本次测试范围 | 仍未写入、未刷新、未重建 | 0 个生产数据变更 |

## Known Issues

- PubMed client 保持一次官方 EFetch 请求语义；A3/A5 负责完整失败分类、retry orchestration 和后续获取策略。
- `publication_date` 与 `article_ids` 是 parser 输出；A1 Schema v2 当前允许写字段没有对应独立 task 列，因此不会被额外写入数据库。
- CLI 的真实网络 fetch 使用 NCBI 官方 EFetch；离线测试通过 `run_cli(..., fetcher=...)` 注入 fixture，不会把测试数据或 token 写入项目。
- `--all`、`--batch-id` 的 PMID 选择依赖 A1 `tasks`、`task_discoveries` 和 `discovery_batches` 的现有结构；A2 不负责创建并行 workflow database。

## Out of Scope

A3 状态机与完整错误 taxonomy、A4 OA resolver、A5 正文获取与统一 retry client、A6 内容验证与 bibliographic match、A7 normalization、A8 lease/heartbeat/batch、A9 reporting/acquisition 总验收，以及 Phase B/C/D 的 indexing、retrieval、generation 和发布治理均未实现或启动。

## Acceptance criterion evidence mapping

| ID | 证据 |
|---|---|
| A2-AC01 | `pubmed_metadata.py:_pmids_for_mode()` 和 `run_cli()`（约 508、545 行）；`test_cli_executes_single_list_input_batch_and_full_modes_offline`（`tests/test_a2_metadata.py:293`）覆盖 `--pmid` 单个/列表、`--input`、`--batch-id`、`--all`。 |
| A2-AC02 | `parse_pubmed_xml()`（约 343 行）和 fixture；`test_xml_parser_reads_structured_metadata_and_article_ids`（`tests/test_a2_metadata.py:45`）核验 DOI、PMCID、Title、Authors、First Author、Journal、ISSN、Publication Date、Year、Publication Type、Language 和 Article IDs。 |
| A2-AC03 | `ALLOWED_METADATA_FIELDS`（约 32 行）与 `_metadata_value_is_non_empty()`；`test_upsert_nonempty_and_protects_empty_existing_metadata`（`tests/test_a2_metadata.py:84`）核验非空更新和空值保留。 |
| A2-AC04 | A1 `ingest_pmids()` 产生 pending task 后由 `refresh_pubmed_metadata()` 补全；同一测试核验 metadata 写入且 `status` 不变，`test_new_pmid_batch_scope_and_duplicate_refresh`（`tests/test_a2_metadata.py:135`）覆盖新增 PMID。 |
| A2-AC05 | `_upsert_metadata()` 仅生成允许字段 UPDATE；`test_all_business_and_provenance_fields_are_unchanged`（`tests/test_a2_metadata.py:190`）逐字段核验业务字段，并核验 `fetch_attempts`、`source_candidates`、`discovery_batches`、`task_discoveries` 不变。 |
| A2-AC06 | `_pmids_for_mode(batch_id=...)` 使用 `task_discoveries` 的 `DISTINCT pmid`；`test_new_pmid_batch_scope_and_duplicate_refresh` 核验 batch-b 只请求 `[1002, 1003]`。 |
| A2-AC07 | `normalize_author_family()`（约 146 行）优先使用结构化 LastName/ForeName/Initials，并支持 `Smith AB`、`Smith, Andrew B`、`Andrew B Smith`；`test_family_normalization_structured_and_fallback_forms`（`tests/test_a2_metadata.py:78`）核验统一为 `smith`。 |
| A2-AC08 | `refresh_pubmed_metadata()` 对 fetch/parse 失败只返回诊断、不写库；`test_temporary_failure_preserves_task_and_metadata`（`tests/test_a2_metadata.py:165`）、重复 refresh 断言和 `test_batch_upsert_rolls_back_on_database_failure`（`tests/test_a2_metadata.py:268`）核验失败保留、幂等保护和事务回滚。 |

## 独立审查交接

No subsequent stage has been started.

Requested reviewer decision:

- APPROVED
- APPROVED WITH NON-BLOCKING NOTES
- NEEDS REVISION
- REJECTED

STATUS: PAUSED FOR INDEPENDENT REVIEW
