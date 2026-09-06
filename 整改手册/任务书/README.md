# Rectal Cancer RAG 整改任务书索引

本目录将综合整改手册拆分为可独立执行的 Stage 任务。执行顺序为：

| 阶段 | 任务范围 | 前置条件 |
|---|---|---|
| Phase A | A0–A9：文献获取与语料治理 | 从 A0 开始 |
| Phase B | B0–B6：索引与检索基础设施 | Phase A 总审查通过 |
| Phase C | C1–C5：文献注册、质量与高级排序 | B6 及 Phase B 总审查通过 |
| Phase D | D1–D4：证据生成、端到端评估与发布治理 | C5 及 Phase C 总审查通过 |

## 单个任务的执行规则

1. 记录 BASE_COMMIT、STAGE、START_TIME。
2. 只修改该任务书列出的范围；不提前实现后续 Stage。
3. 为行为变化补充测试，并记录具体命令、退出状态和结果。
4. 生成 docs/reviews/stage-<stage>-execution-report.md。
5. 创建独立 commit 后停止，等待外部审查。
6. 只有 APPROVED 或 APPROVED WITH NON-BLOCKING NOTES 才能领取下一任务。

审查期间不得继续开发。若审查结果为 NEEDS REVISION，只修当前任务、补测试、更新报告、重新提交并再次审查。

## 任务书清单

### Phase A

- A0：当前状态冻结与完整备份
- A1：SQLite 权威源与 Schema v2
- A2：Metadata UPSERT 与 PubMed Canonical Metadata
- A3：状态机与错误分类
- A4：OA Resolver
- A5：正文获取与统一 Retry Client
- A6：内容验证与 Bibliographic Match
- A7：JATS/TXT/PDF 统一 Normalization
- A8：Lease、Heartbeat 与 Batch
- A9：Reporting 与 Acquisition 总验收

### Phase B

- B0：RAG Baseline 冻结
- B1：Index Integrity Preflight
- B2：正式 Gold Retrieval Benchmark
- B3：Passage Representation v2
- B4：Hybrid Retrieval
- B5：Document-Level Index 与文献覆盖
- B6：Semantic Reranker

### Phase C

- C1：Paper Registry
- C2：文献质量 Metadata
- C3：Corpus Citation Graph
- C4：Evidence-Aware Ranking
- C5：Query Expansion / Decomposition

### Phase D

- D1：Evidence Pack
- D2：Answer Generation 与 Citation Grounding
- D3：End-to-End Evaluation
- D4：CI、Branch Protection 与发布治理

## 总体优先级

Metadata before content。

Content correctness before retrieval optimization。

Evaluation before complexity。

Independent review before progression。

