# STAGE A3 EXECUTION REPORT

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。
> 本报告记录 A3 的实际执行证据；状态机、错误分类和跨阶段边界以 canonical 任务书为准。

## 1. Stage Information

~~~text
Stage: A3
Phase: Phase A
Depends on: A2 ACCEPT_WITH_FINDINGS（依赖满足）
Base commit: fac2182a6b877466c1376c445d16a5788b44e9a9
Remediation commit: f82e66399c1fad693c3fd54fe283593959b880c1
End commit: f82e66399c1fad693c3fd54fe283593959b880c1
END_COMMIT: f82e66399c1fad693c3fd54fe283593959b880c1
Date: 2026-09-15
Executor: Independent final A3 Worker（Luna / XHigh）
Review status: PAUSED FOR INDEPENDENT REVIEW
Blocking findings addressed: source-level 2xx was previously sufficient for fulltext_ready; dual validation is now mandatory.
Next allowed stage: A4, only after independent review approval
~~~

## 2. Scope

### Allowed changes

- `直肠癌文献爬取/scripts/state_machine.py`: A3 canonical document state machine, source-level error taxonomy, HTTP classification, retry-policy metadata, attempt recording and document-level status derivation.
- `tests/test_a3_state_machine.py`: A3 专项离线回归测试。
- `docs/reviews/stage-A3-execution-report.md`: 本阶段执行证据报告。

### Forbidden changes

- 不实现 A4 OA source discovery、A5 正文获取/统一 Retry Client、A6 content acceptance 或后续 Stage。
- 不修改生产 SQLite，不创建并行 workflow database，不执行生产数据库迁移或索引变更。
- 不修改任务书、综合手册或其他无关工作流文件。

工作区中预先存在的 `整改手册/` 任务书改动未被暂存或提交，仍保持原状。

## 3. Remediation

- `fulltext_ready` 的显式状态转换现在要求 `file_valid` 与 `bibliographic_match` 同时为真；缺失或任一为假都会被拒绝。
- source attempt 的成功判定现在同样要求这两项显式证据。HTTP 2xx（包括 200）只能记录 source request，不能单独推导 `fulltext_ready`。
- `record_fetch_attempt` 接收调用方提供的两项验证结果，并仅将其作为本次文献级推导的前置输入；A3 不实现验证算法。A1 Schema v2 保持不变，未对生产数据库执行迁移。
- 旧 `done` 状态没有双验证证据时保守映射为 `metadata_only`，不再被视为已验收正文。

## 4. Files Changed

| File | Change | Behavior impact |
|---|---|---|
| `直肠癌文献爬取/scripts/state_machine.py` | 增加双验证前置条件，接入状态转换、attempt success 判定和文献级 derivation；保持既有 taxonomy、HTTP mapping、attempt 事务逻辑 | 无双验证的 2xx 只能得到 `metadata_only`/`pending`；双验证均真才可进入 `fulltext_ready` |
| `tests/test_a3_state_machine.py` | 保留原有 A3 required tests；新增无双验证、单项验证、双验证和直接转换门禁测试 | 覆盖 Reviewer 阻塞证据及 AC01–AC04 所需行为 |
| `docs/reviews/stage-A3-execution-report.md` | 更新 remediation、测试退出状态、AC 证据和交接状态 | 报告只描述当前 A3 状态 |

## 5. Database or Index Changes

生产 SQLite、索引和语料：N/A。所有专项测试都在 `tempfile.TemporaryDirectory()` 中创建临时 SQLite；未打开、写入或迁移项目生产 SQLite，未修改 Chroma、manifest 或其他索引产物。

A1 Schema v2 的 `fetch_attempts` 表保持原样。由于该表没有验证字段，当前调用提供的 `file_valid` 与 `bibliographic_match` 只用于本次原子推导；数据库中未标注的历史/临时成功 attempt 默认不具备 fulltext 验证证据，因此后续 derivation 不会用它晋级。

原始 `fetch_attempts` 不被状态推导删除；`record_fetch_attempt` 在同一事务中追加 attempt、读取全部 attempts 并更新 tasks 状态，失败时回滚。

## 6. Tests

| Command | Exit status | Result |
|---|---:|---|
| `& 'E:\writing-rag\.venv\Scripts\python.exe' 'E:\writing-rag\tests\test_a3_state_machine.py'` | 0 | PASS；13 tests，13 passed |
| `& 'E:\writing-rag\.venv\Scripts\python.exe' 'E:\writing-rag\tests\test_pipeline.py'` | 0 | PASS；3 tests，3 passed |
| `& 'E:\writing-rag\.venv\Scripts\python.exe' -m unittest discover -s 'E:\writing-rag\tests' -p 'test_*.py'` | 0 | PASS；全量离线 31 tests，31 passed |
| `git diff --check` | 0 | PASS；无 whitespace error |

测试均为离线执行；未访问网络，未执行 A4 或任何后续 Stage。

专项关键证据：

- `tests/test_a3_state_machine.py:127-183`：HTTP 200 缺失/单项验证不得晋级，双验证均真才晋级。
- `tests/test_a3_state_machine.py:185-202`：直接 `transition_task_status` 缺双验证拒绝，双验证均真允许。
- `tests/test_a3_state_machine.py:64-98,204-288`：A3 原有 HTTP/error/attempt/mixed-source/taxonomy 覆盖保留。

## 7. Acceptance Criteria

| Criterion ID | Result | Evidence |
|---|---|---|
| A3-AC01 | PASS | 404 仍分类为 source-level `source_not_found`，有 metadata 的单一 404 推导为 `metadata_only`；无双验证的 200 不会成为文献级 `fulltext_ready`。见 `state_machine.py:194-224,342-410` 与 `test_a3_state_machine.py:79-92,127-183`。 |
| A3-AC02 | PASS | `CANONICAL_STATES`、`ERROR_CLASSES`、`HTTP_ERROR_MAPPING`、`RETRY_POLICIES` 保持 machine-readable；503/429/timeout 覆盖见 `state_machine.py:21-145,194-224` 与 `test_a3_state_machine.py:64-98,271-288`。 |
| A3-AC03 | PASS | 成功和失败 source request 均执行 `INSERT INTO fetch_attempts`；成功 attempt（带双验证）和失败 attempt 测试见 `test_a3_state_machine.py:100-125,204-227`，实现见 `state_machine.py:540-684`。 |
| A3-AC04 | PASS | `derive_document_status` 与 `record_fetch_attempt` 均基于同一 PMID 的全部 attempts 综合推导；source-level 404/503 与已验证成功的 mixed-source 覆盖见 `test_a3_state_machine.py:229-269`，数据库读取入口见 `state_machine.py:511-533`。 |

## 8. Metrics

| Metric | Before remediation | After remediation | Delta |
|---|---:|---:|---:|
| HTTP 200 without dual validation can enter `fulltext_ready` | yes | no | fixed |
| Direct fulltext transition without dual validation | allowed | not permitted | fixed |
| A3专项离线测试 | 10/10 | 13/13 | +3 tests |
| 全量离线 discovery | 28/28（既有证据） | 31/31 | +3 tests；全通过 |
| 生产 SQLite / index changes | 0 | 0 | 0 |

## 9. Known Issues

- A2 的非阻塞 findings 按 RUN DELTA 保留，未在 A3 中扩展处理。
- A3 只消费调用方的 `file_valid` 与 `bibliographic_match` 结果，不实现 A6 内容验证或 bibliographic match 算法。
- A3 不发起网络请求、不发现 OA source、不下载正文、不实现 A5 retry client。

## 10. Out of Scope

- A4 OA source priority/discovery。
- A5 统一 Retry Client、正文获取和下载流程。
- A6 内容验证与 Bibliographic Match。
- 生产数据修复、SQLite schema migration、并行 workflow database、RAG/Chroma/manifest 变更。
- 任何 A4 或后续单元的执行。

## 11. Handoff

本单元已停止在独立审查前；未启动任何后续 Stage。

~~~text
STATUS: PAUSED FOR INDEPENDENT REVIEW

No subsequent stage has been started.

Requested reviewer decision:

APPROVED
APPROVED WITH NON-BLOCKING NOTES
NEEDS REVISION
REJECTED
~~~
