# STAGE A3 EXECUTION REPORT

> Governing protocol: [00-总控与执行报告.md](../../整改手册/任务书/00-总控与执行报告.md#全局阶段执行协议)。
> 本报告记录 A3 的实际执行证据；状态机、错误分类和跨阶段边界以 canonical 任务书为准。

## 1. Stage Information

~~~text
Stage: A3
Phase: Phase A
Depends on: A2 ACCEPT_WITH_FINDINGS（依赖满足）
Stage base commit: db494fad785ce53f91d5a33893652525ae22ef86
Previous rejected handoff: 8bab5a87b0d1718f08111329f7e51f30d0cb81fc
Prior A3 remediation commit: f82e66399c1fad693c3fd54fe283593959b880c1
Remediation commit: c65f1231e28eaeaf410c20d3ab3d89e14145d9f5
Remediation: 2
End commit: HEAD（最终报告交接提交）
END_COMMIT: HEAD
Post-commit verification: remediation 2 提交后执行 `git rev-parse --verify HEAD`；实际解析结果在最终交接中记录，本报告使用 `HEAD` 符号引用以避免自引用哈希。
Date: 2026-09-15
Executor: Independent final A3 Worker（Luna / XHigh）
Review status: PAUSED FOR INDEPENDENT REVIEW
Blocking findings addressed: source-level 2xx was previously sufficient for fulltext_ready；现已同时修复 self-transition 无 flags 和 current fulltext 无验证证据的保持/推导绕过。
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

## 3. Remediation 2：持久化与显式转换收口

- 保持既有双验证合同：所有进入或保持 `fulltext_ready` 的显式转换都要求 `file_valid=True` 与 `bibliographic_match=True`；self-transition 也受同一门禁约束。
- `derive_document_status` 不再无条件信任当前 `fulltext_ready`。无 attempts 时按 metadata 可用性降为 `metadata_only` 或 `pending`；仅 HTTP 200 且无双验证时降为 `metadata_only`。
- 允许当前 `fulltext_ready` 在缺少验证证据时落到推导出的非全文状态，以便保守降级真正写回 tasks；双验证成功的 attempt 仍可推导为 `fulltext_ready`。
- source attempt 的成功判定继续要求两项显式证据。HTTP 2xx（包括 200）只能记录 source request，不能单独推导 `fulltext_ready`。
- `record_fetch_attempt` 在成功 source request 且两项验证均真时，把现有 A1 `fetch_attempts.outcome` 持久化为 `fulltext_ready`；重开 SQLite 后该 marker 仍可被状态推导识别。A3 不实现验证算法，A1 Schema v2 保持不变。
- `transition_task_status(..., "fulltext_ready")` 必须找到同一 PMID 的持久化双验证 attempt；caller flags 不能单独作为写入依据。
- 旧 `done` 状态和未带持久化 marker 的历史成功 attempt 没有双验证证据时保守映射为 `metadata_only`，不再被视为已验收正文。

## 4. Files Changed

| File | Change | Behavior impact |
|---|---|---|
| `直肠癌文献爬取/scripts/state_machine.py` | 收紧 fulltext_ready self-transition 门禁；移除 current fulltext 的无条件 derivation 短路；将双验证成功持久化到既有 outcome 字段；要求显式 fulltext 转换具备持久化 qualifying attempt | 无双验证的 current fulltext 只能得到 `metadata_only`/`pending` 或其他推导出的非全文状态；双验证 marker 可跨 SQLite 重启保持 `fulltext_ready`；caller flags 不能绕过 |
| `tests/test_a3_state_machine.py` | 保留原有 A3 required tests；新增 self-transition/current fulltext 门禁、SQLite round-trip 和 caller flags 不能作为 proof 的回归测试 | 覆盖两个 Reviewer 阻塞绕过、持久化不变量及 AC01–AC04 所需行为 |
| `docs/reviews/stage-A3-execution-report.md` | 更新 remediation 2、测试退出状态、AC 证据和交接状态 | 报告记录本次 A3-only remediation 的最终状态 |

## 5. Database or Index Changes

生产 SQLite、索引和语料：N/A。所有专项测试都在 `tempfile.TemporaryDirectory()` 中创建临时 SQLite；未打开、写入或迁移项目生产 SQLite，未修改 Chroma、manifest 或其他索引产物。

A1 Schema v2 的 `fetch_attempts` 表保持原样。由于该表没有验证字段，A3 使用既有 `outcome` 字段持久化 `fulltext_ready` marker；该 marker 只由成功 source request 且 `file_valid=True`、`bibliographic_match=True` 时的 `record_fetch_attempt` 写入。数据库中未标注的历史/临时成功 attempt 默认不具备 fulltext 验证证据，因此后续 derivation 不会用它晋级。

因此，current `fulltext_ready` 在无 attempts 或只有未标注 HTTP 200 attempt 时会保守推导为非全文状态；已持久化 marker 的 attempt 在关闭并重新打开 SQLite 后仍可推导为 `fulltext_ready`。A3 只消费调用方的验证结果，不新增 A6 内容验证逻辑。

`transition_task_status` 对 `fulltext_ready` 先读取同一 PMID 的全部 `fetch_attempts` 并要求 qualifying attempt，再执行状态转换；无 marker 时即使 caller 同时传入两个 true flags 也会拒绝。

原始 `fetch_attempts` 不被状态推导删除；`record_fetch_attempt` 在同一事务中追加 attempt、读取全部 attempts 并更新 tasks 状态，失败时回滚。

## 6. Tests

| Command | Exit status | Result |
|---|---:|---|
| `$env:PYTHONIOENCODING = 'utf-8'; & .venv\Scripts\python.exe tests\test_a3_state_machine.py` | 0 | PASS；19 tests，19 passed |
| `$env:PYTHONIOENCODING = 'utf-8'; & .venv\Scripts\python.exe tests\test_pipeline.py` | 0 | PASS；3 tests，3 passed |
| `$env:PYTHONIOENCODING = 'utf-8'; & .venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'` | 0 | PASS；全量离线 37 tests，37 passed |
| `git diff --check` | 0 | PASS；无 whitespace error |

测试均为离线执行；未访问网络，未执行 A4 或任何后续 Stage。

专项关键证据：

- `test_fulltext_self_transition_requires_dual_validation`：纯状态机 self-transition 仍要求双验证。
- `test_current_fulltext_without_attempts_degrades_to_metadata_only`、`test_current_fulltext_http_200_without_validation_degrades_to_metadata_only`：current `fulltext_ready` 无 qualifying evidence 时降级。
- `test_dual_validation_round_trips_through_sqlite`：持久化 marker 在关闭写连接、修改 current status 并重新 derivation 后仍得到 `fulltext_ready`。
- `test_direct_fulltext_transition_cannot_use_caller_flags_as_proof`：显式 DB transition 无 qualifying attempt 时拒绝 caller flags。
- A3 原有 HTTP/error/attempt、mixed-source 和 taxonomy 覆盖全部保留。

## 7. Acceptance Criteria

| Criterion ID | Result | Evidence |
|---|---|---|
| A3-AC01 | PASS | 404 仍分类为 source-level `source_not_found`，有 metadata 的单一 404 推导为 `metadata_only`；current fulltext 缺 qualifying evidence 时不会保持 ready。证据：`classify_http_status`、`derive_document_status`、`test_404_is_source_level_not_found`、current fulltext degradation tests。 |
| A3-AC02 | PASS | `CANONICAL_STATES`、`ERROR_CLASSES`、`HTTP_ERROR_MAPPING`、`RETRY_POLICIES` 保持 machine-readable；503/429/timeout 覆盖见 `test_503_is_retryable`、`test_429_uses_retry_policy`、`test_timeout_is_retryable`。 |
| A3-AC03 | PASS | 成功和失败 source request 均执行 `INSERT INTO fetch_attempts`；`test_success_fetch_attempt_is_written_and_promotes_status`、`test_failure_fetch_attempt_is_written_and_marks_retryable_error` 覆盖写入，且成功 attempt 持久化 `fulltext_ready` marker。 |
| A3-AC04 | PASS | `derive_document_status`、`derive_task_status` 与 `record_fetch_attempt` 均基于同一 PMID 的全部 attempts 综合推导；`test_dual_validation_round_trips_through_sqlite` 和 mixed-source tests 覆盖跨连接持久化与 source-level 混合结果。 |

## 8. Metrics

| Metric | Before remediation | After remediation | Delta |
|---|---:|---:|---:|
| `fulltext_ready` self-transition without dual validation | allowed | not permitted | fixed |
| Current `fulltext_ready` without attempts | retained | `metadata_only`/`pending` | fixed |
| Current `fulltext_ready` with HTTP 200 without dual validation | retained | `metadata_only` | fixed |
| Current `fulltext_ready` with dual validation | retained only in-memory | retained across SQLite reopen | fixed |
| A3专项离线测试 | 17/17 | 19/19 | +2 tests |
| 全量离线 discovery | 35/35 | 37/37 | +2 tests；全通过 |
| 生产 SQLite / index changes | 0 | 0 | 0 |

## 9. Known Issues

- A2 的非阻塞 findings 按 RUN DELTA 保留，未在 A3 中扩展处理。
- A3 只消费调用方的 `file_valid` 与 `bibliographic_match` 结果，不实现 A6 内容验证或 bibliographic match 算法；A1 Schema v2 未增加验证字段，A3 以既有 `outcome=fulltext_ready` marker 持久化 qualifying 语义，未标注 attempt 仍按未验证处理。
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
